from google.adk.agents import LlmAgent, LoopAgent
from google.genai import types

from ...prompt import render_instruction
from ...shared_libraries.constants import GENERATE_TEMPERATURE
from ...tools.chronology_tools import normalise_date_tool
from ...tools.document_tools import find_document_references, get_document_overview
from ...tools.evidence_tools import get_document_page_refs, get_document_pages, get_text_excerpt
from ...tools.submission_tools import next_document_to_extract, submit_document_extraction
from ...tools.validation_tools import (
    check_date_consistency,
    check_source_reference,
    validate_extraction_schema,
)
from ...tracing import llm_callbacks, stage_callbacks
from .prompt import EXTRACTION_DESCRIPTION, EXTRACTION_PROMPT


def _instruction(ctx) -> str:  # type: ignore[no-untyped-def]
    return render_instruction(EXTRACTION_PROMPT, ctx.state)


def build_document_extraction_stage(model, max_documents: int = 200) -> LoopAgent:  # type: ignore[no-untyped-def]
    """A bounded ADK LoopAgent: one extraction turn per document, context isolated per document."""
    extractor = LlmAgent(
        name="document_extraction_agent",
        model=model,
        description=EXTRACTION_DESCRIPTION,
        instruction=_instruction,
        tools=[
            next_document_to_extract,
            get_document_pages,
            get_document_page_refs,
            get_text_excerpt,
            get_document_overview,
            find_document_references,
            normalise_date_tool,
            check_source_reference,
            check_date_consistency,
            validate_extraction_schema,
            submit_document_extraction,
        ],
        generate_content_config=types.GenerateContentConfig(temperature=GENERATE_TEMPERATURE),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        **llm_callbacks("document_extraction", isolate_document=True),
    )
    return LoopAgent(
        name="document_extraction_loop",
        description="Runs extraction once per document.",
        sub_agents=[extractor],
        max_iterations=max_documents + 1,
        **stage_callbacks("document_extraction"),
    )
