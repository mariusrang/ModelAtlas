"""Unit tests for the ETL transform step, on the synthetic dump in tests/synthetic.py."""

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.etl import transform
from scripts.etl.transform import Scope, ancestors_closure, break_cycles, build_graph
from tests.synthetic import parsed_frame, raw_frame


@pytest.fixture(name="scope")
def fixture_scope() -> Scope:
    return Scope.load()


@pytest.fixture(name="graph")
def fixture_graph(scope):
    return build_graph(parsed_frame(), scope)


def test_scope_file_matches_profile_decisions(scope):
    assert scope.min_downloads == 50 and scope.min_likes == 1
    assert "blockassist" in scope.spam_tags
    assert "1910.09700" in scope.template_papers


def test_spam_and_unpopular_models_are_dropped(graph):
    ids = set(graph.models["id"])
    assert "spam/bot" not in ids
    assert "z/Nobody" not in ids
    assert graph.stats["spam_models_dropped"] == 1


def test_unpopular_ancestor_is_added_back(graph):
    ids = set(graph.models["id"])
    assert "d/Old-Base" in ids  # 2 downloads, but parent of a popular model
    assert graph.stats["ancestors_added"] == 1


def test_parent_missing_from_dump_becomes_unlisted(graph):
    assert list(graph.unlisted["id"]) == ["ghost/Missing"]
    orgs = graph.links["organization"]
    assert ("ghost/Missing", "ghost") in set(zip(orgs["model"], orgs["organization"]))


def test_self_loops_and_cycles_are_removed(graph):
    pairs = set(zip(graph.lineage["child"], graph.lineage["parent"]))
    assert ("g/Self", "g/Self") not in pairs
    assert graph.stats["self_loops_dropped"] == 1
    assert graph.stats["cycle_edges_dropped"] == 1
    assert len({("h/LoopA", "h/LoopB"), ("h/LoopB", "h/LoopA")} & pairs) == 1


def test_relation_kinds_map_to_relationship_types(graph):
    by_pair = {(c, p): (t, d) for c, p, t, d in graph.lineage.itertuples(index=False)}
    assert by_pair[("a/Chat-7B", "base/Root-7B")] == ("FINETUNED_FROM", True)
    assert by_pair[("b/Chat-7B-GGUF", "a/Chat-7B")] == ("QUANTIZED_FROM", True)
    assert by_pair[("e/Child-of-old", "d/Old-Base")] == ("ADAPTER_OF", True)
    assert by_pair[("c/Merge-7B", "a/Chat-7B")] == ("MERGED_FROM", True)
    # untyped parent: stored as FINETUNED_FROM but flagged as not declared
    assert by_pair[("f/Uses-ghost", "ghost/Missing")] == ("FINETUNED_FROM", False)


def test_lineage_metrics(graph):
    models = graph.models.set_index("id")
    assert models.loc["base/Root-7B", "lineage_depth"] == 0
    assert models.loc["b/Chat-7B-GGUF", "lineage_depth"] == 2
    # Root's descendants: Chat, GGUF, Merge, Old-Base, Child-of-old
    assert models.loc["base/Root-7B", "descendant_count"] == 5
    assert models.loc["base/Root-7B", "child_count"] == 3
    assert list(models.loc["b/Chat-7B-GGUF", "root_ids"]) == ["base/Root-7B"]
    assert models.loc["b/Chat-7B-GGUF", "formats"] == ["gguf"]


def test_link_tables_filter_noise(graph):
    papers = set(graph.links["paper"]["paper"])
    assert papers == {"2310.06825"}  # the template CO2 paper is gone
    datasets = set(graph.links["dataset"]["dataset"])
    assert datasets == {"HuggingFaceH4/ultrachat_200k"}  # "custom" and a .jsonl file are gone
    assert graph.stats["fake_dataset_links_dropped"] == 2
    assert set(graph.links["architecture"]["architecture"]) == {"llama"}
    assert set(graph.links["language"]["language"]) == {"en", "fr"}
    assert set(graph.links["license"]["license"]) == {"apache-2.0", "llama3.1"}


def test_ancestors_closure_and_break_cycles_helpers():
    parents = {"c": ["b"], "b": ["a"], "x": ["y"], "y": ["x"]}
    assert ancestors_closure({"c"}, parents) == {"a", "b", "c"}
    assert len(break_cycles({"x", "y"}, parents)) == 1


def test_main_writes_staged_tables_and_report(tmp_path, monkeypatch):
    sample_dir = tmp_path / "sample"
    sample_dir.mkdir()
    pq.write_table(pa.Table.from_pandas(raw_frame()), sample_dir / "sample.parquet")
    monkeypatch.setattr(transform, "SAMPLE_DIR", sample_dir)
    monkeypatch.setattr(transform, "REPORTS_DIR", tmp_path / "reports")

    out_dir = tmp_path / "staged"
    assert transform.main(["--sample", "--out-dir", str(out_dir)]) == 0
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_counts"]["models"] == 10
    assert (out_dir / "link_license.parquet").exists()
    assert "| spam models dropped | 1 |" in (tmp_path / "reports" / "etl_report_sample.md").read_text(encoding="utf-8")
