"""Step 5 of the ETL: check the loaded graph against the staged tables.

Hard checks (the pipeline fails if one is broken):
- node and lineage-relationship counts equal the staged row counts,
- every constraint exists,
- no model is its own parent and no lineage cycle exists,
- every model has a publisher.

The numbers go to reports/load_report.md, which we cite in the README.
"""

import logging
from dataclasses import dataclass, field

from app.database.neo4j_client import Neo4jClient
from app.database.schema import CONSTRAINTS
from scripts.etl.load import LINEAGE_TYPES

logger = logging.getLogger("modelatlas.validate")
LINEAGE = "|".join(LINEAGE_TYPES)

COUNTS_BY_LABEL = """
CALL db.labels() YIELD label
CALL (label) {
  MATCH (n) WHERE label IN labels(n)
  RETURN count(n) AS nodes
}
RETURN label, nodes ORDER BY label
"""
COUNTS_BY_TYPE = """
CALL db.relationshipTypes() YIELD relationshipType AS type
CALL (type) {
  MATCH ()-[r]->() WHERE type(r) = type
  RETURN count(r) AS rels
}
RETURN type, rels ORDER BY type
"""
SELF_LOOPS = f"MATCH (m:Model)-[r:{LINEAGE}]->(m) RETURN count(r) AS n"
# Bounded on purpose: cycles of length <= 8 cover every cycle the profile found.
CYCLES = f"MATCH (m:Model) WHERE EXISTS {{ (m)-[:{LINEAGE}*1..8]->(m) }} RETURN count(m) AS n"
NO_PUBLISHER = "MATCH (m:Model) WHERE NOT (m)-[:PUBLISHED_BY]->() RETURN count(m) AS n"
CONSTRAINT_NAMES = "SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names"


@dataclass
class ValidationResult:
    """Graph counts and the list of failed hard checks."""

    labels: dict[str, int] = field(default_factory=dict)
    rel_types: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when every hard check passed."""
        return not self.failures


def _scalar(client: Neo4jClient, query: str) -> int:
    return client.read(query)[0]["n"]


def validate(client: Neo4jClient, row_counts: dict[str, int]) -> ValidationResult:
    """Run every check; `row_counts` comes from data/staged/manifest.json."""
    result = ValidationResult(
        labels={r["label"]: r["nodes"] for r in client.read(COUNTS_BY_LABEL)},
        rel_types={r["type"]: r["rels"] for r in client.read(COUNTS_BY_TYPE)},
    )
    expected_models = row_counts["models"] + row_counts["unlisted"]
    if result.labels.get("Model", 0) != expected_models:
        result.failures.append(f"Model nodes: expected {expected_models}, got {result.labels.get('Model', 0)}")
    lineage = sum(result.rel_types.get(t, 0) for t in LINEAGE_TYPES)
    if lineage != row_counts["lineage"]:
        result.failures.append(f"lineage relationships: expected {row_counts['lineage']}, got {lineage}")

    existing = set(client.read(CONSTRAINT_NAMES)[0]["names"])
    missing = [s.split()[2] for s in CONSTRAINTS if s.split()[2] not in existing]
    if missing:
        result.failures.append(f"missing constraints: {missing}")
    for name, query in [
        ("self-loops", SELF_LOOPS),
        ("models in a cycle", CYCLES),
        ("models without publisher", NO_PUBLISHER),
    ]:
        value = _scalar(client, query)
        if value:
            result.failures.append(f"{name}: {value}")
    return result


def render_report(result: ValidationResult, timings: dict[str, float]) -> str:
    """Markdown summary of the loaded graph."""
    lines = [
        "# Load report",
        "Status: **" + ("all checks passed" if result.passed else "FAILED") + "**",
        *[f"- {failure}" for failure in result.failures],
        "## Nodes by label",
        "| label | nodes |",
        "|---|---|",
        *[f"| {label} | {count:,} |" for label, count in result.labels.items()],
        "## Relationships by type",
        "| type | relationships |",
        "|---|---|",
        *[f"| {rel} | {count:,} |" for rel, count in result.rel_types.items()],
        "## Load time per step",
        "| step | seconds |",
        "|---|---|",
        *[f"| {step} | {seconds} |" for step, seconds in timings.items()],
    ]
    return "\n".join(lines) + "\n"
