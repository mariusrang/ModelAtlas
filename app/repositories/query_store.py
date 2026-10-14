"""Load Cypher queries from app/queries/*.cypher.

Keeping Cypher in .cypher files (not Python strings) means each query can be
read, reviewed, PROFILEd in Neo4j Browser and tested on its own.
"""

from functools import lru_cache
from pathlib import Path

from app.domain import LINEAGE_PATTERN

QUERIES_DIR = Path(__file__).resolve().parent.parent / "queries"


@lru_cache
def _raw(name: str) -> str:
    path = QUERIES_DIR / f"{name}.cypher"
    if not path.is_file():
        raise KeyError(f"unknown query: {name}")
    return path.read_text(encoding="utf-8")


def load_query(name: str, depth: int | None = None) -> str:
    """Return the query text with its tokens filled in.

    `__LINEAGE__` becomes the fixed list of lineage relationship types and
    `__DEPTH__` the traversal bound. Cypher can't take those two as
    parameters, so they are the only things we ever put in a query string,
    and `depth` must be a real int (never user text).
    """
    text = _raw(name).replace("__LINEAGE__", LINEAGE_PATTERN)
    if "__DEPTH__" in text:
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 < depth <= 12:
            raise ValueError(f"query {name} needs an int depth between 1 and 12, got {depth!r}")
        text = text.replace("__DEPTH__", str(depth))
    return text
