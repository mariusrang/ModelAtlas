"""Step 4 of the ETL: load the staged tables into Neo4j.

Design choices:
- `UNWIND $rows AS row MERGE ...` with batches of 5,000 rows: one round trip
  per batch instead of one per row, and MERGE makes the load idempotent
  (running it twice gives the same graph).
- every value goes through query parameters, never string formatting; the
  only formatted parts are labels and relationship types, which come from the
  fixed LINKS table below, never from the data.
- constraints are created BEFORE loading, so each MERGE is an index seek.
"""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.database.license_rules import LICENSE_RULES_QUERY, UNKNOWN_FAMILY_QUERY, load_license_rules
from app.database.neo4j_client import Neo4jClient
from app.database.schema import apply_schema
from app.domain import LINEAGE_TYPES

logger = logging.getLogger("modelatlas.load")


@dataclass(frozen=True)
class Link:
    """How one staged link table maps to the graph."""

    label: str
    key: str
    rel_type: str


# staged table name -> target node label, its key property, relationship type
LINKS = {
    "organization": Link("Organization", "id", "PUBLISHED_BY"),
    "license": Link("License", "id", "HAS_LICENSE"),
    "task": Link("Task", "id", "PERFORMS"),
    "dataset": Link("Dataset", "id", "TRAINED_ON"),
    "paper": Link("Paper", "arxiv_id", "CITES"),
    "language": Link("Language", "code", "SUPPORTS"),
    "architecture": Link("Architecture", "id", "HAS_ARCHITECTURE"),
}

MODEL_QUERY = """
UNWIND $rows AS row
MERGE (m:Model {id: row.id})
SET m.author = row.author,
    m.downloads = row.downloads,
    m.likes = row.likes,
    m.library = row.library,
    m.task = row.task,
    m.created_at = datetime(row.created_at),
    m.last_modified = datetime(row.last_modified),
    m.params_b = row.params_b,
    m.params_estimated = row.params_b IS NOT NULL,
    m.card_length = row.card_length,
    m.card_is_template = row.card_is_template,
    m.formats = row.formats,
    m.lineage_depth = row.lineage_depth,
    m.child_count = row.child_count,
    m.descendant_count = row.descendant_count,
    m.root_ids = row.root_ids
REMOVE m:Unlisted
"""

UNLISTED_QUERY = """
UNWIND $rows AS row
MERGE (m:Model {id: row.id})
ON CREATE SET m:Unlisted
SET m.author = row.author,
    m.lineage_depth = row.lineage_depth,
    m.child_count = row.child_count,
    m.descendant_count = row.descendant_count,
    m.root_ids = row.root_ids
"""

LINEAGE_QUERY = """
UNWIND $rows AS row
MATCH (child:Model {{id: row.child}})
MATCH (parent:Model {{id: row.parent}})
MERGE (child)-[r:{rel_type}]->(parent)
SET r.declared = row.declared, r.source = 'card_metadata'
"""

NODE_QUERY = "UNWIND $rows AS row MERGE (:{label} {{{key}: row.value}})"

LINK_QUERY = """
UNWIND $rows AS row
MATCH (m:Model {{id: row.model}})
MATCH (t:{label} {{{key}: row.value}})
MERGE (m)-[r:{rel_type}]->(t)
SET r.source = 'card_metadata'
"""

# Dataset nodes already exist (created from the model cards' dataset tags);
# the dataset cards only add properties and links to them.
DATASET_QUERY = """
UNWIND $rows AS row
MATCH (d:Dataset {id: row.id})
SET d.author = row.author,
    d.downloads = row.downloads,
    d.likes = row.likes,
    d.created_at = datetime(row.created_at),
    d.size_category = row.size_category,
    d.task_categories = row.task_categories,
    d.listed = true
"""

DATASET_LINK_QUERY = """
UNWIND $rows AS row
MATCH (d:Dataset {{id: row.dataset}})
MERGE (t:{label} {{{key}: row.value}})
MERGE (d)-[r:{rel_type}]->(t)
SET r.source = 'dataset_card'
"""

# staged table -> (label, key, relationship type) for the dataset links
DATASET_LINKS = {
    "dataset_license": Link("License", "id", "HAS_LICENSE"),
    "dataset_language": Link("Language", "code", "SUPPORTS"),
}

UNLISTED_DATASETS_QUERY = "MATCH (d:Dataset) WHERE d.listed IS NULL SET d.listed = false"

RESET_QUERY = "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS"


def _plain(value):
    """Convert numpy/pandas values into types the Neo4j driver accepts."""
    if isinstance(value, np.ndarray):
        return [_plain(v) for v in value.tolist()]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return value


def to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of plain dicts (NaN becomes None, arrays become lists)."""
    columns = list(df.columns)
    return [{col: _plain(val) for col, val in zip(columns, row)} for row in df.itertuples(index=False, name=None)]


def batches(rows: list[dict], size: int) -> Iterator[list[dict]]:
    """Split rows into chunks of `size`."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


class GraphLoader:
    """Loads staged tables into Neo4j in batches and keeps per-step timings."""

    def __init__(self, client: Neo4jClient, batch_size: int = 5000):
        self.client = client
        self.batch_size = batch_size
        self.timings: dict[str, float] = {}

    def run_batched(self, step: str, query: str, rows: list[dict]) -> int:
        """Send `rows` to `query` in batches; return the number of rows sent."""
        start = time.perf_counter()
        for chunk in batches(rows, self.batch_size):
            self.client.write(query, {"rows": chunk})
        self.timings[step] = round(time.perf_counter() - start, 2)
        logger.info(f"{step}: {len(rows):,} rows in {self.timings[step]} s")
        return len(rows)

    def reset(self) -> None:
        """Delete every node and relationship (constraints and indexes are kept)."""
        self.client.write(RESET_QUERY)

    def load_models(self, models: pd.DataFrame, unlisted: pd.DataFrame) -> None:
        """Model nodes first, then the parents that are not in the public dump."""
        self.run_batched("models", MODEL_QUERY, to_records(models))
        self.run_batched("unlisted models", UNLISTED_QUERY, to_records(unlisted))

    def load_lineage(self, lineage: pd.DataFrame) -> None:
        """One query per relationship type (types can't be query parameters)."""
        for rel_type in LINEAGE_TYPES:
            rows = to_records(lineage.loc[lineage["rel_type"] == rel_type, ["child", "parent", "declared"]])
            self.run_batched(f"lineage {rel_type}", LINEAGE_QUERY.format(rel_type=rel_type), rows)

    def load_link(self, name: str, table: pd.DataFrame) -> None:
        """Create the target nodes, then the (Model)->(target) relationships."""
        link = LINKS[name]
        values = table.iloc[:, 1].dropna().unique()
        node_rows = [{"value": _plain(v)} for v in values]
        self.run_batched(f"{link.label} nodes", NODE_QUERY.format(label=link.label, key=link.key), node_rows)
        rows = [{"model": m, "value": _plain(v)} for m, v in zip(table.iloc[:, 0], table.iloc[:, 1])]
        query = LINK_QUERY.format(label=link.label, key=link.key, rel_type=link.rel_type)
        self.run_batched(f"{link.rel_type} rels", query, rows)

    def load_datasets(self, staged_dir: Path) -> None:
        """Dataset card properties and links. Skipped if the transform found no dataset cards."""
        path = staged_dir / "datasets.parquet"
        if path.exists():
            self.run_batched("dataset cards", DATASET_QUERY, to_records(pd.read_parquet(path)))
            for name, link in DATASET_LINKS.items():
                table = pd.read_parquet(staged_dir / f"{name}.parquet")
                rows = [{"dataset": d, "value": _plain(v)} for d, v in zip(table.iloc[:, 0], table.iloc[:, 1])]
                query = DATASET_LINK_QUERY.format(label=link.label, key=link.key, rel_type=link.rel_type)
                self.run_batched(f"dataset {link.rel_type} rels", query, rows)
        self.client.write(UNLISTED_DATASETS_QUERY)

    def apply_license_rules(self) -> None:
        """Add family / commercial-use rules to License nodes, 'unknown' for the rest."""
        self.run_batched("license rules", LICENSE_RULES_QUERY, load_license_rules())
        self.client.write(UNKNOWN_FAMILY_QUERY)

    def load_all(self, staged_dir: Path, reset: bool = True) -> dict[str, float]:
        """Full load from data/staged/: schema, nodes, lineage, links, rules."""
        if reset:
            self.reset()
        apply_schema(self.client)
        self.load_models(
            pd.read_parquet(staged_dir / "models.parquet"), pd.read_parquet(staged_dir / "unlisted.parquet")
        )
        self.load_lineage(pd.read_parquet(staged_dir / "lineage.parquet"))
        for name in LINKS:
            self.load_link(name, pd.read_parquet(staged_dir / f"link_{name}.parquet"))
        self.load_datasets(staged_dir)
        self.apply_license_rules()  # last, so licenses that only datasets use get their rules too
        return self.timings
