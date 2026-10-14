"""Unit tests for Neo4jClient, using a mocked driver (no database needed)."""

from unittest.mock import MagicMock

import pytest
from neo4j import READ_ACCESS, WRITE_ACCESS
from neo4j.exceptions import ServiceUnavailable

from app.database.neo4j_client import Neo4jClient
from app.errors import ServiceUnavailableError


def _client_with_rows(settings, rows):
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value = [MagicMock(data=MagicMock(return_value=row)) for row in rows]
    return Neo4jClient(settings, driver=driver), driver, session


def test_read_uses_read_access_and_parameters(settings):
    client, driver, session = _client_with_rows(settings, [{"n": 1}])
    assert client.read("RETURN $n AS n", {"n": 1}) == [{"n": 1}]
    assert driver.session.call_args.kwargs["default_access_mode"] == READ_ACCESS
    query, params = session.run.call_args.args
    assert query.text == "RETURN $n AS n"
    assert query.timeout == settings.query_timeout_seconds
    assert params == {"n": 1}


def test_write_uses_write_access(settings):
    client, driver, _ = _client_with_rows(settings, [])
    client.write("CREATE (:X)")
    assert driver.session.call_args.kwargs["default_access_mode"] == WRITE_ACCESS


def test_unreachable_database_becomes_503_error(settings):
    driver = MagicMock()
    driver.session.side_effect = ServiceUnavailable("down")
    with pytest.raises(ServiceUnavailableError):
        Neo4jClient(settings, driver=driver).read("RETURN 1")


def test_ping(settings):
    driver = MagicMock()
    client = Neo4jClient(settings, driver=driver)
    assert client.ping() is True
    driver.verify_connectivity.side_effect = ServiceUnavailable("down")
    assert client.ping() is False


def test_close_is_idempotent(settings):
    driver = MagicMock()
    client = Neo4jClient(settings, driver=driver)
    client.close()
    client.close()
    driver.close.assert_called_once()
