from google.adk.agents import LlmAgent
from google.genai import types

from ...prompt import render_instruction
from ...shared_libraries.constants import GENERATE_TEMPERATURE
from ...tools.submission_tools import submit_plain_english_summary
from ...tools.validation_tools import check_unsupported_claims
from ...tracing import llm_callbacks, stage_callbacks
from .prompt import REPORT_DESCRIPTION, REPORT_PROMPT


def _instruction(ctx) -> str:  # type: ignore[no-untyped-def]
    return render_instruction(
        REPORT_PROMPT,
        ctx.state,
        include=("events", "cross_reference", "variation", "review_preparation"),
    )


def build_plain_english_report_agent(model) -> LlmAgent:  # type: ignore[no-untyped-def]
    return LlmAgent(
        name="plain_english_report_agent",
        model=model,
        description=REPORT_DESCRIPTION,
        instruction=_instruction,
        tools=[check_unsupported_claims, submit_plain_english_summary],
        generate_content_config=types.GenerateContentConfig(temperature=GENERATE_TEMPERATURE),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        **llm_callbacks("plain_english_report"),
        **stage_callbacks("plain_english_report"),
    )
