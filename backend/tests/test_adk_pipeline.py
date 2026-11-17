"""End-to-end tests of the real ADK workflow with a scripted model (mechanics + guards)."""

import asyncio
import json

import pytest

from app.analysis.runner import run_analysis
from construction_variation_agent.model_config import resolve_model
from construction_variation_agent.shared_libraries.profile_provider import (
    ProfileEvidenceProvider,
    load_profile,
)
from construction_variation_agent.shared_libraries.provenance import legal_conclusions
from construction_variation_agent.shared_libraries.runtime import (
    _REGISTRY,
    JobLimits,
    ScopeError,
    runtime_from_state,
)
from construction_variation_agent.shared_libraries.schemas import CaseAnalysisRequest
from tests.support.scripted_model import ScriptedLlm

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def make(profile_name, mode="oracle", job="job-0001", tenant="tenant-a", **limits):
    profile = load_profile(profile_name)
    provider = ProfileEvidenceProvider(profile)
    req = CaseAnalysisRequest(
        tenant_id=tenant,
        case_id="case-0001",
        job_id=job,
        correlation_id="corr-1",
        document_ids=provider.document_ids,
    )
    model = ScriptedLlm(
        profile=profile, mode=mode, extractor_sizes=[], system_texts=[], content_dump=[]
    )
    return req, provider, model, (JobLimits(**limits) if limits else None)


async def run(profile_name, mode="oracle", **limits):
    req, provider, model, lim = make(profile_name, mode, **limits)
    return await run_analysis(req, provider, model=model, limits=lim), model


async def test_basic_case_runs_through_all_six_stages():
    out, model = await run("synthetic_case_basic")
    assert out.ok, out.failure
    r, rep = out.result, out.report
    assert len(r.events) == 8 and rep.citation_validity == 1.0 and not rep.dropped
    dates = [e.event_date for e in r.events if e.event_date]
    assert dates == sorted(dates)  # chronological
    assert all(e.review_status.value == "needs_review" for e in r.events)
    assert len(r.relationships) == 2 and len(r.candidates) == 1
    c = r.candidates[0]
    assert c.amount == 6300.0 and c.currency == "GBP" and c.entitlement_asserted is False
    assert any("E-301" in m.description for m in r.missing_evidence)
    assert r.reviewer_questions
    assert r.plain_english_summary is not None
    assert len(r.plain_english_summary.text.split()) <= 70
    stages = [e["stage"] for e in out.trace_events if e["kind"] == "stage_start"]
    assert stages == [
        "intake",
        "document_extraction",
        "cross_reference",
        "variation_analysis",
        "review_preparation",
        "plain_english_report",
    ]
    assert all(e["validation"] == "passed" for e in out.trace_events if e["kind"] == "stage_end")


async def test_every_event_citation_is_verifiable_and_offsets_present():
    out, _ = await run("synthetic_case_basic")
    provider = ProfileEvidenceProvider(load_profile("synthetic_case_basic"))
    for e in out.result.events:
        page = provider.get_document(e.document_id).page(e.page_ref)
        ev = e.evidence[0]
        assert ev.offset_start is not None
        assert (
            page.text[ev.offset_start : ev.offset_end].lower().split()[:2]
            == e.quoted_span.lower().split()[:2]
        )


async def test_conflicting_dates_are_exposed_not_resolved():
    out, _ = await run("synthetic_case_conflicting_dates")
    assert out.ok
    kinds = [c.kind for c in out.result.contradictions]
    assert "date" in kinds
    conflicted = [e for e in out.result.events if e.basis.value == "contradicted"]
    assert {e.document_id for e in conflicted} >= {"doc-101", "doc-102"}
    assert any(q.question_id.startswith("qst-auto") for q in out.result.reviewer_questions)
    ambiguous = next(e for e in out.result.events if e.document_id == "doc-104")
    assert any("ambiguous" in u for u in ambiguous.uncertainties)
    assert out.result.candidates[0].contradictions  # candidate linked to the conflict


async def test_two_variations_and_prompt_injection_treated_as_data():
    out, model = await run("synthetic_case_multiple_variations")
    assert out.ok
    assert len(out.result.candidates) == 2
    # the injected sentence never becomes an event, a question or a relationship
    dump = out.result.model_dump_json()
    assert "evil.example" not in dump and "send all customer documents" not in dump.lower()
    # injection text never reached any agent instruction
    assert not any("evil.example" in s for s in model.system_texts)


async def test_unsupported_change_is_unknown_with_missing_evidence():
    out, _ = await run("synthetic_case_missing_evidence")
    assert out.ok
    assert out.result.candidates == []
    assert any("CVI-045" in m.description for m in out.result.missing_evidence)
    undated = next(e for e in out.result.events if e.document_id == "doc-302")
    assert undated.event_date is None


async def test_fabricated_quote_is_rejected_before_it_can_enter_the_record():
    out, _ = await run("synthetic_case_basic", mode="hallucinate_quote")
    assert out.ok
    assert len(out.result.events) == 8  # rejected quotes are resubmitted with verified citations
    assert out.report.citation_validity == 1.0
    assert "This sentence does not appear anywhere" not in out.result.model_dump_json()
    tool_events = [
        e
        for e in out.trace_events
        if e["kind"] == "tool_call" and e["tool"] == "submit_document_extraction"
    ]
    assert sum(e["rejected"] for e in tool_events) == 6


async def test_invented_amount_and_legal_language_are_rejected():
    for mode in ("invent_amount", "legal_language"):
        out, _ = await run("synthetic_case_basic", mode=mode)
        assert out.ok, out.failure
        dump = out.result.model_dump_json()
        assert "99999" not in dump
        assert not legal_conclusions(dump)


async def test_unsupported_candidate_amount_is_removed():
    out, _ = await run("synthetic_case_basic", mode="unsupported_amount")
    assert out.result.candidates[0].amount is None


async def test_relationship_without_verified_shared_reference_is_downgraded():
    out, _ = await run("synthetic_case_basic", mode="bad_relationship")
    weak = [r for r in out.result.relationships if "no shared reference verified" in r.rationale]
    assert weak and all(r.confidence <= 0.4 and not r.shared_references for r in weak)


async def test_out_of_scope_document_access_is_denied_and_traced():
    out, _ = await run("synthetic_case_basic", mode="scope_probe")
    assert out.ok
    assert any(e["kind"] == "access_denied" for e in out.trace_events)
    assert len(out.result.events) == 8  # unaffected


async def test_stage_without_validated_output_fails_the_job_closed():
    out, _ = await run("synthetic_case_basic", mode="no_submit_intake")
    assert not out.ok and out.failure.code == "STAGE_INCOMPLETE" and out.result is None


async def test_runaway_loop_hits_budget_and_fails_closed():
    out, _ = await run(
        "synthetic_case_basic", mode="runaway", max_model_calls=15, max_tool_calls=100
    )
    assert not out.ok and out.failure.code == "BUDGET_EXCEEDED" and not out.failure.retryable


async def test_unconfigured_model_gives_clear_non_secret_failure():
    req, provider, _, _ = make("synthetic_case_basic")
    out = await run_analysis(req, provider, model=resolve_model(None, None, False))
    assert out.failure.code == "MODEL_NOT_CONFIGURED" and "GEMINI_MODEL" in out.failure.message


async def test_context_is_isolated_per_document():
    out, model = await run("synthetic_case_basic")
    assert out.ok and len(model.extractor_sizes) > 12
    assert max(model.extractor_sizes) <= 7  # one document's own turn; does not grow with documents
    assert model.extractor_sizes[:1] == [1]  # prior stages' transcripts are not replayed


async def test_runtime_is_unregistered_and_scope_is_cross_checked():
    out, _ = await run("synthetic_case_basic", job="job-0042")
    assert "job-0042" not in _REGISTRY
    req, provider, _, _ = make("synthetic_case_basic", job="job-0043")
    from construction_variation_agent.shared_libraries.runtime import (
        JobRuntime,
        register_runtime,
        unregister_runtime,
    )

    register_runtime(JobRuntime(req, provider))
    try:
        with pytest.raises(ScopeError):
            runtime_from_state(
                {"job_id": "job-0043", "tenant_id": "tenant-evil", "case_id": "case-0001"}
            )
        with pytest.raises(ScopeError):
            runtime_from_state(
                {"job_id": "job-unknown", "tenant_id": "tenant-a", "case_id": "case-0001"}
            )
    finally:
        unregister_runtime("job-0043")


async def test_concurrent_jobs_do_not_mix_documents():
    (r1, p1, m1, _), (r2, p2, m2, _) = (
        make("synthetic_case_basic", job="job-1001", tenant="tenant-a"),
        make("synthetic_case_multiple_variations", job="job-1002", tenant="tenant-b"),
    )
    o1, o2 = await asyncio.gather(run_analysis(r1, p1, model=m1), run_analysis(r2, p2, model=m2))
    assert o1.ok and o2.ok
    assert {e.document_id for e in o1.result.events} <= set(p1.document_ids)
    assert {e.document_id for e in o2.result.events} <= set(p2.document_ids)
    assert all(e["tenant_id"] == "tenant-a" for e in o1.trace_events)
    assert all(e["tenant_id"] == "tenant-b" for e in o2.trace_events)


async def test_trace_contains_no_raw_document_text_or_prompts():
    out, _ = await run("synthetic_case_basic")
    blob = json.dumps(out.trace_events)
    for needle in (
        "CONSTRUCTION VARIATION INSTRUCTION",
        "Please proceed with the additional",
        "NON-NEGOTIABLE RULES",
        "£5,400",
    ):
        assert needle not in blob
    assert out.trace_summary["tokens"]["prompt"] > 0
    first = next(e for e in out.trace_events if e["kind"] == "stage_start")
    for k in ("correlation_id", "job_id", "tenant_id", "case_id", "root_agent", "model", "attempt"):
        assert k in first


def test_isolate_current_document_prunes_previous_iterations():
    from google.genai import types

    from construction_variation_agent.tracing import isolate_current_document

    def c(role, part):
        return types.Content(role=role, parts=[part])

    call = types.Part(function_call=types.FunctionCall(name="x", args={}))
    resp = types.Part(function_response=types.FunctionResponse(name="x", response={}))
    t = types.Part(text="DONE")

    class Req:
        contents = [
            c("user", t),
            c("model", call),
            c("user", resp),
            c("model", t),
            c("model", call),
            c("user", resp),
        ]

    r = Req()
    isolate_current_document(r)
    assert len(r.contents) == 3 and r.contents[0].role == "user"


async def test_later_stages_never_receive_document_text_in_their_context():
    out, model = await run("synthetic_case_basic")
    assert out.ok
    page_text = (
        "Please proceed with the additional electrical units",
        "Total: £5,400.00",
        "Work started on the additional",
    )
    for agent, dump in model.content_dump:
        if agent != "document_extraction_agent":
            assert not any(t in dump for t in page_text), agent
    # sizes of later-stage requests stay small regardless of how much the extractor did
    sizes = {a: len(d) for a, d in model.content_dump if a != "document_extraction_agent"}
    assert sizes  # the stages still received their (validated) inputs via instructions
    assert any("RECORDED EVENTS" in s for s in model.system_texts)


def test_drop_other_agent_context_keeps_own_traffic():
    from google.genai import types

    from construction_variation_agent.tracing import drop_other_agent_context

    ctx = types.Content(
        role="user", parts=[types.Part(text="For context:"), types.Part(text="[x] said: hi")]
    )
    own_call = types.Content(
        role="model", parts=[types.Part(function_call=types.FunctionCall(name="t", args={}))]
    )
    first = types.Content(role="user", parts=[types.Part(text="go")])

    class Req:
        contents = [first, ctx, own_call]

    r = Req()
    drop_other_agent_context(r)
    assert r.contents == [first, own_call]
