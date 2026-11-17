"""Deterministic evidence engine: verify, normalise and assemble agent output.

Runs AFTER the ADK workflow and has final authority: it re-verifies every citation against the
parsed document, computes date/amount conflicts by rule, removes duplicates, links candidates to
contradictions and missing evidence, rejects legal-conclusion language, and only then builds the
AnalysisResult. Nothing here asks a model for permission.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from construction_variation_agent.shared_libraries.constants import (
    STATE_CROSSREF,
    STATE_EXTRACTIONS,
    STATE_PLAIN_ENGLISH_SUMMARY,
    STATE_REVIEW_PREP,
    STATE_VARIATION,
)
from construction_variation_agent.shared_libraries.provenance import (
    citation_errors,
    legal_conclusions,
)
from construction_variation_agent.shared_libraries.runtime import JobRuntime
from construction_variation_agent.shared_libraries.schemas import (
    AnalysisResult,
    Contradiction,
    DocumentRelationship,
    EvidenceReference,
    MissingEvidence,
    PlainEnglishSummary,
    ReviewerQuestion,
    TimelineEvent,
    VariationCandidate,
)
from construction_variation_agent.shared_libraries.types import EvidenceBasis
from construction_variation_agent.tools.chronology_tools import (
    detect_amount_conflicts,
    detect_date_conflicts,
    find_duplicate_events,
    sort_events,
)
from construction_variation_agent.tools.validation_tools import missing_reference_candidates


class HardValidationError(Exception):
    """Output must not be persisted (e.g. legal conclusion language or out-of-scope reference)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ValidationReport:
    events_submitted: int = 0
    events_citation_valid: int = 0
    dropped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def citation_validity(self) -> float:
        return self.events_citation_valid / self.events_submitted if self.events_submitted else 1.0


def _cid(kind: str, ids: list[str]) -> str:
    return (
        "ctr-"
        + hashlib.sha1(
            f"{kind}:{','.join(sorted(ids))}".encode(), usedforsecurity=False
        ).hexdigest()[:10]
    )


def _scan_text(label: str, text: str | None) -> None:
    if text and (lc := legal_conclusions(text)):
        raise HardValidationError(
            "LEGAL_CONCLUSION_DETECTED", f"{label} contains legal-conclusion language: {lc}"
        )


def assemble_result(
    rt: JobRuntime, state: dict[str, Any], model_id: str | None
) -> tuple[AnalysisResult, ValidationReport]:
    req, provider = rt.request, rt.provider
    report = ValidationReport()
    docs = {d: provider.get_document(d) for d in req.document_ids}
    rec_dates = {d["document_id"]: d.get("recorded_event_date") for d in provider.list_documents()}

    # 1. events: re-verify citations, schema-validate
    raw_events = [e for evs in (state.get(STATE_EXTRACTIONS) or {}).values() for e in evs]
    report.events_submitted = len(raw_events)
    valid: list[dict[str, Any]] = []
    for e in raw_events:
        if e.get("document_id") not in docs:
            raise HardValidationError(
                "SCOPE_VIOLATION", "event references a document outside the job scope"
            )
        if citation_errors(
            docs[e["document_id"]],
            e.get("page_ref"),
            e.get("quoted_span"),
            e.get("visual_region"),
        ):
            report.dropped.append({"id": e["event_id"], "reason": "citation could not be verified"})
            continue
        try:
            TimelineEvent.model_validate(e)
        except Exception:  # noqa: BLE001
            report.dropped.append({"id": e["event_id"], "reason": "schema validation failed"})
            continue
        report.events_citation_valid += 1
        _scan_text(f"event {e['event_id']}", e["description"])
        valid.append(e)

    # 2. duplicates (same type/date/wording): keep first, record the rest
    drop_ids: set[str] = set()
    for group in find_duplicate_events(valid):
        for dup in group[1:]:
            drop_ids.add(dup)
            report.dropped.append({"id": dup, "reason": f"duplicate of {group[0]}"})
    valid = [e for e in valid if e["event_id"] not in drop_ids]
    valid = sort_events(valid)
    by_id = {e["event_id"]: e for e in valid}

    # 3. deterministic contradictions
    contradictions: dict[str, Contradiction] = {}
    for cf in [*detect_date_conflicts(valid, rec_dates), *detect_amount_conflicts(valid)]:
        ids = [i for i in cf["event_ids"] if i in by_id]
        ev = [EvidenceReference.model_validate(by_id[i]["evidence"][0]) for i in ids]
        cid = _cid(cf["kind"], ids)
        contradictions[cid] = Contradiction(
            contradiction_id=cid,
            kind=cf["kind"],
            description=cf["description"],
            evidence=ev,
            detected_by="deterministic_rule",
        )
        for i in ids:
            note = f"involved in a {cf['kind']} conflict ({cid})"
            by_id[i]["basis"] = EvidenceBasis.CONTRADICTED.value
            if note not in by_id[i]["uncertainties"]:
                by_id[i]["uncertainties"].append(note)
    conflict_events: dict[str, list[str]] = {}
    for cid, con in contradictions.items():
        for ref in con.evidence:
            for e in valid:
                if e["document_id"] == ref.document_id and cid in "".join(e["uncertainties"]):
                    conflict_events.setdefault(cid, []).append(e["event_id"])

    # agent-proposed contradictions (citations re-verified)
    xref = state.get(STATE_CROSSREF) or {}
    for c in xref.get("contradictions", []):
        ev = []
        for e in c["evidence"]:
            if e["document_id"] in docs and not citation_errors(
                docs[e["document_id"]],
                e["page_ref"],
                e.get("quoted_span"),
                e.get("visual_region"),
            ):
                ev.append(
                    EvidenceReference(
                        document_id=e["document_id"],
                        page_ref=e["page_ref"],
                        quoted_span=e.get("quoted_span"),
                        visual_region=e.get("visual_region"),
                    )
                )
        if not ev:
            report.dropped.append({"id": c["contradiction_id"], "reason": "no verifiable evidence"})
            continue
        _scan_text("contradiction", c["description"])
        contradictions[c["contradiction_id"]] = Contradiction(
            contradiction_id=c["contradiction_id"],
            kind=c["kind"],
            description=c["description"],
            evidence=ev,
            detected_by="agent",
        )

    # 4. relationships
    relationships = []
    for r in xref.get("relationships", []):
        if not set(r["document_ids"]) <= set(docs):
            raise HardValidationError(
                "SCOPE_VIOLATION", "relationship references a document outside scope"
            )
        rationale = r["rationale"] + (" [" + "; ".join(r["flags"]) + "]" if r.get("flags") else "")
        _scan_text("relationship", rationale)
        relationships.append(
            DocumentRelationship(
                relationship_id=r["relationship_id"],
                document_ids=r["document_ids"],
                relation=r["relation"],
                shared_references=r["shared_references"],
                rationale=rationale[:5000],
                basis=EvidenceBasis.AI_INFERRED,
                confidence=r["confidence"],
            )
        )

    # 5. missing evidence: agent-submitted + deterministic reference rule
    var = state.get(STATE_VARIATION) or {}
    missing: list[MissingEvidence] = []
    for m in var.get("missing_evidence", []):
        _scan_text("missing evidence", m["description"])
        missing.append(
            MissingEvidence(
                missing_id=m["missing_id"],
                description=m["description"],
                expected_document_kind=m.get("expected_document_kind"),
                related_event_ids=[i for i in m.get("related_event_ids", []) if i in by_id],
            )
        )
    for n, mc in enumerate(missing_reference_candidates(rt), 1):
        text = f"Reference {mc['reference']} is mentioned in {', '.join(mc['mentioned_in'])} but no document with that reference as its subject is in the evidence."
        if not any(mc["reference"].casefold() in m.description.casefold() for m in missing):
            missing.append(MissingEvidence(missing_id=f"miss-auto-{n:04d}", description=text))

    # 6. candidates: must keep at least one verified event; link contradictions
    candidates = []
    for c in var.get("candidates", []):
        ev_ids = [i for i in c["event_ids"] if i in by_id]
        if not ev_ids:
            report.dropped.append(
                {"id": c["candidate_id"], "reason": "no remaining verified events"}
            )
            continue
        for f in ("title", "alleged_original_scope", "proposed_scope_delta", "quantity_text"):
            _scan_text(f"candidate {f}", c.get(f))
        linked = sorted(cid for cid, evs in conflict_events.items() if set(evs) & set(ev_ids))
        amounts = {(by_id[i].get("amount"), by_id[i].get("currency")) for i in ev_ids}
        amount, currency = c.get("amount"), c.get("currency")
        if amount is not None and (amount, currency) not in amounts:
            amount, currency = None, None
            report.warnings.append(f"{c['candidate_id']}: unsupported amount removed")
        candidates.append(
            VariationCandidate(
                candidate_id=c["candidate_id"],
                title=c["title"],
                alleged_original_scope=c.get("alleged_original_scope"),
                proposed_scope_delta=c.get("proposed_scope_delta"),
                related_instruction_refs=c["related_instruction_refs"],
                quantity_text=c.get("quantity_text"),
                amount=amount,
                currency=currency,
                event_ids=ev_ids,
                evidence=[
                    EvidenceReference.model_validate(r)
                    for i in ev_ids
                    for r in by_id[i]["evidence"]
                ],
                contradictions=linked,
                missing_evidence=[
                    m.missing_id for m in missing if set(m.related_event_ids) & set(ev_ids)
                ],
                basis=EvidenceBasis.CONTRADICTED if linked else EvidenceBasis.AI_INFERRED,
                confidence=c["confidence"],
            )
        )

    # 7. reviewer questions: agent-submitted + one automatic question per contradiction
    questions = []
    for q in (state.get(STATE_REVIEW_PREP) or {}).get("questions", []):
        _scan_text("reviewer question", q["question"])
        questions.append(
            ReviewerQuestion(
                question_id=q["question_id"],
                question=q["question"],
                related_event_ids=[i for i in q["related_event_ids"] if i in by_id],
                related_candidate_ids=[
                    i
                    for i in q["related_candidate_ids"]
                    if any(c.candidate_id == i for c in candidates)
                ],
            )
        )
    for n, (cid, con) in enumerate(sorted(contradictions.items()), 1):
        questions.append(
            ReviewerQuestion(
                question_id=f"qst-auto-{n:04d}",
                question=f"[{con.kind}] Please resolve this conflict against the source documents: {con.description}",
                related_event_ids=conflict_events.get(cid, []),
            )
        )

    plain_summary = None
    raw_summary = state.get(STATE_PLAIN_ENGLISH_SUMMARY)
    if raw_summary is not None:
        candidate_summary = PlainEnglishSummary.model_validate(raw_summary)
        if set(candidate_summary.event_ids) <= set(by_id) and set(
            candidate_summary.candidate_ids
        ) <= {candidate.candidate_id for candidate in candidates}:
            _scan_text("plain-English summary", candidate_summary.text)
            plain_summary = candidate_summary
        else:
            report.warnings.append("plain-English summary omitted because a cited item was not kept")

    result = AnalysisResult(
        job_id=req.job_id,
        case_id=req.case_id,
        model_id=model_id,
        events=[TimelineEvent.model_validate(e) for e in valid],
        candidates=candidates,
        relationships=relationships,
        contradictions=sorted(contradictions.values(), key=lambda c: c.contradiction_id),
        missing_evidence=missing,
        reviewer_questions=questions,
        plain_english_summary=plain_summary,
    )
    return result, report
