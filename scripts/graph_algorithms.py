"""Graph Data Science step: run graph algorithms and write the results on the nodes.

Usage (stack running, graph loaded with `make seed` or `make seed-sample`):
    python -m scripts.graph_algorithms             # make gds
    python -m scripts.graph_algorithms --sample    # report name for the CI sample

Each algorithm answers one question of the project:

- PageRank on lineage edges (child -> parent): which base models does the
  ecosystem rely on the most? -> Model.pagerank
- WCC on lineage edges: which models belong to the same family? -> Model.family_id
- Louvain on undirected lineage edges: which sub-families exist inside a big
  family? -> Model.community
- Node Similarity on Model -> Dataset / Paper: which models were trained on the
  same data or cite the same papers? -> (:Model)-[:SIMILAR_TO {score}]->(:Model)
- FastRP on lineage + data + papers + publishers + architectures: a 64-d vector
  per model that places a model close to its family -> Model.embedding
- FastRP again WITHOUT lineage edges: what a model "looks like" from its data,
  papers, publisher and architecture only. The link-prediction step uses this
  one, because an embedding built from lineage edges would already contain the
  parent we are trying to guess (data leakage) -> Model.content_embedding

Results are written back (write mode) so the API reads them with plain, indexed
Cypher instead of running an algorithm on every request. Everything here is
DERIVED, never VERIFIED: it is computed from the declared facts.
"""

import argparse
import logging
import sys
import time
from dataclasses import dataclass

from app.database.neo4j_client import Neo4jClient
from app.database.schema import apply_schema
from app.domain import LINEAGE_TYPES
from scripts.etl.sources import REPORTS_DIR
from scripts.seed_data import build_client

logger = logging.getLogger("modelatlas.gds")

CONTENT_TYPES = ["TRAINED_ON", "CITES", "PUBLISHED_BY", "HAS_ARCHITECTURE"]
ALL_TYPES = [*LINEAGE_TYPES, *CONTENT_TYPES]

# name -> (node labels, relationship projection)
PROJECTIONS = {
    "lineage": (["Model"], {t: {"orientation": "NATURAL"} for t in LINEAGE_TYPES}),
    "lineage_undirected": (["Model"], {t: {"orientation": "UNDIRECTED"} for t in LINEAGE_TYPES}),
    "model_features": (
        ["Model", "Dataset", "Paper"],
        {"TRAINED_ON": {"orientation": "NATURAL"}, "CITES": {"orientation": "NATURAL"}},
    ),
    "embedding": (
        ["Model", "Dataset", "Paper", "Organization", "Architecture"],
        {t: {"orientation": "UNDIRECTED"} for t in ALL_TYPES},
    ),
    "content": (
        ["Model", "Dataset", "Paper", "Organization", "Architecture"],
        {t: {"orientation": "UNDIRECTED"} for t in CONTENT_TYPES},
    ),
}

PROJECT_QUERY = """
CALL gds.graph.project($name, $nodes, $relationships)
YIELD nodeCount, relationshipCount, projectMillis
RETURN nodeCount, relationshipCount, projectMillis
"""
DROP_QUERY = "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName"
CLEAR_SIMILAR_QUERY = "MATCH ()-[r:SIMILAR_TO]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 10000 ROWS"


@dataclass(frozen=True)
class Algorithm:
    """One GDS algorithm in write mode."""

    name: str
    projection: str
    query: str


ALGORITHMS = [
    Algorithm(
        "PageRank",
        "lineage",
        # edges go child -> parent, so rank flows up to the models others are built on;
        # 20 iterations did not converge on the full graph, 50 does
        """CALL gds.pageRank.write($graph, {writeProperty: 'pagerank', maxIterations: 50, dampingFactor: 0.85})
        YIELD nodePropertiesWritten, ranIterations, didConverge, computeMillis
        RETURN nodePropertiesWritten, ranIterations, didConverge, computeMillis""",
    ),
    Algorithm(
        "WCC",
        "lineage",
        """CALL gds.wcc.write($graph, {writeProperty: 'family_id'})
        YIELD nodePropertiesWritten, componentCount, computeMillis
        RETURN nodePropertiesWritten, componentCount, computeMillis""",
    ),
    Algorithm(
        "Louvain",
        "lineage_undirected",
        # concurrency 1 so two runs give the same communities
        """CALL gds.louvain.write($graph, {writeProperty: 'community', concurrency: 1})
        YIELD nodePropertiesWritten, communityCount, modularity, ranLevels, computeMillis
        RETURN nodePropertiesWritten, communityCount, modularity, ranLevels, computeMillis""",
    ),
    Algorithm(
        "Node Similarity",
        "model_features",
        # Jaccard on the datasets + papers two models declare; degreeCutoff 2 skips models
        # with a single dataset/paper, which would be "100% similar" to thousands of others
        """CALL gds.nodeSimilarity.write($graph, {
            writeRelationshipType: 'SIMILAR_TO', writeProperty: 'score',
            topK: 5, similarityCutoff: 0.3, degreeCutoff: 2})
        YIELD nodesCompared, relationshipsWritten, computeMillis
        RETURN nodesCompared, relationshipsWritten, computeMillis""",
    ),
    Algorithm(
        "FastRP",
        "embedding",
        """CALL gds.fastRP.write($graph, {
            embeddingDimension: 64, iterationWeights: [0.0, 1.0, 1.0], randomSeed: 42,
            writeProperty: 'embedding'})
        YIELD nodePropertiesWritten, computeMillis
        RETURN nodePropertiesWritten, computeMillis""",
    ),
    Algorithm(
        "FastRP (content)",
        "content",
        """CALL gds.fastRP.write($graph, {
            embeddingDimension: 64, iterationWeights: [0.0, 1.0, 1.0], randomSeed: 42,
            writeProperty: 'content_embedding'})
        YIELD nodePropertiesWritten, computeMillis
        RETURN nodePropertiesWritten, computeMillis""",
    ),
]

# Read-only queries for the report.
TOP_PAGERANK = """
MATCH (m:Model) WHERE m.pagerank IS NOT NULL
RETURN m.id AS model, round(m.pagerank, 2) AS pagerank, m.descendant_count AS descendants
ORDER BY m.pagerank DESC LIMIT 10
"""
TOP_FAMILIES = """
MATCH (m:Model) WHERE m.family_id IS NOT NULL
WITH m.family_id AS family, count(*) AS size ORDER BY size DESC LIMIT 10
CALL (family) {
  MATCH (top:Model {family_id: family})
  RETURN top.id AS most_influential ORDER BY top.pagerank DESC LIMIT 1
}
RETURN family, size, most_influential
"""
FAMILY_SIZES = """
MATCH (m:Model) WHERE m.family_id IS NOT NULL
WITH m.family_id AS family, count(*) AS size
RETURN count(*) AS families, sum(CASE WHEN size = 1 THEN 1 ELSE 0 END) AS singletons, max(size) AS largest
"""
TOP_COMMUNITIES = """
MATCH (m:Model) WHERE m.community IS NOT NULL
WITH m.community AS community, count(*) AS size ORDER BY size DESC LIMIT 10
CALL (community) {
  MATCH (top:Model {community: community})
  RETURN top.id AS most_influential ORDER BY top.pagerank DESC LIMIT 1
}
RETURN community, size, most_influential
"""
TOP_SIMILAR = """
MATCH (a:Model)-[s:SIMILAR_TO]->(b:Model) WHERE a.id < b.id
RETURN a.id AS model_a, b.id AS model_b, round(s.score, 3) AS score
ORDER BY s.score DESC, a.downloads + b.downloads DESC LIMIT 10
"""
# Copies of the same model (same card, same datasets) all score 1.0 above, so we
# also show the pairs that come from two different lineage families.
TOP_SIMILAR_OTHER_FAMILY = """
MATCH (a:Model)-[s:SIMILAR_TO]->(b:Model) WHERE a.id < b.id AND a.family_id <> b.family_id
RETURN a.id AS model_a, b.id AS model_b, round(s.score, 3) AS score
ORDER BY s.score DESC, a.downloads + b.downloads DESC LIMIT 10
"""
# Sanity check of the embeddings: the nearest neighbours of a well-known base
# model should be its own family (fine-tunes, quantized copies).
EMBEDDING_NEIGHBOURS = """
MATCH (anchor:Model {id: $anchor}) WHERE anchor.embedding IS NOT NULL
MATCH (m:Model) WHERE m <> anchor AND m.downloads >= 1000 AND m.embedding IS NOT NULL
WITH m, gds.similarity.cosine(anchor.embedding, m.embedding) AS cosine
RETURN m.id AS model, round(cosine, 3) AS cosine ORDER BY cosine DESC LIMIT 10
"""
MISSING_RESULTS = """
MATCH (m:Model)
WHERE m.pagerank IS NULL OR m.family_id IS NULL OR m.community IS NULL
   OR m.embedding IS NULL OR m.content_embedding IS NULL
RETURN count(m) AS n
"""
EMBEDDING_ANCHOR = "mistralai/Mistral-7B-v0.1"


def drop_projections(client: Neo4jClient) -> None:
    """Remove leftovers of a previous (maybe interrupted) run."""
    for name in PROJECTIONS:
        client.write(DROP_QUERY, {"name": name})


def project(client: Neo4jClient, name: str) -> dict:
    """Create one in-memory projection and return its size."""
    nodes, relationships = PROJECTIONS[name]
    return client.write(PROJECT_QUERY, {"name": name, "nodes": nodes, "relationships": relationships})[0]


def run_all(client: Neo4jClient) -> list[dict]:
    """Project, run and drop, algorithm by algorithm (keeps the heap small)."""
    apply_schema(client)  # makes sure the pagerank / family / community indexes exist
    drop_projections(client)
    client.write(CLEAR_SIMILAR_QUERY)  # re-running must not duplicate SIMILAR_TO edges
    results = []
    for algo in ALGORITHMS:
        start = time.perf_counter()
        size = project(client, algo.projection)
        stats = client.write(algo.query, {"graph": algo.projection})[0]
        client.write(DROP_QUERY, {"name": algo.projection})
        seconds = round(time.perf_counter() - start, 1)
        logger.info(f"{algo.name}: {stats} ({seconds} s)")
        results.append({"algorithm": algo.name, "projection": algo.projection, **size, **stats, "seconds": seconds})
    return results


def _table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["_(no rows)_"]
    header = list(rows[0])
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(row[h]) for h in header) + " |" for row in rows]
    return lines


def collect_findings(client: Neo4jClient) -> dict[str, list[dict]]:
    """The top-10 tables shown in the report."""
    return {
        "PageRank: most influential models": client.read(TOP_PAGERANK),
        "WCC: family sizes": client.read(FAMILY_SIZES),
        "WCC: largest families": client.read(TOP_FAMILIES),
        "Louvain: largest communities": client.read(TOP_COMMUNITIES),
        "Node Similarity: most similar pairs": client.read(TOP_SIMILAR),
        "Node Similarity: most similar pairs from different families": client.read(TOP_SIMILAR_OTHER_FAMILY),
        f"FastRP: nearest neighbours of {EMBEDDING_ANCHOR}": client.read(
            EMBEDDING_NEIGHBOURS, {"anchor": EMBEDDING_ANCHOR}
        ),
    }


def render_report(runs: list[dict], findings: dict[str, list[dict]], missing: int) -> str:
    """Markdown report: what ran, how long, and what it found."""
    keys = ["algorithm", "projection", "nodeCount", "relationshipCount", "seconds"]
    lines = [
        "# GDS report",
        "Generated by `make gds` (`scripts/graph_algorithms.py`). All results are DERIVED.",
        "## Runs",
        *_table([{k: run.get(k) for k in keys} for run in runs]),
        "",
        "Algorithm-specific numbers:",
        "",
        *[
            f"- **{run['algorithm']}**: "
            + ", ".join(f"{k} = {v}" for k, v in run.items() if k not in keys and k != "computeMillis")
            for run in runs
        ],
        "",
        f"Models missing a result: **{missing}** (must be 0).",
    ]
    for title, rows in findings.items():
        lines += ["", f"## {title}", *_table(rows)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns 1 if some model got no result."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--sample", action="store_true", help="write reports/gds_report_sample.md")
    args = parser.parse_args(argv)

    client = build_client(args.uri)
    try:
        runs = run_all(client)
        missing = client.read(MISSING_RESULTS)[0]["n"]
        report = render_report(runs, collect_findings(client), missing)
    finally:
        client.close()
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / ("gds_report_sample.md" if args.sample else "gds_report.md")).write_text(report, encoding="utf-8")
    if missing:
        logger.error(f"{missing} models have no GDS result")
        return 1
    logger.info("GDS results written on every model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
