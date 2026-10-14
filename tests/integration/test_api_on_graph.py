"""The API endpoints run against the real loaded graph (sample or full).

Read-only, like test_graph_invariants.py. The assertions are relative to what
is in the graph (we pick a real parent/child pair first), so they pass on the
sample and on the full dump.
"""

import os

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import AuthError, ServiceUnavailable

from app.config import Settings
from app.database.neo4j_client import Neo4jClient
from app.domain import LINEAGE_PATTERN
from app.errors import ServiceUnavailableError
from app.main import create_app

pytestmark = pytest.mark.integration

PAIR_QUERY = f"""
MATCH (c:Model)-[r:{LINEAGE_PATTERN}]->(p:Model)
WHERE NOT c:Unlisted AND NOT p:Unlisted AND (c)-[:PUBLISHED_BY]->()
RETURN c.id AS child, p.id AS parent, type(r) AS rel_type
ORDER BY c.downloads DESC, c.id
LIMIT 1
"""


@pytest.fixture(name="graph", scope="module")
def fixture_graph():
    settings = Settings(neo4j_uri=os.getenv("NEO4J_TEST_URI", "bolt://localhost:7687"))
    client = Neo4jClient(settings)
    try:
        pair = client.read(PAIR_QUERY)
    except (ServiceUnavailable, ServiceUnavailableError, AuthError, OSError) as exc:
        client.close()
        pytest.skip(f"Neo4j not reachable: {exc}")
    if not pair:
        client.close()
        pytest.skip("graph is empty: run `make seed-sample` first")
    app = create_app(settings=settings, db=client)
    with TestClient(app) as api:
        yield api, client, pair[0]


def test_stats_match_direct_counts(graph):
    api, db, _ = graph
    body = api.get("/stats").json()
    assert body["models"] == db.read("MATCH (m:Model) RETURN count(m) AS n")[0]["n"]
    assert sum(body["lineage_edges"].values()) > 0


def test_search_finds_the_child_by_its_name(graph):
    api, _, pair = graph
    name = pair["child"].split("/")[-1]
    body = api.get("/models/search", params={"q": name, "limit": 50}).json()
    assert pair["child"] in [h["model"]["id"] for h in body["hits"]]
    ranks = [h["rank"] for h in body["hits"]]
    assert ranks == sorted(ranks, reverse=True)


def test_model_page_lists_the_parent(graph):
    api, _, pair = graph
    body = api.get(f"/models/{pair['child']}").json()
    assert pair["parent"] in [p["id"] for p in body["parents"]]
    assert body["organization"]


def test_lineage_goes_through_the_parent(graph):
    api, _, pair = graph
    body = api.get(f"/models/{pair['child']}/lineage", params={"depth": 6}).json()
    assert body["paths"], "a model with a parent has at least one path"
    assert any(p["nodes"][1]["id"] == pair["parent"] for p in body["paths"])
    for path in body["paths"]:
        assert len(path["relations"]) == len(path["nodes"]) - 1 <= 6


def test_descendants_are_sorted_and_paginated(graph):
    api, _, pair = graph
    first = api.get(f"/models/{pair['parent']}/descendants", params={"depth": 1, "limit": 5}).json()
    assert first["items"]
    downloads = [i["model"]["downloads"] or 0 for i in first["items"]]
    assert downloads == sorted(downloads, reverse=True)
    assert all(i["distance"] == 1 for i in first["items"])
    if first["next_cursor"]:
        second = api.get(
            f"/models/{pair['parent']}/descendants",
            params={"depth": 1, "limit": 5, "cursor": first["next_cursor"]},
        ).json()
        seen = {i["model"]["id"] for i in first["items"]}
        assert not seen & {i["model"]["id"] for i in second["items"]}


def test_license_check_sees_the_parent(graph):
    api, _, pair = graph
    body = api.get(f"/models/{pair['child']}/license-check").json()
    parent = next(a for a in body["ancestors"] if a["id"] == pair["parent"])
    assert parent["distance"] == 1
    assert parent["path"] == [pair["child"], pair["parent"]]
    assert body["verdict"] in {"commercial_use_ok", "conditional", "needs_review", "not_allowed"}


def test_connection_finds_the_parent_as_common_ancestor(graph):
    api, _, pair = graph
    body = api.get("/connection", params={"a": pair["child"], "b": pair["parent"]}).json()
    assert body["common_ancestor"]["id"] == pair["parent"]
    assert body["common_ancestor"]["distance_from_a"] == 1
    assert len(body["shortest_path"]["nodes"]) == 2


def test_recommend_respects_the_brief(graph):
    api, _, _ = graph
    body = api.post("/recommend", json={"task": "text-generation", "commercial_use": True, "limit": 3}).json()
    for rec in body["results"]:
        assert rec["model"]["task"] == "text-generation"
        assert rec["license_verdict"] in {"commercial_use_ok", "conditional"}


def test_organization_footprint_counts_own_models(graph):
    api, db, pair = graph
    org = db.read("MATCH (:Model {id: $id})-[:PUBLISHED_BY]->(o) RETURN o.id AS id", {"id": pair["parent"]})
    if not org:
        pytest.skip("parent has no publisher")
    org_id = org[0]["id"]
    body = api.get(f"/organizations/{org_id}/footprint", params={"depth": 2}).json()
    expected = db.read("MATCH (:Organization {id: $id})<-[:PUBLISHED_BY]-(m) RETURN count(m) AS n", {"id": org_id})
    assert body["own_models"] == expected[0]["n"]
