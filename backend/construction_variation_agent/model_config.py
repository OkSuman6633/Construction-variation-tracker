"""Gemini model configuration for ADK. Configurable via env; never silently substituted.

GEMINI_API_KEY / GEMINI_MODEL / GOOGLE_GENAI_USE_VERTEXAI come from the backend settings.
The key stays server-side. If the model is not configured, a model that raises a clear
error is returned - the system never falls back to another AI provider.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse


class ModelNotConfiguredError(RuntimeError):
    pass


class UnconfiguredModel(BaseLlm):
    """Raises a clear, non-secret error if an agent is run without GEMINI_MODEL/key."""

    model: str = "unconfigured"
    reason: str = "GEMINI_MODEL is not set"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        raise ModelNotConfiguredError(self.reason)
        yield  # pragma: no cover  (makes this an async generator)


def configure_gemini_environment(api_key: str | None, use_vertex: bool) -> None:
    """Export credentials for the in-process Gemini client only."""
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE" if use_vertex else "FALSE"
    if api_key and not use_vertex:
        os.environ["GOOGLE_API_KEY"] = api_key


def resolve_model(
    model_name: str | None = None, api_key: str | None = None, use_vertex: bool | None = None
) -> Any:
    name = model_name or os.environ.get("GEMINI_MODEL")
    vertex = (
        use_vertex
        if use_vertex is not None
        else os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "FALSE").upper() == "TRUE"
    )
    key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not name:
        return UnconfiguredModel(reason="GEMINI_MODEL is not set")
    if not vertex and not key:
        return UnconfiguredModel(model=name, reason="GEMINI_API_KEY is not set")
    configure_gemini_environment(key, vertex)
    return name  # ADK resolves Gemini model-name strings through its model registry


def check_model_configuration(
    model_name: str | None, api_key: str | None, use_vertex: bool
) -> dict[str, Any]:
    """Offline configuration check (no network). Use verify_model_live for a real API check."""
    problems = []
    if not model_name:
        problems.append("GEMINI_MODEL is not set")
    else:
        from google.adk.models.registry import LLMRegistry

        try:
            LLMRegistry.resolve(model_name)
        except Exception:  # noqa: BLE001
            problems.append(f"model '{model_name}' is not recognised by the installed ADK version")
    if not use_vertex and not api_key:
        problems.append("GEMINI_API_KEY is not set")
    return {"ok": not problems, "problems": problems}


async def verify_model_live(
    model_name: str, api_key: str | None, use_vertex: bool
) -> dict[str, Any]:
    """Network check that the model exists for this credential (metadata call, no generation).

    NOT run in the build sandbox (no Google API access). Run via scripts/check_model.py.
    """
    from google import genai

    configure_gemini_environment(api_key, use_vertex)
    client = genai.Client(vertexai=use_vertex, api_key=None if use_vertex else api_key)
    try:
        info = await client.aio.models.get(model=model_name)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    return {"ok": True, "name": getattr(info, "name", model_name)}
