from google.adk.agents import LlmAgent
from google.genai import types

from ...prompt import render_instruction
from ...shared_libraries.constants import GENERATE_TEMPERATURE
from ...tools.chronology_tools import (
    detect_recorded_date_conflicts,
    find_recorded_duplicate_events,
    sort_recorded_events,
)
from ...tools.evidence_tools import get_case_evidence_references, get_text_excerpt
from ...tools.reference_tools import (
    compare_document_revisions,
    find_related_document_identifiers,
    search_case_references,
    suggest_potential_relationships,
)
from ...tools.submission_tools import submit_cross_references
from ...tools.validation_tools import check_missing_references, check_source_reference
from ...tracing import llm_callbacks, stage_callbacks
from .prompt import CROSS_REFERENCE_DESCRIPTION, CROSS_REFERENCE_PROMPT


def _instruction(ctx) -> str:  # type: ignore[no-untyped-def]
    return render_instruction(CROSS_REFERENCE_PROMPT, ctx.state, include=("intake", "events"))


def build_cross_reference_agent(model) -> LlmAgent:  # type: ignore[no-untyped-def]
    return LlmAgent(
        name="cross_reference_agent",
        model=model,
        description=CROSS_REFERENCE_DESCRIPTION,
        instruction=_instruction,
        tools=[
            get_case_evidence_references,
            get_text_excerpt,
            check_source_reference,
            suggest_potential_relationships,
            find_related_document_identifiers,
            search_case_references,
            compare_document_revisions,
            detect_recorded_date_conflicts,
            check_missing_references,
            sort_recorded_events,
            find_recorded_duplicate_events,
            submit_cross_references,
        ],
        generate_content_config=types.GenerateContentConfig(temperature=GENERATE_TEMPERATURE),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        **llm_callbacks("cross_reference"),
        **stage_callbacks("cross_reference"),
    )
