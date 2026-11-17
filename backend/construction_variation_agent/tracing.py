"""Agent tracing, budgets and stage guards (ADK callbacks).

Records WHAT happened (agent, tool, stage, model, tokens, durations, validation outcomes)
without recording raw customer content or prompts. Tool arguments are reduced to ids and
sizes. The same callbacks enforce per-job budgets and fail the job closed when a stage
ends without validated output - the LLM cannot declare a job complete.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .shared_libraries.constants import (
    STATE_DOCUMENTS,
    STATE_EXTRACT_CURSOR,
    STATE_EXTRACT_DONE,
    STATE_INTAKE,
    STATE_PLAIN_ENGLISH_SUMMARY,
)
from .shared_libraries.runtime import (
    JobRuntime,
    StageIncompleteError,
    runtime_from_state,
)

log = logging.getLogger("cvea.agent_trace")
_ID_KEYS = {
    "document_id",
    "page_ref",
    "stage",
    "document_id_a",
    "document_id_b",
    "first_page",
    "last_page",
}


def safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Keep identifiers; reduce everything else to type and length (no customer content)."""
    return {
        k: (v if k in _ID_KEYS else f"<{type(v).__name__}:{len(str(v))}>")
        for k, v in (args or {}).items()
    }


class TraceRecorder:
    def __init__(
        self,
        request: Any,
        model_id: str | None,
        root_agent: str,
        sink: Callable[[dict[str, Any]], None] | None = None,
        attempt: int = 0,
    ) -> None:
        self.base = {
            "correlation_id": request.correlation_id,
            "job_id": request.job_id,
            "tenant_id": request.tenant_id,
            "case_id": request.case_id,
            "root_agent": root_agent,
            "model": model_id,
            "attempt": attempt,
        }
        self.events: list[dict[str, Any]] = []
        self.sink = sink
        self._starts: dict[str, float] = {}
        self.tokens = {"prompt": 0, "output": 0}

    def record(self, **fields: Any) -> None:
        ev = {**self.base, "ts": datetime.now(UTC).isoformat(), **fields}
        self.events.append(ev)
        log.info(json.dumps(ev, default=str))
        if self.sink:
            self.sink(ev)

    def start(self, key: str) -> None:
        self._starts[key] = time.monotonic()

    def elapsed_ms(self, key: str) -> int:
        return int((time.monotonic() - self._starts.get(key, time.monotonic())) * 1000)

    def summary(self) -> dict[str, Any]:
        return {"events": len(self.events), "tokens": dict(self.tokens)}


def _rt(state: Any) -> JobRuntime:
    return runtime_from_state(state)


def _stage_complete(stage: str, state: Any) -> bool:
    if stage == "intake":
        return bool((state.get(STATE_INTAKE) or {}).get("documents"))
    if stage == "document_extraction":
        routes = {
            d["document_id"]: d["parser_route"]
            for d in (state.get(STATE_INTAKE) or {}).get("documents", [])
        }
        need = [
            d for d in state.get(STATE_DOCUMENTS, [])
            if routes.get(d) in {"extract_text", "vision_required"}
        ]
        done = set(state.get(STATE_EXTRACT_DONE) or [])
        return all(d in done for d in need)
    key = {
        "cross_reference": "cross_reference_result",
        "variation_analysis": "variation_result",
        "review_preparation": "review_prep_result",
        "plain_english_report": STATE_PLAIN_ENGLISH_SUMMARY,
    }[stage]
    return state.get(key) is not None


def stage_callbacks(stage: str) -> dict[str, Callable]:
    """Agent-level callbacks: trace stage timing and enforce stage completion."""

    def before_agent(callback_context):  # type: ignore[no-untyped-def]
        rt = _rt(callback_context.state)
        if rt.tracer:
            rt.tracer.start(f"stage:{stage}")
            rt.tracer.record(kind="stage_start", stage=stage, agent=callback_context.agent_name)
        return None

    def after_agent(callback_context):  # type: ignore[no-untyped-def]
        rt = _rt(callback_context.state)
        ok = _stage_complete(stage, callback_context.state)
        if rt.tracer:
            rt.tracer.record(
                kind="stage_end",
                stage=stage,
                agent=callback_context.agent_name,
                duration_ms=rt.tracer.elapsed_ms(f"stage:{stage}"),
                validation="passed" if ok else "failed",
            )
        if not ok:
            raise StageIncompleteError(f"stage '{stage}' ended without validated output")
        return None

    return {"before_agent_callback": before_agent, "after_agent_callback": after_agent}


def llm_callbacks(stage: str, *, isolate_document: bool = False) -> dict[str, Callable]:
    """Model/tool callbacks: budgets, tracing, optional per-document context isolation."""

    def before_model(callback_context, llm_request):  # type: ignore[no-untyped-def]
        rt = _rt(callback_context.state)
        rt.count_model_call()
        strip_user_attachments(llm_request)
        drop_other_agent_context(llm_request)
        if isolate_document:
            isolate_current_document(llm_request)
            attach_current_image(rt, callback_context.state, llm_request)
        if rt.tracer:
            rt.tracer.start(f"model:{callback_context.agent_name}")
            rt.tracer.record(
                kind="model_call",
                stage=stage,
                agent=callback_context.agent_name,
                n_contents=len(llm_request.contents or []),
            )
        return None

    def after_model(callback_context, llm_response):  # type: ignore[no-untyped-def]
        rt = _rt(callback_context.state)
        usage = getattr(llm_response, "usage_metadata", None)
        p = getattr(usage, "prompt_token_count", 0) or 0
        o = getattr(usage, "candidates_token_count", 0) or 0
        if rt.tracer:
            rt.tracer.tokens["prompt"] += p
            rt.tracer.tokens["output"] += o
            rt.tracer.record(
                kind="model_response",
                stage=stage,
                agent=callback_context.agent_name,
                prompt_tokens=p,
                output_tokens=o,
                duration_ms=rt.tracer.elapsed_ms(f"model:{callback_context.agent_name}"),
                error=getattr(llm_response, "error_code", None),
            )
        return None

    def before_tool(tool, args, tool_context):  # type: ignore[no-untyped-def]
        rt = _rt(tool_context.state)
        rt.count_tool_call()
        if rt.tracer:
            rt.tracer.start(f"tool:{tool.name}")
        return None

    def after_tool(tool, args, tool_context, tool_response):  # type: ignore[no-untyped-def]
        rt = _rt(tool_context.state)
        if rt.tracer:
            status = (tool_response or {}).get("status") or (
                "error" if (tool_response or {}).get("error") else "ok"
            )
            rt.tracer.record(
                kind="tool_call",
                stage=stage,
                agent=tool_context.agent_name,
                tool=tool.name,
                args=safe_args(args),
                result_status=status,
                rejected=len((tool_response or {}).get("rejected") or [])
                if isinstance(tool_response, dict)
                else 0,
                duration_ms=rt.tracer.elapsed_ms(f"tool:{tool.name}"),
            )
        return None

    return {
        "before_model_callback": before_model,
        "after_model_callback": after_model,
        "before_tool_callback": before_tool,
        "after_tool_callback": after_tool,
    }


def strip_user_attachments(llm_request: Any) -> None:
    """Keep uploaded bytes out of prompts; extraction tools attach only the active image."""
    from google.genai import types

    sanitized = []
    for content in llm_request.contents or []:
        if content.role != "user":
            sanitized.append(content)
            continue
        parts = [
            part
            for part in content.parts or []
            if not getattr(part, "inline_data", None) and not getattr(part, "file_data", None)
        ]
        sanitized.append(types.Content(role=content.role, parts=parts))
    llm_request.contents = sanitized


def isolate_current_document(llm_request: Any) -> None:
    """Prune history so each extraction iteration sees ONLY its own document.

    ADK keeps earlier loop iterations in the request contents, so page text from document N
    would otherwise accumulate into document N+1 (unbounded tokens, cross-document bleed).
    The first user message is kept; everything before the current iteration is dropped. The
    previous iteration ends with a model turn that holds no function call.
    """
    contents = list(llm_request.contents or [])
    if len(contents) <= 1:
        return

    def has_call_or_response(c: Any) -> bool:
        return any(
            getattr(p, "function_call", None) or getattr(p, "function_response", None)
            for p in (c.parts or [])
        )

    first_user = next((i for i, c in enumerate(contents) if c.role == "user"), 0)
    last_final = -1
    for i, c in enumerate(contents):
        if i > first_user and c.role == "model" and not has_call_or_response(c):
            last_final = i
    llm_request.contents = (
        [contents[first_user], *contents[last_final + 1 :]] if last_final >= 0 else contents
    )


def attach_current_image(rt: JobRuntime, state: Any, llm_request: Any) -> None:
    """Attach the active image only to its own extraction turn."""
    from google.genai import types

    contents = list(llm_request.contents or [])
    first_user = next((i for i, content in enumerate(contents) if content.role == "user"), None)
    if first_user is None:
        return
    content = contents[first_user]
    parts = [part for part in (content.parts or []) if not getattr(part, "inline_data", None)]
    contents[first_user] = types.Content(role=content.role, parts=parts)
    llm_request.contents = contents

    intake = (state.get(STATE_INTAKE) or {}).get("documents", [])
    routes = {d["document_id"]: d for d in intake}
    queue = [
        d
        for d in state.get(STATE_DOCUMENTS, [])
        if routes.get(d, {}).get("parser_route", "extract_text")
        in {"extract_text", "vision_required"}
    ]
    cursor = int(state.get(STATE_EXTRACT_CURSOR, 0))
    if cursor == 0 or cursor > len(queue):
        return
    document_id = queue[cursor - 1]
    if document_id in set(state.get(STATE_EXTRACT_DONE) or []):
        return
    image = rt.provider.get_document(document_id)
    if not image.image_bytes or not image.mime_type:
        return

    content = contents[first_user]
    parts = list(content.parts or [])
    parts.append(types.Part.from_bytes(data=image.image_bytes, mime_type=image.mime_type))
    contents[first_user] = types.Content(role=content.role, parts=parts)
    llm_request.contents = contents


_CONTEXT_PREFIX = "For context:"


def drop_other_agent_context(llm_request: Any) -> None:
    """Remove other agents' replayed transcripts from the model request.

    ADK re-presents every earlier agent's tool calls/results to later agents as user-role
    "For context:" messages. For this pipeline that would re-send ALL document text (returned by
    the extractor's tools) to every later stage: unbounded tokens and needless exposure of
    untrusted text. Stage inputs reach each agent through the validated state blocks rendered into
    its instruction instead, so these transcripts are dropped. The user's first message and the
    agent's OWN tool calls/results are kept.
    """

    def is_transcript(c: Any) -> bool:
        parts = c.parts or []
        return (
            c.role == "user"
            and bool(parts)
            and all(getattr(p, "text", None) for p in parts)
            and str(parts[0].text).startswith(_CONTEXT_PREFIX)
        )

    llm_request.contents = [c for c in (llm_request.contents or []) if not is_transcript(c)]
