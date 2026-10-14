"""Organization footprint: who builds on an organization's models?"""

from app.config import Settings
from app.domain import relation_from_type
from app.errors import NotFoundError
from app.models.organization import Builder, FootprintResponse
from app.repositories.graph_repository import GraphRepository
from app.services.common import check_depth


class OrganizationService:
    """Read-only organization features."""

    def __init__(self, repo: GraphRepository, settings: Settings):
        self.repo = repo
        self.settings = settings

    def footprint(self, org_id: str, depth: int, limit: int) -> FootprintResponse:
        """Own models, derived models and the top organizations building on them."""
        row = self.repo.organization_footprint(org_id, check_depth(depth, self.settings), limit)
        if row is None:
            raise NotFoundError(f"organization '{org_id}' is not in the graph")
        return FootprintResponse(
            organization=org_id,
            own_models=row["own_models"],
            own_downloads=row["own_downloads"] or 0,
            derived_models=row["derived_models"] or 0,
            depth_limit=depth,
            top_builders=[Builder(**b) for b in row["builders"]],
            direct_children_by_kind={
                relation_from_type(c["rel_type"]): c["count"] for c in row["direct_children_by_kind"]
            },
        )
