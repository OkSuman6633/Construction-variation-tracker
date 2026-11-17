# ADK architecture (as built)

Pinned: `google-adk==2.13.0`. Real ADK components throughout: `SequentialAgent`, `LoopAgent`, `LlmAgent`, function tools with `ToolContext`, session state, ADK callbacks, `Runner` + `InMemorySessionService`.

## Package
```
backend/construction_variation_agent/
  agent.py            root_agent + build_root_agent()   (SequentialAgent "construction_variation_coordinator")
  prompt.py           GLOBAL_RULES + render_instruction()
  tracing.py          TraceRecorder, budget/stage/isolation callbacks
  model_config.py     Gemini config (env), UnconfiguredModel, offline + live model checks
  profiles/           5 synthetic case profiles (fictional) with golden answers
  shared_libraries/   constants, types, schemas, provenance, runtime (scope), profile_provider
  sub_agents/         intake | document_extraction (LoopAgent) | cross_reference | variation_analysis | review_preparation | plain_english_report
                      each: agent.py + prompt.py + __init__.py
  tools/              evidence, document, case, reference, validation, chronology, review, job, submission
backend/app/analysis/ engine.py (deterministic validation/assembly) · runner.py (ADK job runner) · provider.py (GCS-backed evidence)
```

## Flow
API `POST /analyse` (idempotent) -> job record + case stage `analysing` -> queue task named by job id -> worker `POST /internal/tasks/analyse` -> `JobService.execute` -> `run_analysis` builds a fresh agent tree and in-memory ADK session (scope ids written by the backend only) -> intake -> extraction loop (one document per turn) -> cross-reference -> variation analysis -> review preparation -> plain-English report summary -> `assemble_result` (engine) -> CAS write to GCS case state -> audit -> job `needs_review`.

## Where authority sits
| Concern | Agent (interprets) | Deterministic code (decides) |
|---|---|---|
| Document text | reads via tools | parsers, SHA-256, MIME sniff, scan gate |
| Event facts | proposes events | citation present on cited page; amount on page; date normalisation; legal-language ban |
| Relationships | proposes with shared references | shared reference must literally exist in each document, else confidence capped 0.4 + flagged |
| Contradictions | may propose with citations | date/amount rules computed by the engine regardless |
| Candidates | groups events | amount must equal a linked event amount; evidence copied from events; `entitlement_asserted` is `Literal[False]` |
| Report summary | rewrites validated findings in simple English | word limit, legal-language check, and cited event/candidate ids are validated |
| Job outcome | none | stage guards + budgets + engine; the LLM cannot mark completion |
| Persistence | none | backend only (GCS CAS, BigQuery audit) |

## Scope and isolation
- Tools take no tenant/case/path arguments. `runtime_from_state` resolves a backend-registered `JobRuntime` and cross-checks it against backend-written session state.
- Document ids are checked against the job's authorised list on every call; denials are traced as `access_denied`.
- Prompt-injection: document text only reaches agents through tools, wrapped as untrusted; instructions never contain document text; injection-like phrases are flagged; legal-conclusion language is rejected at submission and re-scanned by the engine.

## Findings from the build that shaped the design
1. ADK replays every earlier agent's tool traffic to later agents as "For context:" transcripts - here that would resend all page text to every later stage (observed: 51/61 content items). `drop_other_agent_context` removes them; stage inputs travel via validated state rendered into instructions.
2. Loop iterations accumulate history even with `include_contents="none"`. `isolate_current_document` prunes to the current document (tested: request size is constant across documents).
3. Callback exceptions propagate, which is what lets budgets and stage guards fail the job closed.
4. `SequentialAgent` is deprecated in 2.13 in favour of `Workflow` (which cannot yet be an `LlmAgent` sub-agent). Pinned; migration tracked in KNOWN_LIMITATIONS.
5. ADK's persistent session services (SQL / Vertex Agent Engine) would violate the storage rule, so sessions are in-memory and rebuilt from GCS per job.
