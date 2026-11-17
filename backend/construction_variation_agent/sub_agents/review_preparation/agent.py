from google.adk.agents import LlmAgent
from google.genai import types

from ...prompt import render_instruction
from ...shared_libraries.constants import GENERATE_TEMPERATURE
from ...tools.job_tools import get_job_context, get_processing_diagnostics, report_stage
from ...tools.review_tools import (
    generate_reviewer_checklist,
    get_review_status,
    prepare_unresolved_questions,
    summarise_review_history,
)
from ...tools.submission_tools import submit_review_preparation
from ...tools.validation_tools import check_unsupported_claims
from ...tracing import llm_callbacks, stage_callbacks
from .prompt import REVIEW_DESCRIPTION, REVIEW_PROMPT


def _instruction(ctx) -> str:  # type: ignore[no-untyped-def]
    return render_instruction(
        REVIEW_PROMPT, ctx.state, include=("events", "cross_reference", "variation")
    )


def build_review_preparation_agent(model) -> LlmAgent:  # type: ignore[no-untyped-def]
    return LlmAgent(
        name="review_preparation_agent",
        model=model,
        description=REVIEW_DESCRIPTION,
        instruction=_instruction,
        tools=[
            get_job_context,
            get_processing_diagnostics,
            report_stage,
            prepare_unresolved_questions,
            generate_reviewer_checklist,
            get_review_status,
            summarise_review_history,
            check_unsupported_claims,
            submit_review_preparation,
        ],
        generate_content_config=types.GenerateContentConfig(temperature=GENERATE_TEMPERATURE),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        **llm_callbacks("review_preparation"),
        **stage_callbacks("review_preparation"),
    )
