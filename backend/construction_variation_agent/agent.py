"""Root ADK agent: the Construction Variation Coordinator.

A real ADK SequentialAgent orchestrates the six dependent stages in a fixed order. Control
flow is deterministic on purpose: the LLM never chooses which stage runs next, and no stage can
transfer to a peer or parent. Each stage ends only when its validated output exists (see
tracing.stage_callbacks); otherwise the job fails closed.

`root_agent` is retained for ADK tooling and discovery. ADK Web uses a temporary,
attachment-only runtime; the authenticated backend API builds a separate fresh tree per job
with `build_root_agent()` and its storage-backed authorization.

NOTE: SequentialAgent is marked deprecated in ADK 2.13 in favour of Workflow. The version is
pinned; migration is tracked in docs/KNOWN_LIMITATIONS.md.
"""

from __future__ import annotations

import warnings
from typing import Any

from google.adk.agents import BaseAgent, SequentialAgent

from .model_config import resolve_model
from .prompt import COORDINATOR_DESCRIPTION
from .shared_libraries.constants import STAGES
from .sub_agents.cross_reference import build_cross_reference_agent
from .sub_agents.document_extraction import build_document_extraction_stage
from .sub_agents.intake import build_intake_agent
from .sub_agents.plain_english_report import build_plain_english_report_agent
from .sub_agents.review_preparation import build_review_preparation_agent
from .sub_agents.variation_analysis import build_variation_analysis_agent

ROOT_AGENT_NAME = "construction_variation_coordinator"


def build_root_agent(
    model: Any = None, max_documents: int = 200, *, adk_web_sandbox: bool = False
) -> BaseAgent:
    model = model if model is not None else resolve_model()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        agent_type = SequentialAgent
        if adk_web_sandbox:
            from .adk_web_sandbox import AdkWebSandboxAgent

            agent_type = AdkWebSandboxAgent
        return agent_type(
            name=ROOT_AGENT_NAME,
            description=COORDINATOR_DESCRIPTION,
            sub_agents=[
                build_intake_agent(model),
                build_document_extraction_stage(model, max_documents),
                build_cross_reference_agent(model),
                build_variation_analysis_agent(model),
                build_review_preparation_agent(model),
                build_plain_english_report_agent(model),
            ],
        )


assert len(STAGES) == 6
root_agent = build_root_agent(adk_web_sandbox=True)
