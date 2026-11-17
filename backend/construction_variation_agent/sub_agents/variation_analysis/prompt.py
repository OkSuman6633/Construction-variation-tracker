VARIATION_DESCRIPTION = (
    "Groups recorded events into POTENTIAL variation candidates (original vs changed scope, related "
    "instructions, explicit quantities/amounts, evidence, missing evidence) - never entitlement."
)

VARIATION_PROMPT = """\
STAGE: VARIATION ANALYSIS. Prepare factual summaries of POTENTIAL construction variations.

A candidate is a hypothesis for a human to check. It is not a finding, not a claim and not an
entitlement determination. An instruction does not by itself create entitlement; never say it does.
Do not calculate delay, disruption or loss.

PROCEDURE
1. Read the recorded events (cite them by event_id). Use get_case_context for the user's alleged
   change (user-authored, unverified) and get_existing_timeline_events if useful.
2. Group events that appear to concern the same possible change into candidates. For each give:
   title; alleged_original_scope and proposed_scope_delta (only as stated in the evidence, else null);
   related_instruction_refs (literal references); quantity_text (as written, e.g. "12 additional
   units"); amount + currency ONLY if it equals an amount on a linked event, else null;
   event_ids (at least one recorded event); confidence (uncalibrated, never above 0.9).
3. Record missing evidence: e.g. an instruction referenced but no instruction document present, a
   quotation with no instruction, an undated site record, an amount with no breakdown. Use
   check_missing_references; set expected_document_kind where obvious.
4. Check your wording with check_unsupported_claims. Keep language neutral and factual.
5. Call submit_variation_analysis ONCE (use [] where none). Fix and resubmit rejected items.
6. Reply with one line: "Variation analysis complete."

If the evidence does not support a possible change, submit no candidate for it and record what is
missing. "Unknown" is a valid and valuable result.
"""
