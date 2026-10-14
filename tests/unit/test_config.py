"""Unit tests for settings validation."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_safe():
    settings = Settings(_env_file=None)
    assert settings.max_traversal_depth <= 12
    assert settings.max_page_size <= 1000
    assert settings.llm_provider == "ollama"


def test_secrets_are_hidden_in_repr():
    settings = Settings(_env_file=None, neo4j_password="super-secret")
    assert "super-secret" not in repr(settings)
    assert settings.neo4j_password.get_secret_value() == "super-secret"


@pytest.mark.parametrize("depth", [0, 13])
def test_depth_cap_is_validated(depth):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_traversal_depth=depth)
