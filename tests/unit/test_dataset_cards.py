"""Second source: dataset cards parsing, filtering and loading."""

import pandas as pd
import pytest

from scripts.etl.dataset_cards import build_dataset_tables, enrich_datasets, parse_dataset_tags, read_cards
from scripts.etl.load import GraphLoader
from scripts.etl.transform import Scope, build_graph, write_staged
from tests.conftest import FakeNeo4jClient
from tests.synthetic import parsed_frame

CARDS = pd.DataFrame(
    {
        "datasetId": ["org/chat-data", "org/nc-data", "other/unused"],
        "author": ["org", "org", "other"],
        "downloads": [100, 5, 1],
        "likes": [3, 0, 0],
        "tags": [
            [
                "license:MIT",
                "language:fr",
                "language:en",
                "task_categories:text-generation",
                "size_categories:1K<n<10K",
            ],
            ["license:cc-by-nc-4.0", "language:xx-not-iso", "region:us"],
            ["license:mit"],
        ],
        "createdAt": ["2024-01-01T00:00:00.000Z", None, "2024-02-02T00:00:00.000Z"],
        "card": ["long text"] * 3,
    }
)


@pytest.fixture(name="cards_dir")
def fixture_cards_dir(tmp_path):
    (tmp_path / "data").mkdir()
    CARDS.to_parquet(tmp_path / "data" / "train-0.parquet", index=False)
    return tmp_path


def test_parse_dataset_tags():
    parsed = parse_dataset_tags(list(CARDS["tags"][0]))
    assert parsed.license == "mit"  # lower-cased like model licenses, so both share one License node
    assert parsed.languages == ["fr", "en"]
    assert parsed.task_categories == ["text-generation"]
    assert parsed.size_category == "1K<n<10K"
    assert not parse_dataset_tags(["language:xx-not-iso", "license:"]).languages


def test_read_cards_keeps_only_cited_datasets_and_skips_card_text(cards_dir):
    cards = read_cards(cards_dir, {"org/chat-data", "org/nc-data", "ghost/never-published"})
    assert sorted(cards["datasetId"]) == ["org/chat-data", "org/nc-data"]
    assert "card" not in cards.columns


def test_tables_and_coverage(cards_dir):
    cited = pd.Series(["org/chat-data", "org/nc-data", "ghost/never-published", "org/chat-data"])
    tables, stats = enrich_datasets(cited, cards_dir)
    assert stats["datasets_cited"] == 3
    assert stats["datasets_with_card"] == 2
    assert stats["dataset_card_coverage_pct"] == pytest.approx(66.7)
    assert dict(zip(tables["dataset_license"]["dataset"], tables["dataset_license"]["license"])) == {
        "org/chat-data": "mit",
        "org/nc-data": "cc-by-nc-4.0",
    }
    assert sorted(tables["dataset_language"]["language"]) == ["en", "fr"]


def test_missing_dump_gives_empty_tables(tmp_path):
    tables, stats = enrich_datasets(pd.Series(["a/b"]), tmp_path / "nothing-here")
    assert all(table.empty for table in tables.values())
    assert stats["datasets_with_card"] == 0
    assert list(tables["datasets"].columns)[:2] == ["id", "author"]


def test_empty_tables_still_load(tmp_path):
    tables, _ = build_dataset_tables(pd.DataFrame(), pd.Series(dtype=str))
    graph = build_graph(parsed_frame(), Scope.load())
    graph.datasets = tables
    write_staged(graph, tmp_path, "synthetic")
    fake = FakeNeo4jClient()
    GraphLoader(fake).load_datasets(tmp_path)
    assert fake.calls[-1][0].startswith("MATCH (d:Dataset) WHERE d.listed IS NULL")


def test_loader_sends_dataset_properties_and_links(cards_dir, tmp_path):
    graph = build_graph(parsed_frame(), Scope.load())
    graph.datasets, _ = enrich_datasets(pd.Series(["org/chat-data", "org/nc-data"]), cards_dir)
    out = tmp_path / "staged"
    manifest = write_staged(graph, out, "synthetic")
    assert manifest["row_counts"]["datasets"] == 2

    fake = FakeNeo4jClient()
    GraphLoader(fake).load_datasets(out)
    queries = [query for query, _ in fake.calls]
    assert any("d.listed = true" in q for q in queries)
    assert any("MERGE (d)-[r:HAS_LICENSE]->(t)" in q for q in queries)
    assert any("MERGE (d)-[r:SUPPORTS]->(t)" in q for q in queries)
    license_rows = [row for q, p in fake.calls if "HAS_LICENSE" in q for row in p.get("rows", [])]
    assert {"dataset": "org/nc-data", "value": "cc-by-nc-4.0"} in license_rows
