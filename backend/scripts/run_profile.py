"""Run a synthetic profile through the REAL ADK pipeline with the configured Gemini model.

    python -m scripts.run_profile synthetic_case_basic

Needs GEMINI_MODEL + GEMINI_API_KEY. Use only synthetic profiles (AI Studio endpoint; data
residency not assured). Prints a summary; compare against the profile's golden `expected` block.
"""

import asyncio
import json
import sys

from app.analysis.runner import run_analysis
from app.config import get_settings
from construction_variation_agent.model_config import resolve_model
from construction_variation_agent.shared_libraries.profile_provider import (
    ProfileEvidenceProvider,
    load_profile,
)
from construction_variation_agent.shared_libraries.schemas import CaseAnalysisRequest


async def main(name: str) -> int:
    s = get_settings()
    provider = ProfileEvidenceProvider(load_profile(name))
    req = CaseAnalysisRequest(
        tenant_id="tenant-dev",
        case_id="case-dev-001",
        job_id="job-dev-001",
        correlation_id="cli",
        document_ids=provider.document_ids,
    )
    key = s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
    out = await run_analysis(
        req, provider, model=resolve_model(s.gemini_model, key, s.google_genai_use_vertexai)
    )
    if not out.ok:
        print("FAILED:", out.failure)
        return 1
    r = out.result
    print(
        json.dumps(
            {
                "events": len(r.events),
                "candidates": len(r.candidates),
                "contradictions": len(r.contradictions),
                "missing_evidence": len(r.missing_evidence),
                "citation_validity": out.report.citation_validity,
                "dropped": out.report.dropped,
                "tokens": out.trace_summary["tokens"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "synthetic_case_basic"))
    )
