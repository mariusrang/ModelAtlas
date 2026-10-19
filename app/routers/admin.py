"""Admin endpoints: they write to the graph, so they need the X-API-Key header."""

from fastapi import APIRouter, Depends

from app.database.license_rules import apply_license_rules
from app.database.neo4j_client import Neo4jClient
from app.dependencies import get_db, require_admin_key
from app.models.admin import LicenseRulesReport

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


@router.post("/license-rules/reload", response_model=LicenseRulesReport, summary="Re-apply config/licenses.toml")
def reload_license_rules(db: Neo4jClient = Depends(get_db)) -> LicenseRulesReport:
    """Use it after editing config/licenses.toml: updates every License node in place,
    without reloading the whole graph (a full `make seed` takes ~10 minutes)."""
    return LicenseRulesReport(**apply_license_rules(db))
