"""Read-only checks on the loaded graph (sample or full).

Run after `make seed-sample` or `make seed`, with the stack up:
    make test-integration
Skipped automatically when Neo4j is not reachable or the graph is empty.
These tests never write, so they are safe to run against the full graph.
"""

import os

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable

from app.config import Settings
from app.database.neo4j_client import Neo4jClient
from app.database.schema import CONSTRAINTS
from app.domain import LINEAGE_TYPES
from app.errors import ServiceUnavailableError

pytestmark = pytest.mark.integration
LINEAGE = "|".join(LINEAGE_TYPES)


@pytest.fixture(name="db", scope="module")
def fixture_db():
    client = Neo4jClient(Settings(neo4j_uri=os.getenv("NEO4J_TEST_URI", "bolt://localhost:7687")))
    try:
        models = client.read("MATCH (m:Model) RETURN count(m) AS n")[0]["n"]
    except (ServiceUnavailable, ServiceUnavailableError, AuthError, OSError) as exc:
        client.close()
        pytest.skip(f"Neo4j not reachable: {exc}")
    if models == 0:
        client.close()
        pytest.skip("graph is empty: run `make seed-sample` first")
    yield client
    client.close()


def _count(db, query: str) -> int:
    return db.read(query)[0]["n"]


def test_constraints_exist(db):
    names = set(db.read("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names")[0]["names"])
    assert {s.split()[2] for s in CONSTRAINTS} <= names


def test_no_self_loops(db):
    assert _count(db, f"MATCH (m:Model)-[r:{LINEAGE}]->(m) RETURN count(r) AS n") == 0


def test_every_lineage_edge_says_if_its_kind_was_declared(db):
    assert _count(db, f"MATCH ()-[r:{LINEAGE}]->() WHERE r.declared IS NULL RETURN count(r) AS n") == 0


def test_every_model_has_a_publisher(db):
    assert _count(db, "MATCH (m:Model) WHERE NOT (m)-[:PUBLISHED_BY]->() RETURN count(m) AS n") == 0


def test_unlisted_models_have_no_declared_parents(db):
    # they are not in the dump, so we know nothing about their own parents
    assert _count(db, f"MATCH (m:Unlisted)-[:{LINEAGE}]->() RETURN count(m) AS n") == 0


def test_spam_was_filtered(db):
    assert _count(db, "MATCH (m:Model) WHERE m.id CONTAINS 'blockassist' RETURN count(m) AS n") == 0


def test_every_license_has_a_family(db):
    assert _count(db, "MATCH (l:License) WHERE l.family IS NULL RETURN count(l) AS n") == 0


def test_fulltext_search_finds_mistral(db):
    rows = db.read(
        "CALL db.index.fulltext.queryNodes('model_search', $q) YIELD node RETURN node.id AS id LIMIT 5",
        {"q": "mistral"},
    )
    assert any("mistral" in row["id"].lower() for row in rows)


def test_precomputed_depth_matches_a_real_path(db):
    row = db.read(f"""
        MATCH (m:Model) WHERE m.lineage_depth >= 2
        WITH m ORDER BY m.downloads DESC LIMIT 1
        MATCH p = (m)-[:{LINEAGE}*]->(root:Model) WHERE NOT (root)-[:{LINEAGE}]->()
        RETURN m.lineage_depth AS stored, max(length(p)) AS walked
        """)[0]
    assert row["stored"] == row["walked"]


def test_every_dataset_says_if_it_has_a_card(db):
    assert _count(db, "MATCH (d:Dataset) WHERE d.listed IS NULL RETURN count(d) AS n") == 0
