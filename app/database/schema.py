"""Graph schema: uniqueness constraints and indexes.

Each index is here because a specific query needs it (see the comments); the
before/after effect is measured with PROFILE in docs/explain_profile.md.
Every statement uses IF NOT EXISTS, so applying the schema twice is harmless.
"""

from app.database.neo4j_client import Neo4jClient

# A uniqueness constraint also creates a range index on the key, so MERGE and
# MATCH by id are index seeks instead of full label scans.
CONSTRAINTS = [
    "CREATE CONSTRAINT model_id IF NOT EXISTS FOR (n:Model) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT organization_id IF NOT EXISTS FOR (n:Organization) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT license_id IF NOT EXISTS FOR (n:License) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT dataset_id IF NOT EXISTS FOR (n:Dataset) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT paper_id IF NOT EXISTS FOR (n:Paper) REQUIRE n.arxiv_id IS UNIQUE",
    "CREATE CONSTRAINT task_id IF NOT EXISTS FOR (n:Task) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT language_code IF NOT EXISTS FOR (n:Language) REQUIRE n.code IS UNIQUE",
    "CREATE CONSTRAINT architecture_id IF NOT EXISTS FOR (n:Architecture) REQUIRE n.id IS UNIQUE",
]

INDEXES = [
    # "most downloaded models for task X" and every ranking endpoint sort on these
    "CREATE INDEX model_downloads IF NOT EXISTS FOR (n:Model) ON (n.downloads)",
    "CREATE INDEX model_likes IF NOT EXISTS FOR (n:Model) ON (n.likes)",
    # recommendation filters: task first (most selective), then size
    "CREATE INDEX model_task IF NOT EXISTS FOR (n:Model) ON (n.task)",
    "CREATE INDEX model_params IF NOT EXISTS FOR (n:Model) ON (n.params_b)",
    # "families created after date X", temporal ML split
    "CREATE INDEX model_created IF NOT EXISTS FOR (n:Model) ON (n.created_at)",
    # ecosystem pages: biggest families first
    "CREATE INDEX model_descendants IF NOT EXISTS FOR (n:Model) ON (n.descendant_count)",
    # license-check groups licenses by family
    "CREATE INDEX license_family IF NOT EXISTS FOR (n:License) ON (n.family)",
    # search bar + the agent's entity resolver ("mistral 7b instruct" -> real ids)
    "CREATE FULLTEXT INDEX model_search IF NOT EXISTS FOR (n:Model) ON EACH [n.id, n.author]",
]


def apply_schema(client: Neo4jClient) -> int:
    """Create every constraint and index; return how many statements ran."""
    statements = CONSTRAINTS + INDEXES
    for statement in statements:
        client.write(statement)
    # wait until indexes are online, otherwise the first queries scan the labels
    client.write("CALL db.awaitIndexes(300)")
    return len(statements)
