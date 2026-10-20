"""Small HTTP client the Streamlit pages use to talk to the API.

The UI never queries Neo4j directly: everything goes through the API, which
is where validation, guardrails and evidence labels live.
"""

import os
from typing import Any
from urllib.parse import quote

import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
TIMEOUT_SECONDS = 30
AGENT_TIMEOUT_SECONDS = 300  # the local LLM can take a while on a laptop CPU


class ApiError(Exception):
    """An API call that did not work, with the message the API gave us."""

    def __init__(self, status: int | None, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail

    @property
    def not_found(self) -> bool:
        """404: the model / dataset does not exist."""
        return self.status == 404

    @property
    def unavailable(self) -> bool:
        """API down, or a step not run yet (503: `make gds`, `make predict`, Ollama...)."""
        return self.status in (None, 503)


def model_path(model_id: str) -> str:
    """Model ids keep their slash in URLs: /models/mistralai/Mistral-7B-v0.1."""
    return quote(model_id.strip(), safe="/")


def _detail(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(item.get("msg", item)) for item in detail)
    return str(detail or body)


class ApiClient:
    """Wraps the REST calls used by the UI."""

    def __init__(self, base_url: str = API_BASE_URL, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def _send(self, method: str, path: str, timeout: float, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.session.request(method, f"{self.base_url}{path}", timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            raise ApiError(None, f"the API is not reachable at {self.base_url}") from exc
        if not response.ok:
            raise ApiError(response.status_code, _detail(response))
        return response.json()

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET a JSON endpoint (None parameters are left out)."""
        clean = {key: value for key, value in params.items() if value is not None}
        return self._send("GET", path, TIMEOUT_SECONDS, params=clean)

    def post(self, path: str, payload: dict[str, Any], timeout: float = TIMEOUT_SECONDS) -> dict[str, Any]:
        """POST a JSON body."""
        return self._send("POST", path, timeout, json=payload)

    def health(self) -> dict[str, Any] | None:
        """Return the /health payload, or None if the API is down."""
        try:
            return self.get("/health")
        except ApiError:
            return None

    def ask(self, question: str) -> dict[str, Any]:
        """Ask the agent (slow: no cache, long timeout)."""
        return self.post("/agent/ask", {"question": question}, timeout=AGENT_TIMEOUT_SECONDS)
