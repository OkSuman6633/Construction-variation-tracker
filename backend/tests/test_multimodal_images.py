from __future__ import annotations

import asyncio

from google.genai import types

from app.analysis.runner import run_analysis
from construction_variation_agent.shared_libraries.constants import (
    STATE_DOCUMENTS,
    STATE_EXTRACT_CURSOR,
    STATE_EXTRACT_DONE,
    STATE_INTAKE,
)
from construction_variation_agent.shared_libraries.provenance import citation_errors
from construction_variation_agent.shared_libraries.runtime import (
    JobRuntime,
    ParsedDocument,
    ParsedPage,
)
from construction_variation_agent.shared_libraries.schemas import CaseAnalysisRequest
from construction_variation_agent.tools.validation_tools import validate_event_payload
from construction_variation_agent.tracing import attach_current_image
from tests.support.scripted_model import ScriptedLlm


class ImageProvider:
    def __init__(self) -> None:
        self.document = ParsedDocument(
            "doc-001",
            (ParsedPage("image_1", ""),),
            mime_type="image/jpeg",
            image_bytes=b"normalized image",
        )

    def get_document(self, document_id: str) -> ParsedDocument:
        assert document_id == self.document.document_id
        return self.document

    def case_context(self):
        return {
            "title": "Image evidence test",
            "alleged_change": "A visible site change",
            "parties": [],
            "contract": {},
            "key_dates": {},
        }

    def list_documents(self):
        return [
            {
                "document_id": "doc-001",
                "filename": "site-photo.png",
                "mime_type": "image/png",
                "recorded_event_date": None,
                "origin": "test",
                "page_count": 1,
                "parser_status": "parsed",
            }
        ]

    def existing_events(self):
        return []

    def review_summary(self):
        return {"accepted": 0, "edited": 0, "rejected": 0, "corrections": [], "rejected_items": []}


def _runtime() -> JobRuntime:
    request = CaseAnalysisRequest(
        tenant_id="tenant-001",
        case_id="case-001",
        job_id="job-001",
        correlation_id="corr-001",
        document_ids=["doc-001"],
    )
    return JobRuntime(request=request, provider=ImageProvider())


def _event(**overrides):
    event = {
        "event_type": "site_record",
        "event_date": None,
        "description": "A visible site condition is recorded.",
        "page_ref": "image_1",
        "quoted_span": None,
        "visual_region": "lower-right area of the photograph",
        "confidence": 0.9,
    }
    return {**event, **overrides}


def test_image_citation_requires_visual_region():
    doc = _runtime().provider.get_document("doc-001")
    assert not citation_errors(doc, "image_1", None, "lower-right area")
    assert citation_errors(doc, "image_1", None) == [
        "missing visual_region for image evidence"
    ]


def test_visual_extraction_is_reviewable_but_amounts_need_text_support():
    rt = _runtime()
    accepted, rejected = validate_event_payload(rt, "doc-001", [_event()])
    assert not rejected
    assert accepted[0]["quoted_span"] is None
    assert accepted[0]["basis"] == "ai_inferred_relationship"
    assert accepted[0]["confidence"] <= 0.5
    assert accepted[0]["evidence"][0]["visual_region"]

    accepted, rejected = validate_event_payload(
        rt, "doc-001", [_event(amount=125.0, currency="GBP")]
    )
    assert not accepted
    assert any("image-only" in error for error in rejected[0]["errors"])


def test_extractor_request_gets_only_the_active_image():
    rt = _runtime()
    state = {
        STATE_DOCUMENTS: ["doc-001"],
        STATE_EXTRACT_CURSOR: 1,
        STATE_INTAKE: {
            "documents": [{"document_id": "doc-001", "parser_route": "vision_required"}]
        },
    }

    class Request:
        contents = [types.Content(role="user", parts=[types.Part(text="Analyse this case.")])]

    request = Request()
    attach_current_image(rt, state, request)
    inline_parts = [
        part
        for content in request.contents
        for part in content.parts or []
        if part.inline_data
    ]
    assert len(inline_parts) == 1
    assert inline_parts[0].inline_data.data == b"normalized image"
    assert inline_parts[0].inline_data.mime_type == "image/jpeg"

    state[STATE_EXTRACT_DONE] = ["doc-001"]
    attach_current_image(rt, state, request)
    assert not any(
        part.inline_data
        for content in request.contents
        for part in content.parts or []
    )


def test_real_adk_orchestrator_routes_and_extracts_image_with_visual_citation():
    event = {
        "document_id": "doc-001",
        "page_ref": "image_1",
        "quote": None,
        "visual_region": "upper-left annotation",
        "event_type": "site_record",
        "event_date": None,
        "description": "A marked-up site photograph records a visible change.",
        "parties": [],
        "references": [],
        "amount": None,
        "currency": None,
        "confidence": 0.8,
        "uncertainties": [],
    }
    profile = {
        "documents": [
            {
                "document_id": "doc-001",
                "filename": "site-photo.png",
                "mime_type": "image/png",
                "recorded_event_date": None,
                "origin": "test",
                "page_count": 1,
                "parser_status": "parsed",
                "category": "photograph",
            }
        ],
        "expected": {
            "events": [event],
            "relationships": [],
            "candidates": [],
            "missing_evidence": [],
        },
    }
    provider = ImageProvider()
    request = CaseAnalysisRequest(
        tenant_id="tenant-001",
        case_id="case-001",
        job_id="job-002",
        correlation_id="corr-002",
        document_ids=["doc-001"],
    )
    model = ScriptedLlm(
        profile=profile,
        extractor_sizes=[],
        extractor_images=[],
        system_texts=[],
        content_dump=[],
    )

    outcome = asyncio.run(run_analysis(request, provider, model=model))
    assert outcome.ok, outcome.failure
    assert len(outcome.result.events) == 1
    result_event = outcome.result.events[0]
    assert result_event.basis.value == "ai_inferred_relationship"
    assert result_event.evidence[0].visual_region == "upper-left annotation"
    assert True in model.extractor_images
    assert all(event.quoted_span is None for event in outcome.result.events)
