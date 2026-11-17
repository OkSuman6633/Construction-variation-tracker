"""Scripted 'oracle' LLM for TEST use only.

It replays a profile's golden answers through the REAL ADK runner and tools, so tests exercise
agents, callbacks, tools, session state, loop control and the deterministic engine. It says
nothing about Gemini's extraction quality - that is measured separately on held-out data.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


def fc(name: str, **args: Any) -> list[types.Part]:
    return [types.Part(function_call=types.FunctionCall(name=name, args=args))]


def say(text: str) -> list[types.Part]:
    return [types.Part(text=text)]


def last_response(req: LlmRequest):  # type: ignore[no-untyped-def]
    for p in (req.contents[-1].parts or []) if req.contents else []:
        if p.function_response:
            return p.function_response
    return None


def last_call_arguments(req: LlmRequest, name: str) -> dict[str, Any]:
    for content in reversed(req.contents or []):
        for part in reversed(content.parts or []):
            if part.function_call and part.function_call.name == name:
                return dict(part.function_call.args or {})
    return {}


class ScriptedLlm(BaseLlm):
    model: str = "scripted-oracle"
    profile: dict
    mode: str = "oracle"
    extractor_sizes: list[int] = []
    extractor_images: list[bool] = []
    system_texts: list[str] = []
    content_dump: list[tuple[str, str]] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        sys_text = str(llm_request.config.system_instruction)
        name = re.search(r'internal name is "(\w+)"', sys_text).group(1)  # type: ignore[union-attr]
        self.system_texts.append(sys_text)
        self.content_dump.append((name, str(llm_request.contents)))
        if name == "document_extraction_agent":
            self.extractor_sizes.append(len(llm_request.contents))
            self.extractor_images.append(
                any(
                    part.inline_data
                    for content in llm_request.contents or []
                    for part in content.parts or []
                )
            )
        parts = getattr(self, f"_{name}")(llm_request)
        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100, candidates_token_count=20, total_token_count=120
            ),
        )

    # ------------------------------------------------------------- stages
    def _intake_agent(self, req):  # type: ignore[no-untyped-def]
        fr = last_response(req)
        if fr is None:
            return fc("list_case_documents")
        if fr.name == "list_case_documents":
            if self.mode == "no_submit_intake":
                return say("Intake complete.")  # never submits: stage must fail closed
            docs = [
                {
                    "document_id": d["document_id"],
                    "category": d["category"],
                    "source_type": d.get("origin"),
                    "document_reference": None,
                    "document_date": d.get("recorded_event_date"),
                    "date_basis": "stated_on_document"
                    if d.get("recorded_event_date")
                    else "unknown",
                    "author": None,
                    "parties": [],
                    "revision": None,
                    "missing_metadata": [] if d.get("recorded_event_date") else ["document date"],
                    "parser_route": "extract_text",
                    "issues": [],
                }
                for d in self.profile["documents"]
            ]
            return fc("submit_intake", documents_json=json.dumps(docs))
        return say("Intake complete.")

    def _document_extraction_agent(self, req):  # type: ignore[no-untyped-def]
        fr = last_response(req)
        if self.mode == "runaway":
            return fc(
                "get_document_overview", document_id=self.profile["documents"][0]["document_id"]
            )
        if fr is None:
            if self.mode == "scope_probe":
                return fc(
                    "get_document_pages", document_id="doc-foreign-001", first_page=1, last_page=1
                )
            return fc("next_document_to_extract")
        if fr.name == "get_document_pages" and fr.response.get("error") == "DOCUMENT_NOT_IN_SCOPE":
            return fc("next_document_to_extract")
        if fr.name == "next_document_to_extract":
            if fr.response.get("done"):
                return say("Extraction complete.")
            return fc(
                "get_document_pages",
                document_id=fr.response["document_id"],
                first_page=1,
                last_page=4,
            )
        if fr.name == "get_document_pages":
            did = fr.response["document_id"]
            events = [
                self._event_json(e, i)
                for i, e in enumerate(
                    x for x in self.profile["expected"]["events"] if x["document_id"] == did
                )
            ]
            if self.mode == "invent_amount" and events:
                events.append({**events[0], "amount": 99999.0, "currency": "GBP"})
            if self.mode == "legal_language" and events:
                events[0]["description"] = (
                    "The subcontractor is entitled to payment for this change."
                )
            return fc("submit_document_extraction", document_id=did, events_json=json.dumps(events))
        if fr.name == "submit_document_extraction":
            rejected = (fr.response or {}).get("rejected", [])
            if rejected:
                did = last_call_arguments(req, "submit_document_extraction").get("document_id")
                source_events = [
                    e
                    for e in self.profile["expected"]["events"]
                    if e["document_id"] == did
                ]
                corrected = []
                for issue in rejected:
                    index = issue.get("index", -1)
                    if 0 <= index < len(source_events):
                        event = self._event_json(source_events[index], index)
                        event["quoted_span"] = source_events[index]["quote"]
                        event["description"] = source_events[index]["description"]
                        corrected.append(event)
                return fc(
                    "submit_document_extraction",
                    document_id=did,
                    events_json=json.dumps(corrected),
                )
        return say("DONE")

    def _event_json(self, e: dict, i: int) -> dict:
        quote = e["quote"]
        if self.mode == "hallucinate_quote" and i == 0:
            quote = "This sentence does not appear anywhere in the source document at all."
        return {
            "event_type": e["event_type"],
            "event_date": e["event_date"],
            "description": e["description"],
            "parties": e["parties"],
            "references": e["references"],
            "amount": e["amount"],
            "currency": e["currency"],
            "page_ref": e["page_ref"],
            "quoted_span": quote,
            "visual_region": e.get("visual_region"),
            "confidence": e["confidence"],
            "uncertainties": e["uncertainties"],
        }

    def _cross_reference_agent(self, req):  # type: ignore[no-untyped-def]
        fr = last_response(req)
        if fr is None:
            return fc("suggest_potential_relationships")
        if fr.name == "suggest_potential_relationships":
            rels = [
                {
                    "document_ids": r["documents"],
                    "relation": r["relation"],
                    "shared_references": r["shared_references"],
                    "rationale": r["rationale"],
                    "confidence": r["confidence"],
                }
                for r in self.profile["expected"]["relationships"]
            ]
            if self.mode == "bad_relationship":
                ids = [d["document_id"] for d in self.profile["documents"]][:2]
                rels.append(
                    {
                        "document_ids": ids,
                        "relation": "references",
                        "shared_references": ["ZZ-999"],
                        "rationale": "Similar wording.",
                        "confidence": 0.9,
                    }
                )
            return fc(
                "submit_cross_references",
                relationships_json=json.dumps(rels),
                contradictions_json="[]",
            )
        return say("Cross-reference complete.")

    def _variation_analysis_agent(self, req):  # type: ignore[no-untyped-def]
        fr = last_response(req)
        if fr is None:
            return fc("get_existing_timeline_events")
        if fr.name == "get_existing_timeline_events":
            ids: dict[tuple, str] = {}
            for e in fr.response["events_this_job"]:
                ids.setdefault((e["document_id"], e["event_type"]), e["event_id"])
            exp = self.profile["expected"]
            cands = []
            for c in exp["candidates"]:
                cands.append(
                    {
                        "title": c["title"],
                        "alleged_original_scope": c["alleged_original_scope"],
                        "proposed_scope_delta": c["proposed_scope_delta"],
                        "related_instruction_refs": c["related_instruction_refs"],
                        "quantity_text": c["quantity_text"],
                        "amount": 123456.0 if self.mode == "unsupported_amount" else c["amount"],
                        "currency": c["currency"],
                        "event_ids": [ids[tuple(k)] for k in c["event_keys"] if tuple(k) in ids],
                        "confidence": c["confidence"],
                    }
                )
            miss = [
                {
                    "description": m["description"],
                    "expected_document_kind": m["expected_document_kind"],
                    "related_event_ids": [
                        ids[tuple(k)] for k in m["event_keys"] if tuple(k) in ids
                    ],
                }
                for m in exp["missing_evidence"]
            ]
            return fc(
                "submit_variation_analysis",
                candidates_json=json.dumps(cands),
                missing_evidence_json=json.dumps(miss),
            )
        return say("Variation analysis complete.")

    def _review_preparation_agent(self, req):  # type: ignore[no-untyped-def]
        fr = last_response(req)
        if fr is None:
            return fc("prepare_unresolved_questions")
        if fr.name == "prepare_unresolved_questions":
            qs = []
            for issue in fr.response["issues"][:10]:
                qs.append(
                    {
                        "question": f"[source] Please confirm: {issue['reasons'][0]}",
                        "related_event_ids": issue.get("event_ids", []),
                        "related_candidate_ids": issue.get("candidate_ids", []),
                    }
                )
            return fc("submit_review_preparation", questions_json=json.dumps(qs))
        return say("Review preparation complete.")

    def _plain_english_report_agent(self, req):  # type: ignore[no-untyped-def]
        fr = last_response(req)
        if (
            fr is not None
            and fr.name == "submit_plain_english_summary"
            and (fr.response or {}).get("status") == "accepted"
        ):
            return say("Plain-English summary complete.")
        if fr is not None and fr.name == "check_unsupported_claims":
            summary = {
                "text": (
                    "This report gives a short, plain-English summary. "
                    "Please check the source files before making a decision."
                ),
                "event_ids": [],
                "candidate_ids": [],
            }
            return fc("submit_plain_english_summary", summary_json=json.dumps(summary))
        return fc(
            "check_unsupported_claims",
            text=(
                "This report gives a short, plain-English summary. "
                "Please check the source files before making a decision."
            ),
        )
