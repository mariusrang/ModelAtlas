"""Small helpers shared by the services."""

from app.config import Settings
from app.errors import BadRequestError
from app.models.catalog import ModelSummary

SUMMARY_FIELDS = set(ModelSummary.model_fields)


def to_summary(row: dict) -> ModelSummary:
    """Build a ModelSummary from a Cypher map, ignoring extra keys."""
    return ModelSummary(**{key: value for key, value in row.items() if key in SUMMARY_FIELDS})


def check_depth(depth: int, settings: Settings) -> int:
    """Refuse traversals deeper than the configured cap (400 instead of a slow query)."""
    if depth > settings.max_traversal_depth:
        raise BadRequestError(f"depth {depth} is above the limit of {settings.max_traversal_depth}")
    return depth
