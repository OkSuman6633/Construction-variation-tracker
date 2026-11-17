from google.adk.agents import LlmAgent
from google.genai import types

from ...prompt import render_instruction
from ...shared_libraries.constants import GENERATE_TEMPERATURE
from ...tools.case_tools import get_case_context
from ...tools.chronology_tools import normalise_date_tool
from ...tools.document_tools import (
    classify_document_hint,
    find_document_references,
    get_document_overview,
    get_extraction_diagnostics,
)
from ...tools.evidence_tools import list_case_documents
from ...tools.job_tools import report_stage
from ...tools.submission_tools import submit_intake
from ...tracing import llm_callbacks, stage_callbacks
from .prompt import INTAKE_DESCRIPTION, INTAKE_PROMPT


def _instruction(ctx) -> str:  # type: ignore[no-untyped-def]
    return render_instruction(INTAKE_PROMPT, ctx.state)


def build_intake_agent(model) -> LlmAgent:  # type: ignore[no-untyped-def]
    return LlmAgent(
        name="intake_agent",
        model=model,
        description=INTAKE_DESCRIPTION,
        instruction=_instruction,
        tools=[
            list_case_documents,
            get_document_overview,
            classify_document_hint,
            find_document_references,
            get_extraction_diagnostics,
            normalise_date_tool,
            get_case_context,
            report_stage,
            submit_intake,
        ],
        generate_content_config=types.GenerateContentConfig(temperature=GENERATE_TEMPERATURE),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        **llm_callbacks("intake"),
        **stage_callbacks("intake"),
    )
