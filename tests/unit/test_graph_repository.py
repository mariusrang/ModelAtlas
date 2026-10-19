"""GraphRepository sends the right query file with the right parameters."""

import pytest

from app.domain import LINEAGE_PATTERN
from app.repositories.graph_repository import GraphRepository
from tests.conftest import FakeNeo4jClient


@pytest.fixture(name="fake")
def fixture_fake():
    return FakeNeo4jClient(rows=[{"id": "x"}])


@pytest.fixture(name="repo")
def fixture_repo(fake):
    return GraphRepository(fake)


def test_one_returns_first_row_or_none(repo, fake):
    assert repo.model_detail("a/b") == {"id": "x"}
    fake.rows = []
    assert repo.model_detail("a/b") is None
    assert repo.stats() == {}


def test_depth_and_lineage_types_are_filled_in(repo, fake):
    repo.lineage_up("a/b", 3, 25)
    query, params = fake.calls[-1]
    assert f"[:{LINEAGE_PATTERN}*1..3]" in query
    assert params == {"id": "a/b", "max_paths": 25}


def test_license_check_single_and_batched_use_the_same_query(repo, fake):
    repo.license_check("a/b", 4)
    repo.license_check_many(["a/b", "c/d"], 4)
    (single, single_params), (batch, batch_params) = fake.calls[-2:]
    assert single == batch
    assert single_params == {"ids": ["a/b"]}
    assert batch_params == {"ids": ["a/b", "c/d"]}


def test_connection_falls_back_when_no_common_ancestor(repo, fake):
    fake.rows = []
    assert repo.connection("a/b", "c/d", 4) is None
    assert len(fake.calls) == 2  # connection.cypher, then connection_no_common.cypher
    assert "CALL (a, b)" in fake.calls[0][0] and "CALL (a, b)" not in fake.calls[1][0]


@pytest.mark.parametrize(
    ("call", "expected_params"),
    [
        (lambda r: r.search("mistral*", None, 5), {"query": "mistral*", "task": None, "limit": 5}),
        (lambda r: r.descendants("a/b", 2, {"limit": 3}), {"id": "a/b", "limit": 3}),
        (lambda r: r.recommend_candidates({"task": "zero-shot-xyz"}), {"task": "zero-shot-xyz"}),
        (lambda r: r.organization_footprint("acme", 2, 7), {"id": "acme", "limit": 7}),
        (lambda r: r.dataset_detail("ds/one", 4), {"id": "ds/one", "limit": 4}),
        (lambda r: r.model_exists("a/b"), {"id": "a/b"}),
    ],
)
def test_parameters_are_passed_not_formatted(repo, fake, call, expected_params):
    call(repo)
    query, params = fake.calls[-1]
    assert params == expected_params
    for value in expected_params.values():
        if isinstance(value, str):
            assert value not in query  # values never end up inside the Cypher text
