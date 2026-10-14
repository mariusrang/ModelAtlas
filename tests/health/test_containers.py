"""Container health tests: run against the live docker compose stack.

Start the stack first (`make docker-run`), then `make test-integration`.
They are skipped automatically when the stack is not running, so `make test`
stays green on a laptop without Docker.
"""

import os

import pytest
import requests
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable

pytestmark = pytest.mark.integration

PROXY_URL = os.getenv("STACK_URL", "http://localhost")
NEO4J_URI = os.getenv("NEO4J_TEST_URI", "bolt://localhost:7687")


def _get(url: str):
    try:
        return requests.get(url, timeout=5)
    except requests.RequestException:
        pytest.skip(f"stack not running ({url} unreachable)")
    return None


def test_api_is_healthy_through_nginx():
    response = _get(f"{PROXY_URL}/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ui_is_served_through_nginx():
    response = _get(f"{PROXY_URL}/")
    assert response.status_code == 200


def test_neo4j_answers_cypher():
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        pytest.skip("NEO4J_PASSWORD not set")
    try:
        with GraphDatabase.driver(NEO4J_URI, auth=(os.getenv("NEO4J_USER", "neo4j"), password)) as driver:
            records, _, _ = driver.execute_query("RETURN 1 AS ok")
    except (ServiceUnavailable, AuthError) as exc:
        pytest.skip(f"neo4j not reachable: {exc}")
    assert records[0]["ok"] == 1


def test_gds_plugin_is_installed():
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        pytest.skip("NEO4J_PASSWORD not set")
    try:
        with GraphDatabase.driver(NEO4J_URI, auth=(os.getenv("NEO4J_USER", "neo4j"), password)) as driver:
            records, _, _ = driver.execute_query("RETURN gds.version() AS version")
    except (ServiceUnavailable, AuthError) as exc:
        pytest.skip(f"neo4j not reachable: {exc}")
    assert records[0]["version"]
