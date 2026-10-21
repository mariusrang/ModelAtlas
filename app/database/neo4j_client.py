"""Thin wrapper around the official Neo4j Python driver.

Why a wrapper:
- one place that opens/closes the driver (FastAPI lifespan),
- every query goes through `read()` / `write()` with parameters, so we never
  build Cypher by string concatenation (injection-safe, plan-cache friendly),
- read queries run in READ access mode, which also protects us from an
  LLM-generated query that tries to write.
"""

import logging
from typing import Any

from neo4j import READ_ACCESS, WRITE_ACCESS, Driver, GraphDatabase, Query
from neo4j.exceptions import ClientError, Neo4jError, ServiceUnavailable

from app.config import Settings
from app.errors import QueryTimeoutError, ServiceUnavailableError

logger = logging.getLogger(__name__)
# The server sends an INFO notification for every "IF NOT EXISTS" that had nothing
# to do; keep only real warnings in our logs.
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


def _is_timeout(exc: ClientError) -> bool:
    return "TransactionTimedOut" in (exc.code or "")


class Neo4jClient:
    """Owns the driver and exposes simple read/write helpers."""

    def __init__(self, settings: Settings, driver: Driver | None = None):
        self._settings = settings
        self._driver = driver

    @property
    def driver(self) -> Driver:
        """Create the driver lazily, the first time it is needed."""
        if self._driver is None:
            if not self._settings.neo4j_password.get_secret_value():
                raise ServiceUnavailableError("NEO4J_PASSWORD is not set: run `make env` and edit .env")
            self._driver = GraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password.get_secret_value()),
            )
        return self._driver

    def close(self) -> None:
        """Close the driver and its connection pool."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def ping(self) -> bool:
        """Return True if Neo4j answers, False otherwise (used by /health)."""
        try:
            self.driver.verify_connectivity()
            return True
        except (ServiceUnavailable, ServiceUnavailableError, Neo4jError, OSError) as exc:
            logger.warning(f"Neo4j ping failed: {exc}")
            return False

    def _run(self, cypher: str, params: dict[str, Any] | None, access_mode: str) -> list[dict[str, Any]]:
        query = Query(cypher, timeout=self._settings.query_timeout_seconds)
        try:
            with self.driver.session(
                database=self._settings.neo4j_database, default_access_mode=access_mode
            ) as session:
                result = session.run(query, params or {})
                return [record.data() for record in result]
        except ServiceUnavailable as exc:
            raise ServiceUnavailableError("Neo4j is not reachable") from exc
        except ClientError as exc:
            if _is_timeout(exc):
                raise QueryTimeoutError(
                    f"the query took more than {self._settings.query_timeout_seconds:g} s and was stopped"
                ) from exc
            raise

    def explain(self, cypher: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Plan a query without running it: the operators used and the biggest row estimate.

        Used by the agent to refuse LLM-written queries that would be too expensive
        (a cartesian product of two Model scans is 24 billion rows).
        """
        with self.driver.session(database=self._settings.neo4j_database, default_access_mode=READ_ACCESS) as session:
            summary = session.run(Query(f"EXPLAIN {cypher}"), params or {}).consume()
        operators: list[str] = []
        estimated = 0.0
        stack = [summary.plan] if summary.plan else []
        while stack:
            node = stack.pop()
            operators.append(str(node.get("operatorType", "")).split("@", maxsplit=1)[0])
            estimated = max(estimated, float(node.get("args", {}).get("EstimatedRows", 0) or 0))
            stack += node.get("children", [])
        return {"operators": operators, "estimated_rows": estimated}

    def read(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read-only query and return the records as dicts."""
        return self._run(cypher, params, READ_ACCESS)

    def write(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a write query (used by the ETL and admin jobs only)."""
        return self._run(cypher, params, WRITE_ACCESS)
