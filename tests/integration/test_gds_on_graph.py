"""GDS results on the real graph (after `make seed-sample` + `make gds` in CI).

Skipped when Neo4j is down or `make gds` has not been run, like the other
integration tests. Read-only.
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


@pytest.fixture(name="graph", scope="module")
def fixture_graph():
    settings = Settings(neo4j_uri=os.getenv("NEO4J_TEST_URI", "bolt://localhost:7687"))
    client = Neo4jClient(settings)
    try:
        ready = client.read("MATCH (m:Model) WHERE m.pagerank IS NOT NULL RETURN count(m) AS n")[0]["n"]
    except (ServiceUnavailable, ServiceUnavailableError, AuthError, OSError) as exc:
        client.close()
        pytest.skip(f"Neo4j not reachable: {exc}")
    if not ready:
        client.close()
        pytest.skip("GDS results missing: run `make gds` first")
    with TestClient(create_app(settings=settings, db=client)) as api:
        yield api, client


def _scalar(db, query, params=None):
    return db.read(query, params or {})[0]["n"]


def test_every_model_has_every_result(graph):
    _, db = graph
    query = (
        "MATCH (m:Model) WHERE m.pagerank IS NULL OR m.family_id IS NULL "
        "OR m.community IS NULL OR m.embedding IS NULL RETURN count(m) AS n"
    )
    assert _scalar(db, query) == 0


def test_parent_and_child_are_in_the_same_family(graph):
    _, db = graph
    query = f"MATCH (c:Model)-[:{LINEAGE_PATTERN}]->(p:Model) WHERE c.family_id <> p.family_id RETURN count(*) AS n"
    assert _scalar(db, query) == 0


def test_influential_models_have_descendants(graph):
    api, db = graph
    body = api.get("/graph/influential", params={"limit": 5}).json()
    ranks = [r["pagerank"] for r in body["results"]]
    assert ranks == sorted(ranks, reverse=True)
    average = db.read("MATCH (m:Model) RETURN avg(m.descendant_count) AS n")[0]["n"]
    assert body["results"][0]["descendant_count"] > average


def test_similar_models_share_data_or_papers(graph):
    api, db = graph
    row = db.read("MATCH (a:Model)-[:SIMILAR_TO]->(b:Model) RETURN a.id AS id LIMIT 1")
    if not row:
        pytest.skip("no SIMILAR_TO edge in this graph")
    body = api.get(f"/models/{row[0]['id']}/similar").json()
    assert body["results"]
    for item in body["results"]:
        assert 0.3 <= item["score"] <= 1
        assert item["shared_datasets"] + item["shared_papers"] >= 1


def test_family_endpoint_matches_the_graph(graph):
    api, db = graph
    top = db.read("MATCH (m:Model) RETURN m.id AS id, m.family_id AS family ORDER BY m.pagerank DESC LIMIT 1")[0]
    body = api.get(f"/models/{top['id']}/family").json()
    assert body["rank_in_family"] == 1  # the most influential model leads its family
    expected = _scalar(db, "MATCH (m:Model {family_id: $f}) RETURN count(m) AS n", {"f": top["family"]})
    assert body["family_size"] == expected


def test_embeddings_are_only_on_models(graph):
    _, db = graph
    query = (
        "MATCH (n) WHERE NOT n:Model AND (n.embedding IS NOT NULL OR n.content_embedding IS NOT NULL) "
        "RETURN count(n) AS n"
    )
    assert _scalar(db, query) == 0
