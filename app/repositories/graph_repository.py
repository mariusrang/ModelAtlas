"""All reads the API does on the graph. One method = one .cypher file.

The repository returns plain dicts; turning them into API objects and adding
evidence labels is the job of the services.
"""

from app.database.neo4j_client import Neo4jClient
from app.repositories.query_store import load_query


class GraphRepository:
    """Thin layer between the services and Neo4j."""

    def __init__(self, db: Neo4jClient):
        self.client = db

    def _one(self, name: str, params: dict, depth: int | None = None) -> dict | None:
        rows = self.client.read(load_query(name, depth), params)
        return rows[0] if rows else None

    def _all(self, name: str, params: dict, depth: int | None = None) -> list[dict]:
        return self.client.read(load_query(name, depth), params)

    def stats(self) -> dict:
        """Node and relationship counts."""
        return self._one("stats", {}) or {}

    def search(self, query: str, task: str | None, limit: int) -> list[dict]:
        """Fulltext search, popularity-boosted."""
        return self._all("search_models", {"query": query, "task": task, "limit": limit})

    def model_detail(self, model_id: str) -> dict | None:
        """One model with its organization, license, datasets, papers..."""
        return self._one("model_detail", {"id": model_id})

    def lineage_up(self, model_id: str, depth: int, max_paths: int) -> dict | None:
        """Paths from a model up to its root models."""
        return self._one("lineage_up", {"id": model_id, "max_paths": max_paths}, depth)

    def descendants(self, model_id: str, depth: int, params: dict) -> list[dict]:
        """Derived models, keyset-paginated (params: relation, after_downloads, after_id, limit)."""
        return self._all("descendants", {"id": model_id, **params}, depth)

    def license_check(self, model_id: str, depth: int) -> dict | None:
        """Own license, every ancestor's license and the training datasets' licenses."""
        return self._one("license_check", {"ids": [model_id]}, depth)

    def license_check_many(self, model_ids: list[str], depth: int) -> list[dict]:
        """Same as license_check for a whole list, in a single query."""
        return self._all("license_check", {"ids": model_ids}, depth)

    def dataset_detail(self, dataset_id: str, limit: int) -> dict | None:
        """A dataset with its license, languages and top models trained on it."""
        return self._one("dataset_detail", {"id": dataset_id, "limit": limit})

    def connection(self, model_a: str, model_b: str, depth: int) -> dict | None:
        """Common ancestor, shortest path and shared datasets/papers of two models."""
        params = {"a": model_a, "b": model_b}
        return self._one("connection", params, depth) or self._one("connection_no_common", params, depth)

    def recommend_candidates(self, params: dict) -> list[dict]:
        """Models matching a client brief (task, language, size, license)."""
        return self._all("recommend_candidates", params)

    def organization_footprint(self, org_id: str, depth: int, limit: int) -> dict | None:
        """Own models + who builds on them."""
        return self._one("organization_footprint", {"id": org_id, "limit": limit}, depth)

    def influential(self, task: str | None, limit: int) -> list[dict]:
        """Top models by PageRank (written by `make gds`)."""
        return self._all("influential", {"task": task, "limit": limit})

    def similar_models(self, model_id: str, limit: int) -> dict | None:
        """Neighbours through SIMILAR_TO edges (Node Similarity)."""
        return self._one("similar_models", {"id": model_id, "limit": limit})

    def model_family(self, model_id: str, limit: int) -> dict | None:
        """WCC family, Louvain community and PageRank rank of a model."""
        return self._one("model_family", {"id": model_id, "limit": limit})

    def gds_ready(self) -> bool:
        """True once `make gds` has written its results."""
        return bool(self.client.read("MATCH (m:Model) WHERE m.pagerank IS NOT NULL RETURN 1 AS found LIMIT 1"))

    def model_exists(self, model_id: str) -> bool:
        """Cheap existence check (unique-constraint seek)."""
        return bool(self.client.read("MATCH (m:Model {id: $id}) RETURN 1 AS found", {"id": model_id}))
