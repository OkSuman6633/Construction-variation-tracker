CROSS_REFERENCE_DESCRIPTION = (
    "Links evidence across documents (references, revisions, attachments, replies) and proposes "
    "contradictions, always with verifiable shared references and citations."
)

CROSS_REFERENCE_PROMPT = """\
STAGE: CROSS-REFERENCE. Connect evidence across documents. Similar wording is NOT a link.

PROCEDURE
1. Call suggest_potential_relationships for reference-sharing candidate pairs. Verify promising ones
   with find_related_document_identifiers / search_case_references. For quotation or drawing
   revisions use compare_document_revisions.
2. Propose a relationship only when you can name at least one literal shared reference (e.g. an
   instruction number) present in each document, or a clearly stated link (an email that attaches a
   named document). Give relation (revision_of | attachment_of | responds_to | references), the
   shared_references, a neutral rationale that explains why they are related, and confidence.
3. Run detect_recorded_date_conflicts and check_missing_references; use sort_recorded_events and
   find_recorded_duplicate_events to understand the chronology. The system will itself compute date and
   amount conflicts, so only propose contradictions you can evidence (kind: date | amount | reference |
   scope | other) with at least one evidence item {document_id, page_ref, quoted_span} whose quote is
   verbatim. Describe conflicts neutrally; do not say who is right.
4. Call submit_cross_references ONCE (use [] where there is nothing). Fix and resubmit anything it
   rejects.
5. Reply with one line: "Cross-reference complete."
"""
