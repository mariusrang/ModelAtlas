"""The GDS step, with a fake Neo4j client (GDS itself is tested on the real graph in CI)."""

from app.database.schema import CONSTRAINTS, INDEXES
from scripts import graph_algorithms as gds

SCHEMA_CALLS = len(CONSTRAINTS + INDEXES) + 1  # + db.awaitIndexes


class GdsFake:
    """Answers each GDS call with a plausible YIELD row and records the calls."""

    def __init__(self, missing=0):
        self.calls: list[tuple[str, dict]] = []
        self.missing = missing
        self.closed = False

    def write(self, query, params=None):
        self.calls.append((query, params or {}))
        if "gds.graph.project" in query:
            return [{"nodeCount": 10, "relationshipCount": 8, "projectMillis": 3}]
        if "gds.pageRank" in query:
            return [{"nodePropertiesWritten": 10, "ranIterations": 12, "didConverge": True, "computeMillis": 5}]
        if ".write($graph" in query:
            return [{"nodePropertiesWritten": 10, "computeMillis": 4}]
        return []

    def read(self, query, params=None):
        self.calls.append((query, params or {}))
        if "count(m) AS n" in query:
            return [{"n": self.missing}]
        return [{"model": "base/Root-7B", "pagerank": 3.2}]

    def close(self):
        self.closed = True


def test_every_algorithm_projects_runs_and_drops():
    fake = GdsFake()
    runs = gds.run_all(fake)
    assert [r["algorithm"] for r in runs] == [
        "PageRank",
        "WCC",
        "Louvain",
        "Node Similarity",
        "FastRP",
        "FastRP (content)",
    ]
    queries = [q for q, _ in fake.calls]
    assert "model_pagerank" in " ".join(queries[:SCHEMA_CALLS])  # indexes first
    queries = queries[SCHEMA_CALLS:]
    # leftovers from an earlier run are dropped first, old SIMILAR_TO edges removed
    assert all("gds.graph.drop" in q for q in queries[: len(gds.PROJECTIONS)])
    assert "DELETE r" in queries[len(gds.PROJECTIONS)]
    assert "REMOVE n.embedding" in queries[len(gds.PROJECTIONS) + 1]
    # each algorithm: project -> run -> drop, so only one projection is in memory at a time
    tail = queries[len(gds.PROJECTIONS) + 2 :]
    for i in range(0, len(tail), 3):
        assert "gds.graph.project" in tail[i]
        assert "gds." in tail[i + 1] and ".write($graph" in tail[i + 1]
        assert "gds.graph.drop" in tail[i + 2]


def test_projections_use_only_known_labels_and_types():
    for nodes, relationships in gds.PROJECTIONS.values():
        assert set(nodes) <= {"Model", "Dataset", "Paper", "Organization", "Architecture"}
        assert all(spec["orientation"] in {"NATURAL", "UNDIRECTED"} for spec in relationships.values())
    lineage = {"FINETUNED_FROM", "ADAPTER_OF", "QUANTIZED_FROM", "MERGED_FROM"}
    assert set(gds.PROJECTIONS["lineage"][1]) == lineage
    # the embedding used by link prediction must not see lineage edges (no leakage)
    assert not set(gds.PROJECTIONS["content"][1]) & lineage


def test_algorithms_are_parameterised_by_graph_name():
    for algo in gds.ALGORITHMS:
        assert "$graph" in algo.query
        assert algo.projection in gds.PROJECTIONS


def test_report_lists_runs_and_findings():
    runs = [
        {
            "algorithm": "PageRank",
            "projection": "lineage",
            "nodeCount": 10,
            "relationshipCount": 8,
            "seconds": 0.1,
            "ranIterations": 12,
            "computeMillis": 5,
        }
    ]
    report = gds.render_report(runs, {"PageRank: most influential models": [{"model": "a/b", "pagerank": 1.0}]}, 0)
    assert "| PageRank | lineage | 10 | 8 | 0.1 |" in report
    assert "ranIterations = 12" in report
    assert "Models missing a result: **0**" in report
    assert "| a/b | 1.0 |" in report
    assert "_(no rows)_" in gds.render_report(runs, {"empty": []}, 0)


def test_main_writes_report_and_fails_when_results_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(gds, "REPORTS_DIR", tmp_path)
    fake = GdsFake()
    monkeypatch.setattr(gds, "build_client", lambda uri: fake)
    assert gds.main(["--sample"]) == 0
    assert (tmp_path / "gds_report_sample.md").exists()
    assert fake.closed

    monkeypatch.setattr(gds, "build_client", lambda uri: GdsFake(missing=3))
    assert gds.main([]) == 1
    assert (tmp_path / "gds_report.md").exists()


def test_embeddings_are_written_on_models_only():
    for algo in gds.ALGORITHMS:
        if algo.name.startswith("FastRP"):
            assert "fastRP.mutate" in algo.query
            assert "nodeProperties.write($graph" in algo.query and "['Model']" in algo.query
