"""Family-tree features: lineage up, descendants down, connection between two models."""

import base64
import binascii
import json

from app.config import Settings
from app.domain import Relation, relation_from_type
from app.errors import BadRequestError, NotFoundError
from app.models.lineage import (
    CommonAncestor,
    ConnectionResponse,
    DescendantItem,
    DescendantPage,
    LineagePath,
    LineageResponse,
    PathNode,
)
from app.repositories.graph_repository import GraphRepository
from app.services.common import check_depth, to_summary

MAX_PATHS = 25


def encode_cursor(downloads: int, model_id: str) -> str:
    """Opaque keyset cursor: the sort key of the last row of a page."""
    raw = json.dumps({"d": downloads, "i": model_id}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[int, str]:
    """Inverse of encode_cursor; 400 on garbage."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return int(data["d"]), str(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise BadRequestError("invalid cursor") from exc


def to_path(nodes: list[dict | str], rel_types: list[str]) -> LineagePath:
    """Cypher path map -> LineagePath (nodes can be maps or plain ids)."""
    return LineagePath(
        nodes=[PathNode(**n) if isinstance(n, dict) else PathNode(id=n) for n in nodes],
        relations=[relation_from_type(t) for t in rel_types],
    )


class LineageService:
    """Traversal features; every depth is capped by settings.max_traversal_depth."""

    def __init__(self, repo: GraphRepository, settings: Settings):
        self.repo = repo
        self.settings = settings

    def lineage(self, model_id: str, depth: int) -> LineageResponse:
        """All paths from the model up to its roots."""
        row = self.repo.lineage_up(model_id, check_depth(depth, self.settings), MAX_PATHS + 1)
        if row is None:
            raise NotFoundError(f"model '{model_id}' is not in the graph")
        paths = [to_path(p["nodes"], p["rel_types"]) for p in row["paths"]]
        return LineageResponse(
            model_id=model_id,
            depth_limit=depth,
            paths=paths[:MAX_PATHS],
            roots=sorted({p.nodes[-1].id for p in paths}) or [model_id],  # a root is its own root
            truncated=len(paths) > MAX_PATHS,
        )

    def descendants(self, model_id: str, depth: int, relation: Relation | None, limit: int, cursor: str | None):
        """One page of models derived from model_id."""
        check_depth(depth, self.settings)
        after = decode_cursor(cursor) if cursor else (None, None)
        params = {
            "relation": relation.rel_type if relation else None,
            "after_downloads": after[0],
            "after_id": after[1],
            "limit": limit + 1,  # one extra row tells us if there is a next page
        }
        rows = self.repo.descendants(model_id, depth, params)
        if not rows and not self.repo.model_exists(model_id):
            raise NotFoundError(f"model '{model_id}' is not in the graph")
        page = rows[:limit]
        last = page[-1]["model"] if page else None
        return DescendantPage(
            model_id=model_id,
            items=[
                DescendantItem(
                    model=to_summary(r["model"]), distance=r["distance"], relation=relation_from_type(r["rel_type"])
                )
                for r in page
            ],
            next_cursor=encode_cursor(last["downloads"] or 0, last["id"]) if len(rows) > limit and last else None,
        )

    def connection(self, model_a: str, model_b: str, depth: int) -> ConnectionResponse:
        """Common ancestor, shortest lineage path and shared resources."""
        if model_a == model_b:
            raise BadRequestError("give two different models")
        row = self.repo.connection(model_a, model_b, check_depth(depth, self.settings))
        if row is None:
            raise NotFoundError(f"'{model_a}' or '{model_b}' is not in the graph")
        common = CommonAncestor(**row["common"]) if row["common"] else None
        path = row["shortest_path"]
        return ConnectionResponse(
            model_a=model_a,
            model_b=model_b,
            common_ancestor=common,
            shortest_path=to_path(path["nodes"], path["rel_types"]) if path else None,
            shared_datasets=row["shared_datasets"],
            shared_papers=row["shared_papers"],
            same_organization=row["same_organization"],
            summary=self._summary(model_a, model_b, common, row),
        )

    @staticmethod
    def _summary(model_a: str, model_b: str, common: CommonAncestor | None, row: dict) -> str:
        if common is None:
            base = f"No common ancestor within the depth limit for {model_a} and {model_b}."
        elif common.id == model_a:
            base = f"{model_a} is an ancestor of {model_b} ({common.distance_from_b} generations up)."
        elif common.id == model_b:
            base = f"{model_b} is an ancestor of {model_a} ({common.distance_from_a} generations up)."
        else:
            base = (
                f"Both descend from {common.id} ({common.distance_from_a} and "
                f"{common.distance_from_b} generations up)."
            )
        extras = []
        if row["shared_datasets"]:
            extras.append(f"{len(row['shared_datasets'])} shared training dataset(s)")
        if row["shared_papers"]:
            extras.append(f"{len(row['shared_papers'])} shared paper(s)")
        if row["same_organization"]:
            extras.append("same publisher")
        return base + (" They also have " + ", ".join(extras) + "." if extras else "")
