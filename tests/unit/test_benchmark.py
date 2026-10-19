"""Benchmark statistics, correctness checks and reports without a real Neo4j."""

import csv
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from neo4j import READ_ACCESS
from neo4j.exceptions import Neo4jError

from app.config import Settings
from app.database.neo4j_client import Neo4jClient
from scripts.benchmark import (
    TIMEOUT_SECONDS,
    WARMUPS,
    BenchmarkResult,
    BenchmarkRunner,
    Experiment,
    Measurement,
    build_experiments,
    data_commit,
    describe_outcome,
    main,
    percentile,
    plan_operators,
    render_markdown,
    speedup,
    total_db_hits,
    write_reports,
)


@pytest.fixture(name="experiment")
def fixture_experiment() -> Experiment:
    """Return a small pair whose query text identifies each variant."""
    return Experiment("Example", "Use an index.", "RETURN 'naive'", "RETURN 'optimized'", {})


@pytest.fixture(name="runner")
def fixture_runner(settings: Settings) -> BenchmarkRunner:
    """Build the runner around a fake client and deterministic query samples."""
    runner = BenchmarkRunner(MagicMock(spec=Neo4jClient), settings, runs=3)
    runner.model_ids = ["org/first", 'org/with"quote', "org/with\\slash"]
    return runner


@pytest.mark.parametrize("percent, expected", [(0, 1), (50, 2.5), (95, 3.85), (100, 4)])
def test_percentile_interpolates_unsorted_values(percent: float, expected: float) -> None:
    """Match the stated percentile convention without modifying the sample."""
    values = [4.0, 1.0, 3.0, 2.0]
    assert percentile(values, percent) == pytest.approx(expected)
    assert values == [4, 1, 3, 2]
    assert percentile([7.0], percent) == 7


@pytest.mark.parametrize("values, percent", [([], 50), ([1.0], -1), ([1.0], 101)])
def test_percentile_rejects_invalid_inputs(values: list[float], percent: float) -> None:
    """Reject empty samples and invalid percentile bounds."""
    with pytest.raises(ValueError):
        percentile(values, percent)


def test_db_hits_include_root_and_all_branches() -> None:
    """Count nested work, including zero-hit operators and missing children."""
    profile = {"dbHits": 2, "children": [{"dbHits": 3, "children": [{"dbHits": 5}]}, {"children": [{"dbHits": 7}]}]}
    assert total_db_hits(profile) == 17
    assert total_db_hits({}) == 0


def test_data_commit_comes_from_manifest(tmp_path: Path) -> None:
    """Keep the measured dump revision independent of the configured branch name."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"commit": "a" * 40, "requested_revision": "main"}), encoding="utf-8")
    assert data_commit(manifest) == "a" * 40
    assert data_commit(tmp_path / "missing.json") == "unrecorded (local manifest missing)"


def test_plan_operators_follow_leaves_before_root() -> None:
    """Keep both branches and remove driver database suffixes from display names."""
    profile = {
        "operatorType": "ProduceResults@neo4j",
        "children": [{"operatorType": "NodeIndexSeek@neo4j"}, {"operatorType": "Filter@neo4j"}],
    }
    assert plan_operators(profile) == ("NodeIndexSeek", "Filter", "ProduceResults")


@pytest.mark.parametrize(
    "naive, optimized, expected",
    [(8.0, 2.0, "4.00x"), (1.0, 2.0, "0.50x"), (0.0, 2.0, "0.00x"), (1.0, 0.0, "n/a"), (None, 1.0, "n/a")],
)
def test_speedup_handles_slowdowns_and_missing_values(
    naive: float | None, optimized: float | None, expected: str
) -> None:
    """Keep slowdowns visible and avoid dividing by zero or missing timings."""
    assert speedup(Measurement(p50_ms=naive), Measurement(p50_ms=optimized)) == expected


@pytest.mark.parametrize("optimized, latency, work", [(2.0, "lower", "lower"), (10.0, "not lower", "higher")])
def test_outcome_keeps_latency_and_storage_work_separate(
    experiment: Experiment, optimized: float, latency: str, work: str
) -> None:
    """Describe an observed loss as clearly as an observed improvement."""
    result = BenchmarkResult(
        experiment, Measurement(5.0, 6.0, 1, 5), Measurement(optimized, optimized, 1, int(optimized))
    )
    assert f"median latency was {latency}" in describe_outcome(result)
    assert f"db-hit count was {work}" in describe_outcome(result)


def test_execute_consumes_results_and_sets_read_timeout(settings: Settings) -> None:
    """Time fetching and summary consumption, with PROFILE requested separately."""
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    result = session.run.return_value
    result.__iter__.return_value = iter([MagicMock(data=MagicMock(return_value={"id": "one"}))])
    result.consume.return_value.profile = {"dbHits": 4}
    runner = BenchmarkRunner(Neo4jClient(settings, driver), settings)
    with patch("scripts.benchmark.perf_counter", side_effect=[10.0, 10.002]):
        rows, elapsed, profile = runner.execute("RETURN $id AS id", {"id": "one"}, profile=True)
    assert rows == [{"id": "one"}]
    assert elapsed == pytest.approx(2)
    assert profile == {"dbHits": 4}
    query, params = session.run.call_args.args
    assert query.text == "PROFILE RETURN $id AS id"
    assert query.timeout == TIMEOUT_SECONDS == 20
    assert params == {"id": "one"}
    assert driver.session.call_args.kwargs == {"database": settings.neo4j_database, "default_access_mode": READ_ACCESS}
    result.consume.assert_called_once()


def test_runner_excludes_warmups_and_profile(runner: BenchmarkRunner, experiment: Experiment) -> None:
    """Use only timed samples and alternate which query runs first."""
    counts = {"naive": 0, "optimized": 0}

    def execute(cypher: str, _params: dict, profile: bool = False) -> tuple[list[dict], float, dict]:
        """Make warm-ups and PROFILE conspicuously slower than measured calls."""
        variant = "naive" if "naive" in cypher else "optimized"
        counts[variant] += 1
        elapsed = 10000.0 if counts[variant] <= WARMUPS or profile else float(counts[variant] - WARMUPS)
        return [{"id": "one"}], elapsed, {"dbHits": 2, "children": [{"dbHits": 3}]}

    with patch.object(runner, "execute", side_effect=execute) as mocked:
        result = runner.run(experiment)
    assert result.naive == result.optimized == Measurement(2.0, 2.9, 1, 5)
    assert counts == {"naive": WARMUPS + 4, "optimized": WARMUPS + 4}
    assert [call.args[0] for call in mocked.call_args_list[:4]] == [
        experiment.naive_cypher,
        experiment.optimized_cypher,
        experiment.optimized_cypher,
        experiment.naive_cypher,
    ]
    assert sum(call.kwargs["profile"] for call in mocked.call_args_list) == 2


@pytest.mark.parametrize("mismatch_at", [1, 9, 13])
def test_runner_rejects_mismatches_in_warmups_timing_and_profile(
    runner: BenchmarkRunner, experiment: Experiment, mismatch_at: int
) -> None:
    """Reject unequal answers even when row counts are the same."""
    samples = [([{"id": "same"}], 1.0, {}) for _ in range(14)]
    samples[mismatch_at] = ([{"id": "different"}], 1.0, {})
    with patch.object(runner, "execute", side_effect=samples), pytest.raises(AssertionError, match="answers differ"):
        runner.run(experiment)


def test_literal_pair_checks_each_id_and_escapes_it(runner: BenchmarkRunner) -> None:
    """Use the same changing id on both sides, including quoted identifiers."""
    experiment = build_experiments({})[2]
    with patch.object(runner, "execute", return_value=([{"id": "same"}], 1.0, {})) as mocked:
        runner.run(experiment)
    naive = [call for call in mocked.call_args_list if "__LITERAL__" not in call.args[0] and "$id" not in call.args[0]]
    assert len({call.args[0] for call in naive}) == 3
    assert any('with\\"quote' in call.args[0] for call in naive)
    for offset in range(0, len(mocked.call_args_list), 2):
        first, second = mocked.call_args_list[offset : offset + 2]
        assert first.args[1]["id"] == second.args[1]["id"]


@pytest.mark.parametrize("fail_profile", [False, True])
def test_only_naive_traversal_timeout_is_reported(runner: BenchmarkRunner, fail_profile: bool) -> None:
    """Retain optimized measurements if the naive traversal or its PROFILE times out."""
    experiment = build_experiments({})[5]
    error = Neo4jError._hydrate_neo4j(code="Neo.ClientError.Transaction.TransactionTimedOut", message="timeout")

    def execute(cypher: str, _params: dict, profile: bool = False) -> tuple[list[dict], float, dict]:
        """Simulate a server timeout without contacting Neo4j."""
        if "length(path)" in cypher and (not fail_profile or profile):
            raise error
        return [{"id": "one"}], 2.0, {"dbHits": 3}

    with patch.object(runner, "execute", side_effect=execute):
        result = runner.run(experiment)
    assert result.naive == Measurement(status="timeout")
    assert result.optimized == Measurement(2.0, 2.0, 1, 3)


def test_non_timeout_database_errors_propagate(runner: BenchmarkRunner, experiment: Experiment) -> None:
    """Never turn syntax errors or other failures into a successful report."""
    error = Neo4jError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="bad query")
    with patch.object(runner, "execute", side_effect=error), pytest.raises(Neo4jError):
        runner.run(experiment)


def test_prepare_requires_the_baseline_schema(runner: BenchmarkRunner) -> None:
    """Reject an author range index instead of silently benchmarking the wrong baseline."""
    runner.client.read.return_value = [{"type": "RANGE", "labelsOrTypes": ["Model"], "properties": ["author"]}]
    with pytest.raises(ValueError, match="range index"):
        runner.prepare()


def test_prepare_requires_the_full_graph(runner: BenchmarkRunner) -> None:
    """Give an actionable error when there is no deep page or target model."""
    runner.client.read.return_value = []
    with pytest.raises(ValueError, match="make seed"):
        runner.prepare()


@pytest.mark.parametrize("deep_root", [None, "deep/root"])
def test_prepare_reads_cursor_and_metadata(runner: BenchmarkRunner, deep_root: str | None) -> None:
    """Resolve the last row before the offset page outside the measured queries."""
    runner.client.read.side_effect = [
        [],
        [{"id": f"org/{number}"} for number in range(30)],
        [{"id": "org/cursor", "downloads": 12}],
        [{"id": "Qwen/Qwen2.5-7B"}],
        [{"version": "5.26"}],
        [{"count": 156611}],
        [{"id": "deep/root"}],
    ]
    experiments, metadata = runner.prepare(deep_root)
    assert len(experiments) == (8 if deep_root else 7)
    if deep_root:
        assert experiments[-1].params["root"] == deep_root
        assert metadata["deep_root"] == deep_root
    assert experiments[4].params["last_id"] == "org/cursor"
    assert experiments[4].params["last_downloads"] == 12
    assert metadata["models"] == 156611
    assert runner.client.read.call_args_list[2].args[1]["offset"] == 4999


def test_query_pairs_preserve_ties_and_traversal_horizon() -> None:
    """Guard semantic details that a downloads-only cursor or unbounded result would lose."""
    experiments = build_experiments({})
    assert "m.id > $last_id" in experiments[4].optimized_cypher
    assert "downloads DESC, id" in experiments[4].naive_cypher
    assert "length(path) <= $depth" in experiments[5].naive_cypher
    assert "*1..6" in experiments[5].optimized_cypher


def test_traversal_depth_is_shared_by_both_variants() -> None:
    """Changing the horizon must change both the filter and the expansion bound."""
    experiment = build_experiments({"depth": 3})[5]
    assert experiment.params["depth"] == 3
    assert "length(path) <= $depth" in experiment.naive_cypher
    assert "*1..3" in experiment.optimized_cypher
    assert "*1..6" not in experiment.optimized_cypher


@pytest.mark.parametrize("depth", [0, 13, True, "6", "6]->() DELETE m //"])
def test_traversal_depth_rejects_invalid_query_structure(depth: object) -> None:
    """Only a validated integer may be embedded in the Cypher range syntax."""
    with pytest.raises(ValueError, match="integer in 1..12"):
        build_experiments({"depth": depth})


def test_reports_include_complete_metrics_and_timeouts(experiment: Experiment, tmp_path: Path) -> None:
    """Keep Markdown and CSV consistent, including missing values and run metadata."""
    result = BenchmarkResult(experiment, Measurement(8.0, 10.0, 1, 42), Measurement(2.0, 3.0, 1, 2))
    timeout = replace(result, experiment=replace(experiment, name="Timeout"), naive=Measurement(status="timeout"))
    metadata = {"date": "2026-10-16", "neo4j_version": "5.26", "models": 156611, "runs": 30, "data_commit": "a" * 40}
    markdown = render_markdown([result, timeout], metadata)
    assert "| Example | 8.000 | 2.000 | 4.00x | 42 | 2 |" in markdown
    assert "| Example | naive | 8.000 | 10.000 | 1 | 42 | ok |" in markdown
    assert "| Timeout | timeout | 2.000 | n/a | timeout | 2 |" in markdown
    assert "156,611 Model nodes" in markdown
    assert "a" * 40 in markdown
    assert "Use an index." in markdown
    assert markdown.count("```json") == 1
    overridden = replace(result, experiment=replace(experiment, params={"root": "another/model"}))
    assert '"root": "another/model"' in render_markdown([result, overridden], metadata)
    write_reports([result, timeout], metadata, tmp_path)
    assert (tmp_path / "benchmarks.md").read_text(encoding="utf-8") == markdown
    with (tmp_path / "benchmarks.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4
    assert rows[0]["p95_ms"] == "10.0"
    assert rows[0]["runs"] == "30"
    assert rows[0]["data_commit"] == "a" * 40
    assert rows[2]["status"] == "timeout"
    assert rows[2]["p50_ms"] == ""


def test_cli_closes_client_on_failure(settings: Settings) -> None:
    """Release the driver and avoid report writes if preparation fails."""
    with patch("scripts.benchmark.Settings", return_value=settings), patch("scripts.benchmark.Neo4jClient") as client:
        client.return_value.read.return_value = []
        with pytest.raises(ValueError, match="make seed"):
            main(["--runs", "1", "--uri", "bolt://example:7687"])
    client.return_value.close.assert_called_once()


def test_cli_rejects_nonpositive_runs() -> None:
    """Reject invalid samples before connecting to a database."""
    with pytest.raises(SystemExit) as error:
        main(["--runs", "0"])
    assert error.value.code == 2
