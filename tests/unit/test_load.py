"""Unit tests for the batch loader (Neo4j is replaced by a recording fake)."""

import numpy as np
import pandas as pd
import pytest

from app.database.schema import CONSTRAINTS, INDEXES
from scripts.etl.load import LINKS, GraphLoader, batches, load_license_rules, to_records
from scripts.etl.transform import Scope, build_graph, write_staged
from tests.conftest import FakeNeo4jClient
from tests.synthetic import parsed_frame


@pytest.fixture(name="staged_dir")
def fixture_staged_dir(tmp_path):
    write_staged(build_graph(parsed_frame(), Scope.load()), tmp_path, "synthetic")
    return tmp_path


def _sent_rows(fake: FakeNeo4jClient, needle: str) -> list[dict]:
    return [row for query, params in fake.calls if needle in query for row in params.get("rows", [])]


def test_to_records_returns_plain_python_types():
    df = pd.DataFrame({"n": np.array([1, 2], dtype="int64"), "x": [np.nan, 1.5], "tags": [np.array(["a"]), []]})
    records = to_records(df)
    assert records == [{"n": 1, "x": None, "tags": ["a"]}, {"n": 2, "x": 1.5, "tags": []}]
    assert type(records[0]["n"]) is int  # pylint: disable=unidiomatic-typecheck


def test_batches_split_evenly():
    assert [len(b) for b in batches(list(range(12)), 5)] == [5, 5, 2]


def test_license_rules_come_from_config():
    rules = {r["id"]: r["props"] for r in load_license_rules()}
    assert rules["apache-2.0"]["family"] == "permissive"
    assert rules["llama3.1"]["commercial_use"] == "conditional"


def test_load_all_sends_everything_in_order(staged_dir):
    fake = FakeNeo4jClient()
    timings = GraphLoader(fake, batch_size=4).load_all(staged_dir)
    queries = [query for query, _ in fake.calls]

    # reset first, then schema, then data
    assert "DETACH DELETE" in queries[0]
    assert queries[1 : 1 + len(CONSTRAINTS + INDEXES)] == CONSTRAINTS + INDEXES
    assert len(_sent_rows(fake, "MERGE (m:Model {id: row.id})\nSET m.author")) == 10
    assert [r["id"] for r in _sent_rows(fake, "ON CREATE SET m:Unlisted")] == ["ghost/Missing"]
    assert len(_sent_rows(fake, "MERGE (child)-[r:QUANTIZED_FROM]->(parent)")) == 1
    assert len(_sent_rows(fake, "MERGE (m)-[r:CITES]->(t)")) == 1
    # batches never exceed batch_size
    assert max(len(params.get("rows", [])) for _, params in fake.calls) <= 4
    assert set(timings) >= {"models", "lineage FINETUNED_FROM", "license rules"}
    assert queries[-1] == "MATCH (l:License) WHERE l.family IS NULL SET l.family = 'unknown'"


def test_every_link_creates_nodes_then_relationships(staged_dir):
    fake = FakeNeo4jClient()
    GraphLoader(fake).load_all(staged_dir, reset=False)
    queries = "\n".join(query for query, _ in fake.calls)
    for link in LINKS.values():
        assert f"MERGE (:{link.label} {{{link.key}: row.value}})" in queries
        assert f"MERGE (m)-[r:{link.rel_type}]->(t)" in queries
    assert "DETACH DELETE" not in queries
