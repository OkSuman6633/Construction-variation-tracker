# Known limitations and unresolved decisions

## Not verified in the build sandbox (no Google API / cloud access)
- No Gemini call has ever been made. The model id is unverified (`scripts/check_model.py --live`). Tool-calling with JSON-string arguments is designed for Gemini compatibility but unproven against it.
- GCS adapter, signed upload URLs, BigQuery audit sink, Cloud Tasks queue and OIDC task verifier are implemented but only covered by local/fake tests.
- Quality gates (>=95% citation validity, >=90% explicit-date accuracy) are NOT measured. Current tests use a scripted model that replays golden answers: they prove pipeline mechanics and guards, not Gemini accuracy.

## Behaviour limits
- Citation check proves the quote exists on the cited page and the amount/date are on that page; it cannot prove the quote semantically supports the description. Reviewer verification remains essential.
- Extraction runs sequentially, one document per turn (latency risk vs the 8-minute p95 proposal; the extraction agent can later be fanned out per document in separate sessions).
- Images (JPG/PNG) are accepted and registered but not text-extracted; scanned/handwritten content needs an OCR or vision adapter (not built). EXIF timestamps are recorded as unverified. EML attachments are listed, not parsed.
- Numeric dates are read day-first and flagged ambiguous; month-first sources need reviewer correction.
- Re-analysis is blocked once reviewer decisions exist (to avoid overwriting them).
- Duplicate files (same SHA-256) are registered with a warning and excluded from analysis.

## Platform limits
- `SequentialAgent` is deprecated in ADK 2.13 (see ADK_ARCHITECTURE.md #4).
- Rate limiting is per instance. Case/project listing scans a tenant prefix (add an index object before scale).
- Agent trace events go to Cloud Logging format only; the BigQuery `agent_events` table writer is not wired. A failed BigQuery audit insert is logged (`audit_lost=true`) and not retried until the M6 outbox.
- `InlineThreadQueue` and `NoopTaskVerifier` are local-dev only; staging/production refuse them (config check).
- Malware scanning has no production implementation: files stay QUARANTINED and cannot be analysed until a scanner is chosen (`UnavailableScanner`).
- No retention/deletion policy is implemented yet (Doc 01 open assumption).

## Decisions needed
1. Gemini endpoint for customer data: AI Studio (no residency assurance) vs Vertex AI in a UK/EU region.
2. Identity provider. 3. Malware scanner. 4. Billing provider. 5. Max file size and retention defaults.
