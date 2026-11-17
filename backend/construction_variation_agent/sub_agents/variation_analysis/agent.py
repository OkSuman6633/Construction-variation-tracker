from google.adk.agents import LlmAgent
from google.genai import types

from ...prompt import render_instruction
from ...shared_libraries.constants import GENERATE_TEMPERATURE
from ...tools.case_tools import (
    get_approved_source_references,
    get_case_context,
    get_case_metadata,
    get_existing_timeline_events,
)
from ...tools.reference_tools import find_related_document_identifiers
from ...tools.submission_tools import submit_variation_analysis
from ...tools.validation_tools import (
    check_missing_references,
    check_source_reference,
    check_unsupported_claims,
)
from ...tracing import llm_callbacks, stage_callbacks
from .prompt import VARIATION_DESCRIPTION, VARIATION_PROMPT


def _instruction(ctx) -> str:  # type: ignore[no-untyped-def]
    return render_instruction(
        VARIATION_PROMPT, ctx.state, include=("intake", "events", "cross_reference")
    )


def build_variation_analysis_agent(model) -> LlmAgent:  # type: ignore[no-untyped-def]
    return LlmAgent(
        name="variation_analysis_agent",
        model=model,
        description=VARIATION_DESCRIPTION,
        instruction=_instruction,
        tools=[
            get_case_context,
            get_case_metadata,
            get_approved_source_references,
            get_existing_timeline_events,
            find_related_document_identifiers,
            check_source_reference,
            check_missing_references,
            check_unsupported_claims,
            submit_variation_analysis,
        ],
        generate_content_config=types.GenerateContentConfig(temperature=GENERATE_TEMPERATURE),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        **llm_callbacks("variation_analysis"),
        **stage_callbacks("variation_analysis"),
    )
