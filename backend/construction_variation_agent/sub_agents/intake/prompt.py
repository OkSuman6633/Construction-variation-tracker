INTAKE_DESCRIPTION = (
    "Classifies each uploaded document (category, source, reference, document date vs upload time, "
    "revision, missing metadata) and routes it to extraction or flags it as unreadable."
)

INTAKE_PROMPT = """\
STAGE: INTAKE. Decide how each document in this job should be processed. You extract no events.

PROCEDURE
1. Call list_case_documents to get every document id.
2. For each document call get_document_overview (and classify_document_hint or
   get_extraction_diagnostics if helpful; diagnostics reveal scans without a text layer). The hint is
   advisory; base your decision on the document's real content.
3. For each document decide:
   - category: instruction | correspondence | site_diary | quotation | cost_breakdown | programme |
     photograph | revised_drawing | contract_record | other
   - source_type and author/parties exactly as shown in the document (else null)
   - document_reference (its own reference number, e.g. an instruction or quotation number) if shown
   - document_date: an ISO date STATED ON THE DOCUMENT (use normalise_date_tool), else null with
     date_basis "unknown". Use "from_user_metadata" only if the user supplied a date and the document
     itself shows none. NEVER use the upload time. If the stated date differs from the user's recorded
     date, add an issue.
   - revision (e.g. "Rev B") if shown
   - missing_metadata: what is absent (date, author, reference...)
   - parser_route: extract_text (readable text), vision_required (image/scan with no usable text),
     skip_unsupported (unreadable/corrupt), skip_irrelevant (clearly unrelated to this case - use
     sparingly and say why in issues)
   - issues: anything the reviewer should know (instruction-like text found, unreadable pages...)
4. Call submit_intake ONCE with a JSON array that covers EVERY document exactly once. If it is
   rejected, fix exactly what it reports and resubmit.
5. Reply with one line: "Intake complete."
"""
