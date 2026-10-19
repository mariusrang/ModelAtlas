"""License rules (config/licenses.toml) applied to the License nodes.

Shared by the ETL (after a full load) and the admin endpoint (after someone
edits licenses.toml, without reloading the whole graph).
"""

import tomllib
from pathlib import Path

from app.database.neo4j_client import Neo4jClient

LICENSES_FILE = Path(__file__).resolve().parents[2] / "config" / "licenses.toml"

LICENSE_RULES_QUERY = """
UNWIND $rows AS row
MATCH (l:License {id: row.id})
SET l += row.props
"""
# Licenses with no entry in the file: the license check reports them as "needs a human".
UNKNOWN_FAMILY_QUERY = "MATCH (l:License) WHERE l.family IS NULL SET l.family = 'unknown'"
COUNT_UNKNOWN_QUERY = "MATCH (l:License {family: 'unknown'}) RETURN count(l) AS n"


def load_license_rules(path: Path = LICENSES_FILE) -> list[dict]:
    """Rows for LICENSE_RULES_QUERY: one {id, props} per entry of the TOML file."""
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return [{"id": entry["id"], "props": {k: v for k, v in entry.items() if k != "id"}} for entry in raw.values()]


def apply_license_rules(client: Neo4jClient, path: Path = LICENSES_FILE) -> dict[str, int]:
    """Write the rules on the License nodes; return how many rules and unknown licenses there are."""
    rows = load_license_rules(path)
    client.write(LICENSE_RULES_QUERY, {"rows": rows})
    client.write(UNKNOWN_FAMILY_QUERY)
    unknown = client.read(COUNT_UNKNOWN_QUERY)
    return {"rules": len(rows), "unknown_licenses": unknown[0]["n"] if unknown else 0}
