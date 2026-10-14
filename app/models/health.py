"""Schemas for the health endpoint."""

from typing import Literal

from pydantic import BaseModel, Field


class DependencyStatus(BaseModel):
    """Status of one dependency (Neo4j, LLM...)."""

    name: str = Field(examples=["neo4j"])
    healthy: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    """Overall service health, used by Docker healthchecks and the UI."""

    status: Literal["ok", "degraded"]
    version: str
    dependencies: list[DependencyStatus]
