"""API tests for the graph endpoints, on a fake repository (no Neo4j)."""

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_repository
from app.main import create_app
from tests.conftest import FakeNeo4jClient
from tests.fake_graph import FakeRepository


@pytest.fixture(name="repo")
def fixture_repo():
    return FakeRepository()


@pytest.fixture(name="api")
def fixture_api(settings, repo):
    app = create_app(settings=settings, db=FakeNeo4jClient())
    app.dependency_overrides[get_repository] = lambda: repo
    with TestClient(app) as test_client:
        yield test_client


def test_stats_groups_lineage_edges(api):
    body = api.get("/stats").json()
    assert body["models"] == 4
    assert body["lineage_edges"] == {"finetune": 1, "adapter": 1, "quantized": 1, "merge": 0}


def test_search_sends_a_safe_lucene_query(api, repo):
    body = api.get("/models/search", params={"q": "Acme base*", "limit": 5}).json()
    assert body["hits"][0]["model"]["id"] == "acme/base"
    assert repo.calls[-1] == ("search", "acme* AND base*", None, 5)


def test_search_rejects_text_without_words(api):
    response = api.get("/models/search", params={"q": "**"})
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


def test_model_page_with_slash_in_id(api):
    body = api.get("/models/acme/chat").json()
    assert body["model"]["id"] == "acme/chat"
    assert body["model"]["license"] == "apache-2.0"
    assert body["parents"] == [{"id": "acme/base", "relation": "finetune", "declared": True, "unlisted": False}]
    assert body["evidence"] == "VERIFIED"


def test_unknown_model_is_404(api):
    response = api.get("/models/nobody/nothing")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_lineage_lists_paths_and_roots(api):
    body = api.get("/models/bob/chat-GGUF/lineage", params={"depth": 3}).json()
    assert body["roots"] == ["acme/base"]
    assert body["paths"][0]["relations"] == ["quantized", "finetune"]
    assert [n["id"] for n in body["paths"][0]["nodes"]] == ["bob/chat-GGUF", "acme/chat", "acme/base"]
    assert body["truncated"] is False


def test_root_model_is_its_own_root(api):
    assert api.get("/models/acme/base/lineage").json()["roots"] == ["acme/base"]


def test_depth_above_the_configured_cap_is_400(api, settings):
    response = api.get("/models/acme/chat/lineage", params={"depth": settings.max_traversal_depth + 1})
    assert response.status_code == 400


def test_depth_above_12_is_rejected_by_validation(api):
    assert api.get("/models/acme/chat/lineage", params={"depth": 50}).status_code == 422


def test_descendants_pagination(api):
    first = api.get("/models/acme/base/descendants", params={"limit": 2}).json()
    assert [i["model"]["id"] for i in first["items"]] == ["acme/chat", "bob/chat-GGUF"]
    assert first["next_cursor"]
    second = api.get("/models/acme/base/descendants", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [i["model"]["id"] for i in second["items"]] == ["carl/lora"]
    assert second["next_cursor"] is None


def test_descendants_filter_by_relation(api, repo):
    body = api.get("/models/acme/base/descendants", params={"relation": "adapter"}).json()
    assert [i["relation"] for i in body["items"]] == ["adapter"]
    assert repo.calls[-1][3]["relation"] == "ADAPTER_OF"


def test_descendants_of_unknown_model_is_404(api):
    assert api.get("/models/nobody/nothing/descendants").status_code == 404


def test_bad_cursor_is_400(api):
    assert api.get("/models/acme/base/descendants", params={"cursor": "not-a-cursor"}).status_code == 400


def test_license_check_inherits_from_ancestor(api):
    body = api.get("/models/bob/chat-GGUF/license-check").json()
    assert body["declared_license"] is None
    assert body["inherited_from"] == "acme/chat"
    assert body["verdict"] == "commercial_use_ok"
    assert body["verdict_evidence"] == "DERIVED"
    assert "not legal advice" in body["disclaimer"]


def test_license_check_uses_configured_depth_by_default(api, repo, settings):
    api.get("/models/acme/base/license-check")
    assert repo.calls[-1] == ("license_check", "acme/base", settings.max_traversal_depth)


def test_connection(api):
    body = api.get("/connection", params={"a": "bob/chat-GGUF", "b": "carl/lora"}).json()
    assert body["common_ancestor"]["id"] == "acme/base"
    assert body["shortest_path"]["relations"] == ["quantized", "finetune", "adapter"]
    assert "Both descend from acme/base" in body["summary"]
    assert "1 shared training dataset" in body["summary"]


def test_connection_needs_two_models(api):
    assert api.get("/connection", params={"a": "acme/base", "b": "acme/base"}).status_code == 400


def test_recommend_commercial_drops_bad_lineage(api):
    body = api.post("/recommend", json={"task": "text-generation", "commercial_use": True, "limit": 5}).json()
    ids = [r["model"]["id"] for r in body["results"]]
    assert ids == ["acme/base", "acme/chat"]  # carl/lora is non-commercial, bob/chat-GGUF has no license
    assert all(r["license_verdict"] == "commercial_use_ok" for r in body["results"])
    assert body["results"][0]["score"] > body["results"][1]["score"]


def test_recommend_without_commercial_skips_license_check(api, repo):
    body = api.post("/recommend", json={"task": "text-generation", "limit": 2}).json()
    assert len(body["results"]) == 2
    assert body["results"][0]["license_verdict"] is None
    assert not [c for c in repo.calls if c[0] == "license_check"]


def test_recommend_validates_the_brief(api):
    assert api.post("/recommend", json={"limit": 2}).status_code == 422


def test_organization_footprint(api):
    body = api.get("/organizations/acme/footprint", params={"limit": 1}).json()
    assert body["own_models"] == 2
    assert body["top_builders"] == [{"organization": "bob", "models": 1}]
    assert body["direct_children_by_kind"] == {"adapter": 1}


def test_unknown_organization_is_404(api):
    assert api.get("/organizations/nobody/footprint").status_code == 404


def test_openapi_lists_every_endpoint(api):
    paths = set(api.get("/openapi.json").json()["paths"])
    assert {
        "/health",
        "/stats",
        "/connection",
        "/recommend",
        "/models/search",
        "/models/{model_id}",
        "/models/{model_id}/lineage",
        "/models/{model_id}/descendants",
        "/models/{model_id}/license-check",
        "/organizations/{org_id}/footprint",
    } <= paths
