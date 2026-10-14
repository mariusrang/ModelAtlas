"""Build the whole graph: transform -> load -> validate.

Usage (from the repo root, with the stack running):
    python -m scripts.seed_data            # full dump   (make seed)
    python -m scripts.seed_data --sample   # small fixture, ~1 min (make seed-sample, used by CI)

The script talks to Neo4j from your laptop, so it uses bolt://localhost:7687
by default (inside docker compose the API uses bolt://neo4j:7687 instead).
"""

import argparse
import json
import logging
import os
import sys
import time

from app.config import Settings
from app.database.neo4j_client import Neo4jClient
from scripts.etl import transform
from scripts.etl.load import GraphLoader
from scripts.etl.sources import REPORTS_DIR
from scripts.etl.validate import render_report, validate

logger = logging.getLogger("modelatlas.seed")


def build_client(uri: str) -> Neo4jClient:
    """Neo4j client for batch jobs: same credentials as the API, longer timeout."""
    settings = Settings(neo4j_uri=uri, query_timeout_seconds=900)
    return Neo4jClient(settings)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns 1 if a validation check fails."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true", help="load the small test fixture")
    parser.add_argument("--uri", default=os.getenv("NEO4J_ETL_URI", "bolt://localhost:7687"))
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--skip-transform", action="store_true", help="reuse data/staged/ from a previous run")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    if not args.skip_transform:
        transform.main(["--sample"] if args.sample else [])
    manifest = json.loads((transform.STAGED_DIR / "manifest.json").read_text(encoding="utf-8"))

    client = build_client(args.uri)
    try:
        timings = GraphLoader(client, batch_size=args.batch_size).load_all(transform.STAGED_DIR)
        result = validate(client, manifest["row_counts"])
    finally:
        client.close()

    timings["total (transform + load + validate)"] = round(time.perf_counter() - started, 1)
    report_name = "load_report_sample.md" if args.sample else "load_report.md"
    (REPORTS_DIR / report_name).write_text(render_report(result, timings), encoding="utf-8")
    for failure in result.failures:
        logger.error(failure)
    status = "passed" if result.passed else "FAILED"
    logger.info(f"graph loaded: {result.labels.get('Model', 0):,} models, checks {status}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
