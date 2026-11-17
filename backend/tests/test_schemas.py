import pytest
from pydantic import ValidationError

from construction_variation_agent.shared_libraries.schemas import (
    EvidenceReference,
    PlainEnglishSummary,
    ReviewDecision,
    TimelineEvent,
    VariationCandidate,
)
from construction_variation_agent.shared_libraries.types import EventType, ReviewStatus


def test_provenance_required():
    with pytest.raises(ValidationError):
        EvidenceReference()
    with pytest.raises(ValidationError):
        EvidenceReference(document_id="doc-001")  # no page or offset
    assert EvidenceReference(document_id="doc-001", page_ref="page_3").page_ref == "page_3"
    assert EvidenceReference(user_authored=True).user_authored


def test_amount_requires_currency_and_null_amount_allowed():
    ok = TimelineEvent(event_id="evt-001", event_type=EventType.OTHER, description="x")
    assert ok.amount is None
    with pytest.raises(ValidationError):
        TimelineEvent(event_id="evt-002", event_type=EventType.OTHER, description="x", amount=5)
    with pytest.raises(ValidationError):
        TimelineEvent(
            event_id="evt-002",
            event_type=EventType.OTHER,
            description="x",
            amount=5,
            currency="pounds",
        )


def test_confidence_bounded():
    with pytest.raises(ValidationError):
        TimelineEvent(
            event_id="evt-001", event_type=EventType.OTHER, description="x", confidence=1.4
        )


def test_plain_english_summary_is_limited_to_seventy_words():
    assert len(PlainEnglishSummary(text="Please check the source files.").text.split()) == 5
    with pytest.raises(ValidationError, match="70 words or fewer"):
        PlainEnglishSummary(text="word " * 71)


def test_entitlement_can_never_be_asserted():
    with pytest.raises(ValidationError):
        VariationCandidate(candidate_id="cand-001", title="t", entitlement_asserted=True)


def test_reject_and_edit_need_reason():
    base = dict(
        review_id="rev-001",
        target_type="event",
        target_id="evt-001",
        reviewer_id="u",
        based_on_revision=1,
    )
    with pytest.raises(ValidationError):
        ReviewDecision(decision=ReviewStatus.REJECTED, **base)
    assert ReviewDecision(decision=ReviewStatus.REJECTED, reason="weak source", **base)


def test_extra_fields_forbidden_and_no_professional_approval_state():
    with pytest.raises(ValidationError):
        TimelineEvent(
            event_id="evt-001", event_type=EventType.OTHER, description="x", legal_conclusion="y"
        )
    assert "professionally_approved" not in {s.value for s in ReviewStatus}
