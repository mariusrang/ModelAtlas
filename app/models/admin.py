"""Schemas for the admin endpoints."""

from pydantic import BaseModel, Field


class LicenseRulesReport(BaseModel):
    """Result of re-applying config/licenses.toml to the graph."""

    rules: int = Field(description="Entries in config/licenses.toml")
    unknown_licenses: int = Field(description="License nodes still without a rule (family = unknown)")
