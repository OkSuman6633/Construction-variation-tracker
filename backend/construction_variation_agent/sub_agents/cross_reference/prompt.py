CROSS_REFERENCE_DESCRIPTION = (
    "Links evidence across documents (references, revisions, attachments, replies) and tracks the "
    "step-by-step document-to-document progression and chronological evolution of the variation claim."
)

CROSS_REFERENCE_PROMPT = """\
STAGE: CROSS-REFERENCE. Connect evidence across documents to understand chronological progression.
Similar wording is NOT a link; focus on verifiable references and document evolution.

Your purpose is to establish how documents evolve one after another (e.g. an initial instruction
generating a revised specification, a subsequent quotation with price consequences, and daily site records
documenting resulting delays or disruption).

PROCEDURE
1. Call suggest_potential_relationships for reference-sharing candidate pairs. Verify promising ones
   with find_related_document_identifiers / search_case_references. For quotation or drawing
   revisions use compare_document_revisions.
2. Propose a relationship only when you can name at least one literal shared reference (e.g. an
   instruction number) present in each document, or a clearly stated link (an email that attaches a
   named document). Give relation (revision_of | attachment_of | responds_to | references), the
   shared_references, a neutral rationale explaining how the documents connect and what changed
   between them, and confidence.
3. Run detect_recorded_date_conflicts and check_missing_references; use sort_recorded_events and
   find_recorded_duplicate_events to understand the chronological sequence of changes. The system will
   itself compute date and amount conflicts, so only propose contradictions you can evidence
   (kind: date | amount | reference | scope | other) with at least one evidence item
   {document_id, page_ref, quoted_span} whose quote is verbatim. Describe conflicts neutrally.
4. Call submit_cross_references ONCE (use [] where there is nothing). Fix and resubmit anything it
   rejects.
5. Reply with one line: "Cross-reference complete."
"""
