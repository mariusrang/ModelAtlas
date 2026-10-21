"""Unit tests for Neo4jClient, using a mocked driver (no database needed)."""

from unittest.mock import MagicMock

import pytest
from neo4j import READ_ACCESS, WRITE_ACCESS
from neo4j.exceptions import ClientError, ServiceUnavailable

from app.database.neo4j_client import Neo4jClient
from app.errors import QueryTimeoutError, ServiceUnavailableError


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


def _error_with_code(code: str) -> ClientError:
    class _Error(ClientError):
        @property
        def code(self):
            return code

    return _Error("error from the server")


def test_query_timeout_becomes_504_error(settings):
    client, _, session = _client_with_rows(settings, [])
    session.run.side_effect = _error_with_code("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration")
    with pytest.raises(QueryTimeoutError) as caught:
        client.read("MATCH (m:Model) RETURN count(m)")
    assert caught.value.status_code == 504


def test_other_client_errors_are_not_hidden(settings):
    client, _, session = _client_with_rows(settings, [])
    session.run.side_effect = _error_with_code("Neo.ClientError.Statement.SyntaxError")
    with pytest.raises(ClientError):
        client.read("MATC")


def test_explain_reads_operators_and_the_biggest_estimate(settings):
    client, _, session = _client_with_rows(settings, [])
    session.run.return_value = MagicMock()
    session.run.return_value.consume.return_value.plan = {
        "operatorType": "ProduceResults@neo4j",
        "args": {"EstimatedRows": 1.0},
        "children": [{"operatorType": "CartesianProduct@neo4j", "args": {"EstimatedRows": 2.4e10}, "children": []}],
    }
    plan = client.explain("MATCH (a:Model), (b:Model) RETURN count(*)")
    assert plan == {"operators": ["ProduceResults", "CartesianProduct"], "estimated_rows": 2.4e10}
    assert session.run.call_args.args[0].text.startswith("EXPLAIN MATCH")
