"""Compare seven pairs of Cypher queries on the loaded ModelAtlas graph.

Run ``python -m scripts.benchmark`` from the repository root. Timings include
fetching all records, but exclude PROFILE, correctness checks and report writing.
"""

import argparse
import csv
import json
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from neo4j import READ_ACCESS, Query
from neo4j.exceptions import Neo4jError

from app.config import Settings
from app.database.neo4j_client import Neo4jClient
from app.domain import LINEAGE_PATTERN
from scripts.etl.sources import RAW_DIR

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
WARMUPS = 3
TIMEOUT_SECONDS = 20
VARIANTS = ("naive", "optimized")
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Experiment:
    """Two queries with identical results and shared input parameters."""

    name: str
    description: str
    naive_cypher: str
    optimized_cypher: str
    params: dict[str, Any]


@dataclass(frozen=True)
class Measurement:
    """Client latency percentiles and the work recorded by one PROFILE run."""

    p50_ms: float | None = None
    p95_ms: float | None = None
    rows: int | None = None
    db_hits: int | None = None
    status: str = "ok"
    operators: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkResult:
    """An experiment and its measured variants."""

    experiment: Experiment
    naive: Measurement
    optimized: Measurement


def percentile(values: list[float], percent: float) -> float:
    """Return a linearly interpolated percentile, including the endpoints.

    Raises:
        ValueError: If the sample is empty or the percentile is outside 0..100.
    """
    if not values or not 0 <= percent <= 100:
        raise ValueError("Expected a nonempty sample and a percentile in 0..100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def total_db_hits(profile: dict[str, Any]) -> int:
    """Sum dbHits over an entire driver PROFILE tree, including its root."""
    return int(profile.get("dbHits", 0)) + sum(total_db_hits(child) for child in profile.get("children", []))


def data_commit(manifest: Path = RAW_DIR / "model_cards" / "manifest.json") -> str:
    """Read the local input revision without inventing provenance when it is missing."""
    if not manifest.exists():
        return "unrecorded (local manifest missing)"
    return json.loads(manifest.read_text(encoding="utf-8"))["commit"]


def speedup(naive: Measurement, optimized: Measurement) -> str:
    """Format the ratio of median latencies; values below one mean slower."""
    if naive.p50_ms is None or optimized.p50_ms is None or optimized.p50_ms <= 0:
        return "n/a"
    return f"{naive.p50_ms / optimized.p50_ms:.2f}x"


def plan_operators(profile: dict[str, Any]) -> tuple[str, ...]:
    """Read operator names from leaves to root, flattening branches for the report."""
    children = tuple(name for child in profile.get("children", []) for name in plan_operators(child))
    operator = profile.get("operatorType", "").split("@")[0]
    return children + ((operator,) if operator else ())


def build_experiments(params: dict[str, Any]) -> list[Experiment]:
    """Define the seven comparisons with stable ordering and shared parameters."""
    lineage = f"MATCH (c:Model)-[:{LINEAGE_PATTERN}*]->(m:Model {{id:$root}})"
    return [
        Experiment(
            "Author lookup",
            "The property filter scans Model nodes. The connected pattern starts from the unique Organization id.",
            "MATCH (m:Model) WHERE m.author = $author RETURN m.id AS id ORDER BY id",
            "MATCH (:Organization {id:$author})<-[:PUBLISHED_BY]-(m:Model) RETURN m.id AS id ORDER BY id",
            params,
        ),
        Experiment(
            "LIMIT early",
            (
                "Collecting the full ordered result keeps all matching models in memory. LIMIT allows a Top operator "
                "or an ordered index scan to keep only the requested rows."
            ),
            (
                "MATCH (m:Model {task:$task}) WITH m ORDER BY m.downloads DESC, m.id "
                "WITH collect({id:m.id, downloads:m.downloads}) AS models "
                "UNWIND models[..$limit] AS model RETURN model.id AS id, model.downloads AS downloads "
                "ORDER BY downloads DESC, id"
            ),
            (
                "MATCH (m:Model {task:$task}) RETURN m.id AS id, m.downloads AS downloads "
                "ORDER BY downloads DESC, id LIMIT $limit"
            ),
            params,
        ),
        Experiment(
            "Literal vs parameter",
            (
                "Each timed pair looks up the same real id. The literal changes between calls across 30 ids. "
                "Neo4j 5 auto-parameterizes literals, so this is not proof of a fresh plan compilation on every call. "
                "Explicit parameters keep the query text stable and avoid manual string escaping."
            ),
            "MATCH (m:Model {id:__LITERAL__}) RETURN m.id AS id",
            "MATCH (m:Model {id:$id}) RETURN m.id AS id",
            params,
        ),
        Experiment(
            "Cartesian product",
            (
                "The disconnected form can combine candidates before checking relationships. The connected pattern "
                "starts from one publisher and checks its models' licenses. The actual planner may also rewrite "
                "the naive form, so syntax alone does not prove that a CartesianProduct operator was used."
            ),
            (
                "MATCH (m:Model), (o:Organization {id:$org}) "
                "WHERE (m)-[:PUBLISHED_BY]->(o) AND (m)-[:HAS_LICENSE]->(:License {id:$license}) "
                "RETURN m.id AS id ORDER BY id"
            ),
            (
                "MATCH (:Organization {id:$org})<-[:PUBLISHED_BY]-(m:Model)-[:HAS_LICENSE]->(:License {id:$license}) "
                "RETURN DISTINCT m.id AS id ORDER BY id"
            ),
            params,
        ),
        Experiment(
            "Deep pagination",
            (
                "SKIP discards 5,000 sorted rows. Keyset pagination filters by the previous page's final "
                "(downloads, id) pair, including ties. Cursor discovery is outside the timed section because a "
                "normal client already has that cursor. An explicit downloads upper bound enables a range seek; "
                "PartialTop can order only the equal-download groups by id."
            ),
            (
                "MATCH (m:Model) WHERE m.downloads IS NOT NULL RETURN m.id AS id, m.downloads AS downloads "
                "ORDER BY downloads DESC, id SKIP $offset LIMIT $page_size"
            ),
            (
                "MATCH (m:Model) WHERE m.downloads <= $last_downloads "
                "AND (m.downloads < $last_downloads OR m.id > $last_id) "
                "RETURN m.id AS id, m.downloads AS downloads ORDER BY downloads DESC, id LIMIT $page_size"
            ),
            params,
        ),
        Experiment(
            "Bounded traversal",
            (
                "Both variants return distinct descendants within six hops. The naive pattern expands without "
                "a bound and filters path length afterwards; the optimized pattern puts the bound in the expansion. "
                "An unrestricted result would include deeper descendants and would be a different answer. "
                "The naive variant has a 20 s server transaction timeout. Breadth-first pruning checks and "
                "deduplicates nodes; on a small family, this can increase db hits despite the depth cap."
            ),
            (
                f"MATCH path = (c:Model)-[:{LINEAGE_PATTERN}*]->(m:Model {{id:$root}}) "
                "WHERE length(path) <= $depth RETURN DISTINCT c.id AS id ORDER BY id"
            ),
            (
                f"MATCH (c:Model)-[:{LINEAGE_PATTERN}*1..6]->(m:Model {{id:$root}}) "
                "RETURN DISTINCT c.id AS id ORDER BY id"
            ),
            params,
        ),
        Experiment(
            "Precomputed descendants",
            (
                "The live count visits the descendant graph and removes duplicates. The stored count needs one "
                "id lookup and a property read. This shifts work to ETL; the property must be refreshed "
                "after graph changes."
            ),
            lineage + " RETURN count(DISTINCT c) AS descendants",
            "MATCH (m:Model {id:$root}) RETURN m.descendant_count AS descendants",
            params,
        ),
    ]


class BenchmarkRunner:
    """Run paired queries, validate their answers, and collect measurements."""

    def __init__(self, client: Neo4jClient, settings: Settings, runs: int = 30) -> None:
        """Keep the existing client and validate the number of timed samples."""
        if runs < 1:
            raise ValueError("runs must be positive")
        self.client = client
        self.settings = settings
        self.runs = runs
        self.model_ids: list[str] = []

    def prepare(self) -> tuple[list[Experiment], dict[str, Any]]:
        """Read metadata, model ids and the cursor before any measurements.

        Raises:
            ValueError: If the full graph or the expected author-index baseline is missing.
        """
        indexes = self.client.read("SHOW INDEXES YIELD type, labelsOrTypes, properties RETURN *")
        if any(
            row["type"] == "RANGE" and row["labelsOrTypes"] == ["Model"] and row["properties"] == ["author"]
            for row in indexes
        ):
            raise ValueError("Author lookup needs no range index on Model.author; finish the index demo first")
        self.model_ids = [
            row["id"]
            for row in self.client.read("MATCH (m:Model) RETURN m.id AS id ORDER BY id LIMIT $count", {"count": 30})
        ]
        cursor = self.client.read(
            (
                "MATCH (m:Model) WHERE m.downloads IS NOT NULL RETURN m.id AS id, m.downloads AS downloads "
                "ORDER BY downloads DESC, id SKIP $offset LIMIT $count"
            ),
            {"offset": 4999, "count": 1},
        )
        root = self.client.read("MATCH (m:Model {id:$id}) RETURN m.id AS id", {"id": "Qwen/Qwen2.5-7B"})
        if len(self.model_ids) < 30 or not cursor or not root:
            raise ValueError("Load the full graph with make seed before benchmarking")
        params = {
            "author": "Qwen",
            "org": "Qwen",
            "license": "apache-2.0",
            "task": "text-generation",
            "limit": 10,
            "offset": 5000,
            "page_size": 20,
            "last_downloads": cursor[0]["downloads"],
            "last_id": cursor[0]["id"],
            "root": "Qwen/Qwen2.5-7B",
            "depth": 6,
        }
        metadata = {
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "neo4j_version": self.client.read("CALL dbms.components() YIELD versions RETURN versions[0] AS version")[0][
                "version"
            ],
            "models": self.client.read("MATCH (m:Model) RETURN count(m) AS count")[0]["count"],
            "runs": self.runs,
            "data_commit": data_commit(),
        }
        return build_experiments(params), metadata

    def execute(self, cypher: str, params: dict[str, Any], profile: bool = False) -> tuple[list[dict], float, dict]:
        """Fetch all rows in a READ session; return rows, elapsed ms and profile.

        The server timeout also applies to PROFILE. Session creation and client
        result comparison are excluded from the latency measurement.
        """
        query = Query(("PROFILE " if profile else "") + cypher, timeout=TIMEOUT_SECONDS)
        with self.client.driver.session(
            database=self.settings.neo4j_database, default_access_mode=READ_ACCESS
        ) as session:
            started = perf_counter()
            result = session.run(query, params)
            records = [record.data() for record in result]
            summary = result.consume()
            elapsed = (perf_counter() - started) * 1000
        return records, elapsed, summary.profile or {}

    def _execute_variant(
        self, experiment: Experiment, variant: str, iteration: int
    ) -> tuple[list[dict], float, dict] | None:
        """Resolve the literal experiment and handle only the allowed traversal timeout."""
        params = dict(experiment.params)
        cypher = getattr(experiment, f"{variant}_cypher")
        if "__LITERAL__" in experiment.naive_cypher:
            params["id"] = self.model_ids[iteration % len(self.model_ids)]
            # JSON strings are valid quoted Cypher literals, including escaped ids.
            cypher = cypher.replace("__LITERAL__", json.dumps(params["id"], ensure_ascii=True))
        try:
            return self.execute(cypher, params, profile=iteration == -1)
        except Neo4jError as exc:
            if experiment.name == "Bounded traversal" and variant == "naive" and "TimedOut" in (exc.code or ""):
                LOGGER.warning("Unbounded traversal exceeded the 20 s server timeout")
                return None
            raise

    def run(self, experiment: Experiment) -> BenchmarkResult:
        """Alternate execution order, check every pair, and PROFILE outside timing.

        Raises:
            AssertionError: If either timed or profiled variants return different records.
        """
        timings: dict[str, list[float]] = {variant: [] for variant in VARIANTS}
        timed_out: set[str] = set()
        for iteration in range(self.runs + WARMUPS):
            samples = {}
            for variant in VARIANTS[:: 1 if iteration % 2 == 0 else -1]:
                if variant in timed_out:
                    continue
                sample = self._execute_variant(experiment, variant, iteration)
                if sample is None:
                    timed_out.add(variant)
                    continue
                samples[variant] = sample[0]
                if iteration >= WARMUPS:
                    timings[variant].append(sample[1])
            if len(samples) == 2:
                self._assert_equal(experiment, samples)
        profiles = {
            variant: self._execute_variant(experiment, variant, -1) for variant in VARIANTS if variant not in timed_out
        }
        if all(profiles.get(variant) is not None for variant in VARIANTS):
            self._assert_equal(experiment, {variant: profiles[variant][0] for variant in VARIANTS})
        measurements = {}
        for variant in VARIANTS:
            sample = profiles.get(variant)
            measurements[variant] = (
                Measurement(status="timeout")
                if sample is None
                else Measurement(
                    percentile(timings[variant], 50),
                    percentile(timings[variant], 95),
                    len(sample[0]),
                    total_db_hits(sample[2]),
                    operators=plan_operators(sample[2]),
                )
            )
        return BenchmarkResult(experiment, measurements["naive"], measurements["optimized"])

    @staticmethod
    def _assert_equal(experiment: Experiment, samples: dict[str, list[dict]]) -> None:
        """Fail explicitly even when Python is invoked with optimization enabled."""
        if samples["naive"] != samples["optimized"]:
            raise AssertionError(f"{experiment.name}: naive and optimized answers differ")


def format_metric(value: float | int | None) -> str:
    """Render report metrics without inventing values for timed-out queries."""
    if value is None:
        return "timeout"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def describe_outcome(result: BenchmarkResult) -> str:
    """State the observed tradeoff instead of assuming the proposed change wins."""
    naive, optimized = result.naive, result.optimized
    if naive.status == "timeout":
        return "The naive traversal timed out; its latency and db hits are unavailable."
    latency = "lower" if optimized.p50_ms < naive.p50_ms else "not lower"
    if optimized.db_hits == naive.db_hits:
        work = "unchanged"
    else:
        work = "lower" if optimized.db_hits < naive.db_hits else "higher"
    return (
        f"The optimized median latency was {latency}, and its db-hit count was {work}. "
        "Small latency differences alone should not be treated as a reliable improvement."
    )


def render_markdown(results: list[BenchmarkResult], metadata: dict[str, Any]) -> str:
    """Render measured medians, complete metrics and reproducible query pairs."""
    lines = [
        "# Query benchmarks",
        "",
        (
            f"Measured at {metadata['date']} on Neo4j {metadata['neo4j_version']}, "
            f"with {metadata['models']:,} Model nodes."
        ),
        f"Data: model_cards dump at commit `{metadata.get('data_commit', 'unrecorded')}` (local download manifest).",
        "Node counts depend on this snapshot and can differ from the repository's reference load report.",
        (
            f"Each variant has {WARMUPS} warm-up runs and {metadata['runs']} timed runs. "
            "p50 and p95 use linear interpolation. Latency is client wall time including all returned records. "
            "Execution order alternates each pair. PROFILE runs separately; db hits are summed over its whole tree."
        ),
        (
            "All paired answers are checked, including row order, warm-ups and the PROFILE run. "
            "The literal test checks every id pair. A naive traversal timeout is the only allowed exception. "
            "The 20 s limit is a server transaction timeout, not a hard client deadline."
        ),
        "",
        (
            "These are warm-cache laptop measurements, not universal speed guarantees. "
            "The cache is not cleared, and compilation is not isolated. No index is created or removed. "
            "A speed-up below 1.00x means the optimized variant was slower."
        ),
        "",
        "| Experiment | Naive p50 ms | Optimized p50 ms | Speed-up | Naive db hits | Optimized db hits |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        lines.append(
            (
                f"| {result.experiment.name} | {format_metric(result.naive.p50_ms)} | "
                f"{format_metric(result.optimized.p50_ms)} | {speedup(result.naive, result.optimized)} | "
                f"{format_metric(result.naive.db_hits)} | {format_metric(result.optimized.db_hits)} |"
            )
        )
    lines.extend(
        [
            "",
            "## Complete measurements",
            "",
            "| Experiment | Variant | p50 ms | p95 ms | Rows | Db hits | Status |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for result in results:
        for variant in VARIANTS:
            metrics = getattr(result, variant)
            lines.append(
                (
                    f"| {result.experiment.name} | {variant} | {format_metric(metrics.p50_ms)} | "
                    f"{format_metric(metrics.p95_ms)} | {format_metric(metrics.rows)} | "
                    f"{format_metric(metrics.db_hits)} | {metrics.status} |"
                )
            )
    for result in results:
        lines.extend(
            [
                "",
                f"## {result.experiment.name}",
                "",
                result.experiment.description,
                "",
                (
                    f"Measured median speed-up: {speedup(result.naive, result.optimized)}. "
                    f"{describe_outcome(result)}"
                ),
                "",
                "Naive:",
                "",
                f"Observed operators (leaves first): {', '.join(result.naive.operators) or result.naive.status}.",
                "",
                "```cypher",
                result.experiment.naive_cypher,
                "```",
                "",
                "Optimized:",
                "",
                (
                    "Observed operators (leaves first): "
                    f"{', '.join(result.optimized.operators) or result.optimized.status}."
                ),
                "",
                "```cypher",
                result.experiment.optimized_cypher,
                "```",
                "",
                "Parameters:",
                "",
                "```json",
                json.dumps(result.experiment.params, indent=2),
                "```",
            ]
        )
    lines.extend(
        [
            "",
            (
                "For the literal pair, replace `__LITERAL__` with a JSON-quoted id from the first 30 "
                "Model ids in ascending order; supply the same id as `$id` to the parameterized query. "
                "The list cycles if more than 30 calls are requested."
            ),
            "",
            (
                "Neo4j's [parameter documentation](https://neo4j.com/docs/cypher-manual/5/syntax/parameters/) "
                "describes auto-parameterization. The [driver summary documentation]"
                "(https://neo4j.com/docs/python-manual/current/result-summary/) explains PROFILE metrics."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(results: list[BenchmarkResult], metadata: dict[str, Any], directory: Path = REPORTS_DIR) -> None:
    """Write Markdown and one CSV row per variant, including run metadata."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "benchmarks.md").write_text(render_markdown(results, metadata), encoding="utf-8")
    with (directory / "benchmarks.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "date",
                "neo4j_version",
                "models",
                "runs",
                "data_commit",
                "experiment",
                "variant",
                "p50_ms",
                "p95_ms",
                "rows",
                "db_hits",
                "status",
                "speedup",
            ]
        )
        for result in results:
            for variant in VARIANTS:
                metrics = getattr(result, variant)
                writer.writerow(
                    [
                        *(metadata[key] for key in ("date", "neo4j_version", "models", "runs")),
                        metadata.get("data_commit", "unrecorded"),
                        result.experiment.name,
                        variant,
                        metrics.p50_ms,
                        metrics.p95_ms,
                        metrics.rows,
                        metrics.db_hits,
                        metrics.status,
                        speedup(result.naive, result.optimized),
                    ]
                )


def main(argv: list[str] | None = None) -> int:
    """Benchmark the running database and write reports only after all checks pass."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--runs", type=int, default=30)
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be positive")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings(neo4j_uri=args.uri)
    client = Neo4jClient(settings)
    try:
        runner = BenchmarkRunner(client, settings, args.runs)
        experiments, metadata = runner.prepare()
        results = []
        for experiment in experiments:
            LOGGER.info(f"Benchmarking {experiment.name}")
            results.append(runner.run(experiment))
        write_reports(results, metadata)
    finally:
        client.close()
    LOGGER.info("Wrote reports/benchmarks.md and reports/benchmarks.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
