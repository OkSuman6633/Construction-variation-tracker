import pytest

from app.errors import AppError
from app.services.cases import CaseService, ProjectService
from app.storage import paths
from app.storage.base import ObjectNotFound, PreconditionFailed
from app.storage.local import LocalObjectStore
from app.storage.sqlite import SQLiteObjectStore
from construction_variation_agent.shared_libraries.schemas import CaseCreate, ProjectCreate
from tests.helpers import case_body


def test_create_if_absent_and_generation_cas(tmp_path):
    s = LocalObjectStore(tmp_path)
    g1 = s.put("a/b.json", b"1", if_generation_match=0)
    with pytest.raises(PreconditionFailed):
        s.put("a/b.json", b"2", if_generation_match=0)
    g2 = s.put("a/b.json", b"2", if_generation_match=g1)
    assert g2 > g1
    with pytest.raises(PreconditionFailed):
        s.put("a/b.json", b"3", if_generation_match=g1)  # stale generation
    assert s.get("a/b.json").data == b"2"


def test_generation_monotonic_after_delete(tmp_path):
    s = LocalObjectStore(tmp_path)
    g1 = s.put("x.json", b"1")
    s.delete("x.json")
    with pytest.raises(ObjectNotFound):
        s.get("x.json")
    assert s.put("x.json", b"2", if_generation_match=0) > g1  # no ABA on stale generation


def test_local_store_blocks_escape(tmp_path):
    with pytest.raises(ValueError):
        LocalObjectStore(tmp_path).get("../../etc/passwd")


def test_local_case_listing_uses_portable_object_keys(tmp_path):
    store = LocalObjectStore(tmp_path)
    projects = ProjectService(store)
    cases = CaseService(store, projects)
    project = projects.create(
        "tenant-a",
        "user-a",
        ProjectCreate(name="Case listing project", reference="CASE-LIST"),
    )
    case = cases.create(
        "tenant-a",
        "user-a",
        CaseCreate.model_validate(case_body(project.project_id)),
    )

    assert [item.case_id for item in cases.list("tenant-a")] == [case.case_id]


def test_sqlite_store_cas_delete_reopen_and_prefix_listing(tmp_path):
    database = tmp_path / "cvea.db"
    store = SQLiteObjectStore(database)
    first = store.put("cases/t-001/c-001/state.json", b"first", if_generation_match=0)
    with pytest.raises(PreconditionFailed):
        store.put("cases/t-001/c-001/state.json", b"stale", if_generation_match=0)
    second = store.put("cases/t-001/c-001/state.json", b"second", if_generation_match=first)
    store.put("cases/t-002/c-002/state.json", b"other", if_generation_match=0)

    reopened = SQLiteObjectStore(database)
    assert reopened.get("cases/t-001/c-001/state.json").data == b"second"
    assert reopened.list("cases/t-001/") == ["cases/t-001/c-001/state.json"]
    reopened.delete("cases/t-001/c-001/state.json")
    with pytest.raises(ObjectNotFound):
        reopened.get("cases/t-001/c-001/state.json")
    assert reopened.put("cases/t-001/c-001/state.json", b"third", if_generation_match=0) > second


def test_local_to_sqlite_migration_is_idempotent_and_detects_conflicts(tmp_path):
    from scripts.migrate_local_to_sqlite import migrate_objects

    source = LocalObjectStore(tmp_path / "source")
    target = SQLiteObjectStore(tmp_path / "cvea.db")
    object_path = "uploads/tenant-a/case-a/document-a"
    source.put(object_path, b"evidence", if_generation_match=0)

    assert migrate_objects(source, target) == (1, 0)
    assert target.get(object_path).data == b"evidence"
    assert migrate_objects(source, target) == (0, 1)

    source.put(object_path, b"changed evidence", if_generation_match=1)
    with pytest.raises(RuntimeError, match="Conflicting SQLite object"):
        migrate_objects(source, target)


def test_audit_log_migration_is_idempotent(tmp_path):
    import json

    from scripts.migrate_local_to_sqlite import migrate_audit_log

    audit_log = tmp_path / "audit_events.jsonl"
    audit_log.write_text(json.dumps({"action": "case.create"}) + "\n", encoding="utf-8")
    database = tmp_path / "cvea.db"

    assert migrate_audit_log(audit_log, database) == (1, 0)
    assert migrate_audit_log(audit_log, database) == (0, 1)


@pytest.mark.parametrize("bad", ["../x", "A", "a/b", "ab", "x" * 80, "a b c", ""])
def test_path_builder_rejects_bad_ids(bad):
    with pytest.raises(AppError):
        paths.case_state(bad, "case-001")
    with pytest.raises(AppError):
        paths.case_state("tenant-a", bad)


def test_path_layout_matches_spec():
    assert paths.case_state("t-001", "c-001") == "cases/t-001/c-001/state.json"
    assert paths.upload("t-001", "c-001", "d-001") == "uploads/t-001/c-001/d-001"
    assert paths.report("t-001", "c-001", "v3", "pdf") == "reports/t-001/c-001/v3.pdf"
    with pytest.raises(AppError):
        paths.report("t-001", "c-001", "v3", "exe")


def test_gcs_adapter_maps_precondition_failure(monkeypatch):
    """Contract test with a fake client; NOT a substitute for a live-bucket check."""
    from google.api_core import exceptions as gexc

    from app.storage import gcs

    class FakeBlob:
        generation = 7

        def upload_from_string(self, data, content_type=None, if_generation_match=None):
            raise gexc.PreconditionFailed("nope")

    class FakeBucket:
        def blob(self, name):
            return FakeBlob()

    monkeypatch.setattr(
        gcs.storage,
        "Client",
        lambda project=None: type("C", (), {"bucket": lambda s, n: FakeBucket()})(),
    )
    store = gcs.GcsObjectStore("bkt")
    with pytest.raises(PreconditionFailed):
        store.put("p", b"x", if_generation_match=3)
