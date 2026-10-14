"""Search, stats and model pages."""

import re

from app.domain import relation_from_type
from app.errors import BadRequestError, NotFoundError
from app.models.catalog import (
    LicenseInfo,
    ModelDetail,
    ParentRef,
    SearchHit,
    SearchResponse,
    StatsResponse,
)
from app.repositories.graph_repository import GraphRepository
from app.services.common import to_summary

# We never pass user text to Lucene as-is ("*", "OR", quotes... have a meaning
# there). We keep only words made of letters, digits, "_" and ".", then build
# the query ourselves.
_TOKEN = re.compile(r"[\w.]+", re.UNICODE)


def to_fulltext_query(text: str) -> str:
    """Turn free text into a safe Lucene query: every word must match, prefix allowed.

    "Mistral 7B instruct" -> "mistral* AND 7b* AND instruct*"
    A "." becomes "?" (any one char) because a wildcard term is not analysed.
    """
    words = [w.lower() for w in _TOKEN.findall(text)][:8]
    if not words:
        raise BadRequestError("search text must contain at least one letter or digit")
    return " AND ".join(f"{word.replace('.', '?')}*" for word in words)


class CatalogService:
    """Read-only catalog features."""

    def __init__(self, repo: GraphRepository):
        self.repo = repo

    def search(self, text: str, task: str | None, limit: int) -> SearchResponse:
        """Fulltext search boosted by popularity."""
        rows = self.repo.search(to_fulltext_query(text), task, limit)
        return SearchResponse(query=text, hits=[SearchHit(model=to_summary(r["model"]), rank=r["rank"]) for r in rows])

    def stats(self) -> StatsResponse:
        """Node and relationship counts."""
        row = self.repo.stats()
        return StatsResponse(
            **{k: row[k] for k in ("models", "unlisted", "organizations", "licenses", "datasets", "papers")},
            lineage_edges={k: row[k] for k in ("finetune", "adapter", "quantized", "merge")},
        )

    def detail(self, model_id: str) -> ModelDetail:
        """Model page; 404 if the id is unknown."""
        row = self.repo.model_detail(model_id)
        if row is None:
            raise NotFoundError(f"model '{model_id}' is not in the graph")
        model = row["model"]
        license_row = row["license"]
        return ModelDetail(
            model=to_summary({**model, "license": license_row["id"] if license_row else None}),
            organization=row["organization"],
            license_info=LicenseInfo(**license_row) if license_row else None,
            library=model.get("library"),
            created_at=model.get("created_at"),
            formats=model.get("formats") or [],
            datasets=row["datasets"],
            papers=row["papers"],
            languages=row["languages"],
            architectures=row["architectures"],
            parents=[
                ParentRef(
                    id=p["id"],
                    relation=relation_from_type(p["rel_type"]),
                    declared=bool(p["declared"]),
                    unlisted=p["unlisted"],
                )
                for p in row["parents"]
            ],
            lineage_depth=model.get("lineage_depth"),
            child_count=model.get("child_count"),
            descendant_count=model.get("descendant_count"),
            root_ids=model.get("root_ids") or [],
        )
