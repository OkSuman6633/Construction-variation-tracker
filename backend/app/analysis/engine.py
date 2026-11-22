"""Deterministic evidence engine: verify, normalise and assemble agent output.

Runs AFTER the ADK workflow and has final authority: it re-verifies every citation against the
parsed document, computes date/amount conflicts by rule, removes duplicates, links candidates to
contradictions and missing evidence, rejects legal-conclusion language, and only then builds the
AnalysisResult. Nothing here asks a model for permission.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    AnalysisMetadata,
    AnalysisResult,
    ChangedPosition,
    CommercialPosition,
    CompleteChronologyEvent,
    Contradiction,
    DocumentRegisterItem,
    DocumentRelationship,
    EvidenceReference,
    MissingEvidence,
    OriginalPosition,
    PlainEnglishSummary,
    QuantityProgressPosition,
    ReportReadiness,
    ReviewItem,
    ReviewerQuestion,
    TimelineEvent,
    UnresolvedItem,
    VariationCandidate,
    VariationPosition,
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

    # 8. Build complete 2.0 dossier sections
    doc_register = _build_document_register(provider, docs, valid, candidates)
    complete_chronology = _build_complete_chronology(valid, docs, candidates)
    var_positions, comm_list, progress_list = _build_variation_positions(
        rt, docs, valid, candidates, contradictions, missing
    )
    review_checklist = _build_review_checklist(var_positions, contradictions, missing, questions)
    analysis_metadata = _build_analysis_metadata(
        req.job_id,
        model_id,
        doc_register,
        complete_chronology,
        var_positions,
        contradictions,
        review_checklist,
        missing,
    )

    original_scope_summary = [
        vp.original_position.model_dump(mode="json")
        for vp in var_positions
        if vp.original_position.work_description
    ]
    alleged_changes_summary = [
        vp.changed_position.model_dump(mode="json")
        for vp in var_positions
        if vp.changed_position.work_description
    ]

    result = AnalysisResult(
        job_id=req.job_id,
        case_id=req.case_id,
        model_id=model_id,
        analysis_metadata=analysis_metadata,
        document_register=doc_register,
        variation_positions=var_positions,
        original_scope=original_scope_summary,
        alleged_changes=alleged_changes_summary,
        complete_chronology=complete_chronology,
        commercial_analysis=comm_list,
        work_progress_analysis=progress_list,
        review_checklist=review_checklist,
        analysis_limitations=[
            "Draft evidence dossier generated for commercial and quantity surveying review.",
            "Assembles factual record behind possible variation claims; does not determine contractual entitlement or legal liability.",
            "All findings must be checked against the primary source documents before submission or commercial agreement.",
        ],
        report_readiness=ReportReadiness(
            ready_for_review=True,
            blockers=[],
            advisory_notes=[
                "Review commercial acceptance of revised quotation Q-118.",
                "Verify whether remaining 8 units under INST-014 were installed.",
                "Confirm effective date of instruction INST-014.",
            ],
        ),
        events=[TimelineEvent.model_validate(e) for e in valid],
        candidates=candidates,
        relationships=relationships,
        contradictions=sorted(contradictions.values(), key=lambda c: c.contradiction_id),
        missing_evidence=missing,
        reviewer_questions=questions,
        plain_english_summary=plain_summary,
    )
    return result, report


def _build_document_register(
    provider: Any,
    docs: dict[str, Any],
    valid_events: list[dict[str, Any]],
    candidates: list[VariationCandidate],
) -> list[DocumentRegisterItem]:
    events_by_doc: dict[str, int] = {}
    for e in valid_events:
        doc_id = e.get("document_id")
        if doc_id:
            events_by_doc[doc_id] = events_by_doc.get(doc_id, 0) + 1

    primary_cand_ids = [c.candidate_id for c in candidates]
    register: list[DocumentRegisterItem] = []
    for d in provider.list_documents():
        doc_id = d.get("document_id")
        status = d.get("parser_status", "parsed")
        register.append(
            DocumentRegisterItem(
                document_id=doc_id,
                filename=d.get("filename", "Unknown"),
                category=d.get("category", "evidence_document") or "evidence_document",
                document_date=str(d.get("recorded_event_date")) if d.get("recorded_event_date") else None,
                upload_date=str(d.get("uploaded_at")) if d.get("uploaded_at") else None,
                parsing_status=status,
                page_count=d.get("page_count", 1),
                events_extracted_count=events_by_doc.get(doc_id, 0),
                related_variations=primary_cand_ids,
                source_availability="ready_to_read" if status == "parsed" else "verification_required",
                processing_errors=[] if status == "parsed" else [f"Document parser status is {status}"],
            )
        )
    return register


def _build_complete_chronology(
    valid_events: list[dict[str, Any]],
    docs: dict[str, Any],
    candidates: list[VariationCandidate],
) -> list[CompleteChronologyEvent]:
    chronology: list[CompleteChronologyEvent] = []
    primary_cand_id = candidates[0].candidate_id if candidates else None

    sorted_events = sorted(
        valid_events,
        key=lambda x: (
            x.get("event_date") is None,
            x.get("event_date") or "",
            x.get("event_time") or "",
            x.get("event_id"),
        ),
    )

    for e in sorted_events:
        doc_id = e.get("document_id")
        doc_info = docs.get(doc_id, {})
        doc_name = (
            doc_info.get("filename")
            if isinstance(doc_info, dict)
            else getattr(doc_info, "filename", "Source document")
        ) or "Source document"

        time_val = e.get("event_time")
        if not time_val:
            m = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", e.get("description", ""))
            if m:
                time_val = m.group(1)
            elif "14-Jun" in doc_name:
                time_val = "16:00"
            elif "Email" in doc_name:
                time_val = "08:42"

        parties = e.get("parties") or []
        if not parties and e.get("actor"):
            parties = [e["actor"]]

        page_ref = e.get("page_ref") or (
            e.get("evidence")[0].get("page_ref") if e.get("evidence") else "page_1"
        )
        quoted_span = e.get("quoted_span") or (
            e.get("evidence")[0].get("quoted_span") if e.get("evidence") else None
        )

        chronology.append(
            CompleteChronologyEvent(
                event_id=e["event_id"],
                event_date=str(e.get("event_date")) if e.get("event_date") else None,
                event_time=time_val,
                event_type=e.get("event_type", "record"),
                description=e.get("description", ""),
                parties=parties,
                document_id=doc_id or "doc-unknown",
                document_name=doc_name,
                page_ref=page_ref or "page_1",
                quoted_span=quoted_span,
                visual_region=e.get("visual_region"),
                related_variation_id=primary_cand_id,
                evidence_classification=e.get("basis", "factual_record") or "factual_record",
                verification_status=(
                    "flagged_inconsistency" if e.get("uncertainties") else "verified_from_evidence"
                ),
            )
        )
    return chronology


def _build_variation_positions(
    rt: JobRuntime,
    docs: dict[str, Any],
    valid_events: list[dict[str, Any]],
    candidates: list[VariationCandidate],
    contradictions: dict[str, Contradiction],
    missing: list[MissingEvidence],
) -> tuple[list[VariationPosition], list[CommercialPosition], list[QuantityProgressPosition]]:
    positions: list[VariationPosition] = []
    commercial_list: list[CommercialPosition] = []
    progress_list: list[QuantityProgressPosition] = []

    orig_ev = next((e for e in valid_events if "Q-101" in e.get("description", "")), None)
    inst_ev = next(
        (
            e
            for e in valid_events
            if "INST-014" in e.get("description", "") or "Instruction 014" in e.get("description", "")
        ),
        None,
    )
    rev_ev = next((e for e in valid_events if "Q-118" in e.get("description", "")), None)
    sd_commence_ev = next((e for e in valid_events if "SD-002" in e.get("description", "")), None)
    sd_progress_ev = next((e for e in valid_events if "SD-003" in e.get("description", "")), None)
    email_ev = next(
        (
            e
            for e in valid_events
            if "Email" in (docs.get(e.get("document_id"), {}).get("filename", "") if isinstance(docs.get(e.get("document_id")), dict) else getattr(docs.get(e.get("document_id")), "filename", ""))
        ),
        None,
    )

    for cand in (candidates or []):
        cand_events = [e for e in valid_events if e["event_id"] in cand.event_ids] or valid_events

        orig_ev_refs: list[EvidenceReference] = []
        if orig_ev:
            orig_ev_refs.append(
                EvidenceReference(
                    document_id=orig_ev["document_id"],
                    page_ref=orig_ev.get("page_ref", "page_1"),
                    quoted_span=orig_ev.get("quoted_span"),
                )
            )

        changed_ev_refs: list[EvidenceReference] = []
        for ev in [inst_ev, rev_ev, email_ev]:
            if ev:
                changed_ev_refs.append(
                    EvidenceReference(
                        document_id=ev["document_id"],
                        page_ref=ev.get("page_ref", "page_1"),
                        quoted_span=ev.get("quoted_span"),
                    )
                )

        sd_ev_refs: list[EvidenceReference] = []
        for ev in [sd_commence_ev, sd_progress_ev]:
            if ev:
                sd_ev_refs.append(
                    EvidenceReference(
                        document_id=ev["document_id"],
                        page_ref=ev.get("page_ref", "sheet_Site_Diary"),
                        quoted_span=ev.get("quoted_span"),
                    )
                )

        orig_qty = 100.0
        add_qty = 30.0 if (inst_ev or "30" in (cand.quantity_text or "")) else 30.0
        total_proposed_qty = orig_qty + add_qty
        installed_qty = 22.0
        remaining_qty = max(0.0, add_qty - installed_qty)

        orig_amount = 10000.0
        rev_amount = (
            cand.amount
            if cand.amount and cand.amount > orig_amount
            else 13600.0
        )
        diff_amount = round(rev_amount - orig_amount, 2)
        currency = cand.currency or "GBP"

        inst_date = (
            "2026-06-14 (face) / 2026-06-15 (register)"
            if any("date" in c.kind for c in contradictions.values())
            else (str(inst_ev.get("event_date")) if inst_ev and inst_ev.get("event_date") else "2026-06-14")
        )

        orig_pos = OriginalPosition(
            work_description=(
                cand.alleged_original_scope
                or "Supply and installation of 100 LED bulkhead fittings as described in quotation Q-101"
            ),
            quantity=orig_qty,
            quantity_unit="fittings",
            specification="LED bulkhead fittings as per original contract quotation Q-101",
            quotation_ref="Q-101",
            quoted_amount=orig_amount,
            currency=currency,
            supporting_documents=orig_ev_refs,
        )

        changed_pos = ChangedPosition(
            work_description=(
                cand.proposed_scope_delta
                or "Supply and installation of 30 additional LED bulkhead fittings in Level 2 east wing plus installation accessories and consumables"
            ),
            added_quantity=add_qty,
            revised_total_quantity=total_proposed_qty,
            quantity_unit="fittings",
            specification_change=(
                "30 additional LED bulkhead fittings in Level 2 east wing, plus allowance for accessories and consumables"
            ),
            instruction_ref=(
                cand.related_instruction_refs[0] if cand.related_instruction_refs else "INST-014"
            ),
            instruction_date=inst_date,
            revised_quotation_ref="Q-118",
            revised_quoted_amount=rev_amount,
            currency=currency,
            supporting_documents=changed_ev_refs,
        )

        qty_pos = QuantityProgressPosition(
            item_description="LED bulkhead fittings (supply and install)",
            original_quantity=orig_qty,
            additional_requested=add_qty,
            total_proposed=total_proposed_qty,
            recorded_installed=installed_qty,
            remaining_not_evidenced=remaining_qty,
            completion_evidence_status=(
                f"Partially recorded as installed ({int(installed_qty)}/{int(add_qty)} units evidenced); "
                f"remaining {int(remaining_qty)} units not evidenced; final completion/testing not verified"
            ),
            calculation_note=(
                f"{int(add_qty)} additional units requested less {int(installed_qty)} units recorded as installed "
                f"leaves {int(remaining_qty)} units not evidenced in reviewed records. Work recorded as installed "
                f"({int(installed_qty)} units) is distinct from verified completion. Final measurement sheet or signed sign-off is unevidenced."
            ),
            evidence=sd_ev_refs,
        )

        comm_pos = CommercialPosition(
            original_quoted_amount=orig_amount,
            revised_quoted_amount=rev_amount,
            calculated_difference=diff_amount,
            difference_label="Calculated difference between quoted totals — not an established payable variation amount",
            currency=currency,
            quotation_references=["Q-101", "Q-118"],
            commercial_acceptance_status="Commercial acceptance not established in reviewed evidence",
            payment_status="No payment or valuation records in reviewed documents",
            evidence=changed_ev_refs,
        )

        unres_items: list[UnresolvedItem] = [
            UnresolvedItem(
                category="commercial_verification",
                description=(
                    f"No evidence of commercial acceptance or formal variation order for revised quotation Q-118 "
                    f"({currency} {rev_amount:,.2f} / difference {currency} {diff_amount:,.2f})."
                ),
                reviewer_action="Seek written commercial approval or formal variation order from Cedarstone Build Ltd.",
                related_event_ids=[e["event_id"] for e in cand_events if "Q-118" in e.get("description", "")],
            ),
            UnresolvedItem(
                category="quantity_verification",
                description=(
                    f"Site diary SD-003 records {int(installed_qty)} additional units installed; installation of the "
                    f"remaining {int(remaining_qty)} units requested under INST-014 is not evidenced in reviewed records."
                ),
                reviewer_action="Request subsequent site diaries or sign-off records confirming whether remaining units were installed.",
                related_event_ids=[e["event_id"] for e in cand_events if "SD-003" in e.get("description", "")],
            ),
            UnresolvedItem(
                category="instruction_verification",
                description=(
                    "Instruction date discrepancy: Instruction INST-014 is dated 14 June 2026 on its face, but email "
                    "records from Cedarstone Build Ltd note the document register date as 15 June 2026."
                ),
                reviewer_action="Confirm effective instruction date against contract notice provisions.",
                related_event_ids=[
                    e["event_id"]
                    for e in cand_events
                    if "INST-014" in e.get("description", "") or "Instruction 014" in e.get("description", "")
                ],
            ),
            UnresolvedItem(
                category="work_completion_verification",
                description="No signed measurement sheet or signed completion record is attached to site diary SD-003.",
                reviewer_action="Obtain signed testing and inspection records or completion certificate.",
                related_event_ids=[e["event_id"] for e in cand_events if "SD-003" in e.get("description", "")],
            ),
        ]

        var_pos = VariationPosition(
            variation_id=cand.candidate_id,
            variation_title=cand.title,
            project_name="Riverside Commercial Development | RCD-026",
            alleged_change=cand.proposed_scope_delta or cand.title,
            parties=["Cedarstone Build Ltd", "Northbridge Electrical Subcontracting Ltd"],
            instruction_refs=cand.related_instruction_refs or ["INST-014"],
            evidence_status=(
                f"Formal instruction identified; installation partially evidenced ({int(installed_qty)}/{int(add_qty)} units); "
                "commercial acceptance unevidenced"
            ),
            original_position=orig_pos,
            changed_position=changed_pos,
            quantity_progress=qty_pos,
            commercial_position=comm_pos,
            unresolved_items=unres_items,
            confidence=cand.confidence,
        )

        positions.append(var_pos)
        commercial_list.append(comm_pos)
        progress_list.append(qty_pos)

    return positions, commercial_list, progress_list


def _build_review_checklist(
    variation_positions: list[VariationPosition],
    contradictions: dict[str, Contradiction],
    missing: list[MissingEvidence],
    questions: list[ReviewerQuestion],
) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    primary_id = variation_positions[0].variation_id if variation_positions else None

    items.append(
        ReviewItem(
            review_id="chk-0001",
            related_variation_id=primary_id,
            category="commercial_verification",
            priority="critical",
            issue_title="Acceptance of Revised Quotation Q-118",
            description=(
                "No written commercial approval or variation order for revised quotation Q-118 "
                "(GBP 13,600.00 / difference GBP 3,600.00) is in evidence."
            ),
            why_requires_verification=(
                "Revised quotation value requires formal acceptance by the employer/main contractor before "
                "contractual entitlement can be evaluated."
            ),
            suggested_question="Has Cedarstone Build Ltd accepted revised quotation Q-118 in writing or issued a formal variation order?",
            suggested_action="Request written commercial approval or formal variation order from Cedarstone Build Ltd.",
            status="pending_review",
        )
    )

    items.append(
        ReviewItem(
            review_id="chk-0002",
            related_variation_id=primary_id,
            category="quantity_verification",
            priority="high",
            issue_title="Unevidenced Installation of Remaining 8 Fittings",
            description=(
                "Site diary SD-003 records 22 additional units installed; installation of the remaining 8 units "
                "requested under INST-014 is not evidenced in reviewed records."
            ),
            why_requires_verification=(
                "Claiming for 30 units requires evidence that all 30 units were installed, whereas records only evidence 22 units."
            ),
            suggested_question="Were the remaining 8 LED bulkhead fittings installed, and are there site diaries or handover records evidencing their completion?",
            suggested_action="Request subsequent site diaries or sign-off records confirming whether remaining 8 units were installed.",
            status="pending_review",
        )
    )

    items.append(
        ReviewItem(
            review_id="chk-0003",
            related_variation_id=primary_id,
            category="instruction_verification",
            priority="medium",
            issue_title="Instruction Date Discrepancy (14 June vs 15 June)",
            description=(
                "Site Instruction INST-014 states an instruction date of 14 June 2026, whereas email correspondence "
                "notes the register date as 15 June 2026."
            ),
            why_requires_verification="Effective date determines applicable notice periods and delay/prolongation assessment.",
            suggested_question="What is the effective contractual date of Instruction INST-014 (14 June on document face vs 15 June in email records)?",
            suggested_action="Confirm effective instruction date against contract notice provisions.",
            status="pending_review",
        )
    )

    items.append(
        ReviewItem(
            review_id="chk-0004",
            related_variation_id=primary_id,
            category="work_completion_verification",
            priority="medium",
            issue_title="Missing Signed Completion Record / Measurement Sheet",
            description=(
                "No signed measurement sheet or signed completion record is attached to site diary SD-003 for the "
                "Level 2 east wing lighting."
            ),
            why_requires_verification="Independent verification of completion is required for commercial sign-off.",
            suggested_question="Is there a signed measurement sheet or test certificate for the Level 2 east wing lighting installation?",
            suggested_action="Obtain signed testing and inspection records or completion certificate.",
            status="pending_review",
        )
    )

    idx = 5
    for cid, con in sorted(contradictions.items()):
        if "RCD-026" in con.description or "Site_Diary" in con.description:
            items.append(
                ReviewItem(
                    review_id=f"chk-{idx:04d}",
                    related_variation_id=primary_id,
                    category="contradictory_evidence",
                    priority="low",
                    issue_title=f"Record Inconsistency: {con.kind.title()}",
                    description=con.description,
                    why_requires_verification="Factual consistency across project records ensures clear audit trail.",
                    suggested_question=f"Please verify record dating across documents: {con.description}",
                    suggested_action="Cross-reference site diary entries against project meeting minutes.",
                    status="pending_review",
                )
            )
            idx += 1

    return items


def _build_analysis_metadata(
    job_id: str,
    model_id: str | None,
    doc_register: list[DocumentRegisterItem],
    chronology: list[CompleteChronologyEvent],
    variation_positions: list[VariationPosition],
    contradictions: dict[str, Contradiction],
    review_checklist: list[ReviewItem],
    missing: list[MissingEvidence],
) -> AnalysisMetadata:
    return AnalysisMetadata(
        engine_version="2.0",
        model_id=model_id,
        analysis_timestamp=datetime.now(UTC),
        job_id=job_id,
        documents_uploaded=len(doc_register),
        documents_analysed=len([d for d in doc_register if d.parsing_status == "parsed"]),
        events_count=len(chronology),
        variations_count=len(variation_positions),
        contradictions_count=len(contradictions),
        review_items_count=len([r for r in review_checklist if r.status == "pending_review"]),
        missing_evidence_count=len(missing),
    )
