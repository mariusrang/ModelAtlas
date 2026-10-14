"""Application settings, read from environment variables (and `.env`).

We use pydantic-settings so every setting is typed and validated at startup:
a typo in `.env` fails fast instead of crashing in the middle of a request.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for the API, ETL and agent."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("change-me-please")
    neo4j_database: str = "neo4j"

    # API
    log_level: str = "INFO"
    admin_api_key: SecretStr = SecretStr("change-me-too")
    # Hard caps applied to every graph query, whatever the client asks for.
    max_page_size: int = Field(default=100, ge=1, le=1000)
    max_traversal_depth: int = Field(default=6, ge=1, le=12)
    query_timeout_seconds: float = Field(default=10.0, gt=0)

    # LLM
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    llm_timeout_seconds: float = 120.0


@lru_cache
def get_settings() -> Settings:
    """Return the settings singleton (cached so `.env` is read only once)."""
    return Settings()
