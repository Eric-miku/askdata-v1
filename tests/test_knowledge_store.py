import json

from fastapi.testclient import TestClient

from askdata.api import routes
from askdata.api.app import app
from askdata.knowledge.store import KnowledgeStore
from askdata.api.routes import _resolve_knowledge


def test_knowledge_store_versions_and_rollback(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite")
    first = store.save({"kind": "metric", "standard_name": "销售额", "formula": "SUM(amount)", "aliases": ["营收"]})
    second = store.save({"kind": "metric", "standard_name": "销售额", "formula": "SUM(net_amount)", "aliases": ["营收", "净销售额"]}, entry_id=first["id"])
    assert second["version"] == 2
    assert [item["version"] for item in store.list_versions(first["id"])] == [2, 1]
    restored = store.rollback(first["id"], 1)
    assert restored["formula"] == "SUM(amount)"
    assert restored["status"] == "draft"


def test_delete_removes_version_history(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite")
    entry = store.save({"kind": "term", "standard_name": "客户"})
    store.save({"kind": "term", "standard_name": "有效客户"}, entry_id=entry["id"])
    assert store.delete(entry["id"])
    assert store.list_versions(entry["id"]) == []


def test_unpublished_terms_are_not_injected_into_questions():
    question, clarification = _resolve_knowledge("查询营收")
    assert question == "查询营收"
    assert clarification is None


def test_bulk_knowledge_import_reports_partial_errors_and_exports(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite")
    monkeypatch.setattr(routes, "knowledge_store", store)
    client = TestClient(app, backend_options={"use_uvloop": True})

    response = client.post("/api/knowledge/import", json={
        "mode": "upsert",
        "entries": [
            {
                "kind": "metric", "standard_name": "销售额", "status": "published",
                "formula": "SUM(amount)", "aliases": ["营收"],
            },
            {"kind": "unknown", "standard_name": "错误条目"},
        ],
    })

    assert response.status_code == 200
    assert response.json()["requested"] == 2
    assert response.json()["imported"] == 1
    assert response.json()["failed"] == 1
    assert response.json()["entries"][0]["status"] == "draft"
    assert response.json()["errors"][0]["index"] == 1

    updated = client.post("/api/knowledge/import", json={
        "entries": [{"kind": "metric", "standard_name": "销售额", "formula": "SUM(net_amount)"}],
    })
    assert updated.json()["entries"][0]["version"] == 2
    assert len(store.list()) == 1

    exported = client.get("/api/knowledge/export", params={"format": "json"})
    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == 'attachment; filename="askdata-knowledge.json"'
    assert json.loads(exported.content)["entries"][0]["formula"] == "SUM(net_amount)"

    csv_export = client.get("/api/knowledge/export", params={"format": "csv"})
    assert csv_export.status_code == 200
    assert csv_export.content.startswith(b"\xef\xbb\xbf")
