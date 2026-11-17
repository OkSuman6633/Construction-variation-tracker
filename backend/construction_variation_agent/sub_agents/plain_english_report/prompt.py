REPORT_DESCRIPTION = (
    "Writes a very short, plain-English overview of the validated case analysis for the visual "
    "two-page report. It does not add findings or make decisions."
)

REPORT_PROMPT = """\
STAGE: PLAIN-ENGLISH REPORT.

Write one short paragraph for a busy person who may not know construction or contract terms.
Use everyday words, short sentences, and no more than 70 words. Explain any unavoidable technical
term. Say what the records appear to show and what still needs checking. Be calm, clear and neutral.

Use only the system-validated events, possible changes and review questions below. Do not add a
fact, date, amount or conclusion. Do not say who is right, who must pay, or whether anyone has a
legal right. A possible change is only something for a person to check.

Include the event_ids and candidate_ids that support the paragraph. Use [] when the paragraph
contains no case-specific fact. Call check_unsupported_claims on the paragraph, then call
submit_plain_english_summary ONCE with JSON:
{"text":"...","event_ids":["evt-..."],"candidate_ids":["cand-..."]}
Use [] for either id list when it does not apply. If the tool rejects the summary, fix it and retry.
Then reply with one line: "Plain-English summary complete."
"""
