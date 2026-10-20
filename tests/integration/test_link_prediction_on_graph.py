"""Link prediction results on the real graph (after `make gds-sample` + `make predict-sample` in CI).

Skipped when Neo4j is down or `make predict` has not been run. Read-only.
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
        ready = client.read("MATCH ()-[r:PREDICTED_PARENT]->() RETURN count(r) AS n")[0]["n"]
    except (ServiceUnavailable, ServiceUnavailableError, AuthError, OSError) as exc:
        client.close()
        pytest.skip(f"Neo4j not reachable: {exc}")
    if not ready:
        client.close()
        pytest.skip("no PREDICTED_PARENT edge: run `make predict` first")
    with TestClient(create_app(settings=settings, db=client)) as api:
        yield api, client


def _scalar(db, query):
    return db.read(query)[0]["n"]


def test_predictions_only_for_models_without_a_declared_parent(graph):
    _, db = graph
    query = (
        f"MATCH (c:Model)-[:PREDICTED_PARENT]->() WHERE EXISTS {{ (c)-[:{LINEAGE_PATTERN}]->() }} RETURN count(c) AS n"
    )
    assert _scalar(db, query) == 0


def test_predictions_are_scored_ranked_and_versioned(graph):
    _, db = graph
    query = (
        "MATCH ()-[r:PREDICTED_PARENT]->() WHERE r.score IS NULL OR NOT r.score >= 0 OR r.score > 1 "
        "OR NOT r.rank IN [1, 2, 3] OR r.model_version IS NULL RETURN count(r) AS n"
    )
    assert _scalar(db, query) == 0


def test_no_model_is_predicted_as_its_own_parent_or_twice(graph):
    _, db = graph
    assert _scalar(db, "MATCH (m:Model)-[:PREDICTED_PARENT]->(m) RETURN count(m) AS n") == 0
    query = "MATCH (c)-[r:PREDICTED_PARENT]->() WITH c, r.rank AS rank, count(*) AS k WHERE k > 1 RETURN count(c) AS n"
    assert _scalar(db, query) == 0


def test_endpoint_matches_the_graph(graph):
    api, db = graph
    row = db.read("MATCH (c:Model)-[r:PREDICTED_PARENT {rank: 1}]->(p) RETURN c.id AS child, p.id AS parent LIMIT 1")[0]
    body = api.get(f"/models/{row['child']}/predicted-parents").json()
    assert body["evidence"] == "HYPOTHESIS"
    assert body["declared_parents"] == 0
    assert body["results"][0]["model"]["id"] == row["parent"]
