REPORT_DESCRIPTION = (
    "Writes a crystal-clear, plain-English executive summary highlighting what the records show across "
    "all documents (scope changes, instructions, delays, price impacts) and what needs checking for "
    "the commercial team."
)

REPORT_PROMPT = """\
STAGE: PLAIN-ENGLISH REPORT.

Write one clear, easily readable paragraph for a busy person or commercial reviewer.
Highlight the main points so the user understands the variation record just by reading it.
Use everyday words, short sentences, and strictly no more than 70 words. Explain any unavoidable
technical term. Summarise what the documents show (scope changed, instructions issued, delay or price
consequences) and what still needs checking. Be calm, clear and factual.

Use only the system-validated events, possible changes and review questions below. Do not add a
fact, date, amount or conclusion. Never state contractual entitlement or legal liability.
A possible change is a factual hypothesis for the commercial team to review.

Include the event_ids and candidate_ids that support the paragraph. Use [] when the paragraph
contains no case-specific fact. Call submit_plain_english_summary ONCE with JSON:
{"text":"...","event_ids":["evt-..."],"candidate_ids":["cand-..."]}
(The submit tool validates word count and legal conclusion constraints. Call check_unsupported_claims
only if you wish to verify wording beforehand). If rejected, fix it and retry.
Then reply with one line: "Plain-English summary complete."
"""
