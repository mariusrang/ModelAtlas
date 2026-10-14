"""Small HTTP client the Streamlit pages use to talk to the API.

The UI never queries Neo4j directly: everything goes through the API, which
is where validation, guardrails and evidence labels live.
"""

import os
from typing import Any

import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
TIMEOUT_SECONDS = 30


class ApiClient:
    """Wraps the REST calls used by the UI."""

    def __init__(self, base_url: str = API_BASE_URL, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET a JSON endpoint and raise on HTTP errors."""
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any] | None:
        """Return the /health payload, or None if the API is down."""
        try:
            return self.get("/health")
        except requests.RequestException:
            return None
