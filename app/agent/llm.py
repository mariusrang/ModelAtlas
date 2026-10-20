"""Minimal client for a local Ollama server (no API key, nothing leaves the machine).

We call the HTTP API directly instead of a framework wrapper: two methods are
all the agent needs, and a small class is easy to replace by a fake in tests.
"""

import json
import logging
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)


class ChatModel(Protocol):
    """What the agent needs from an LLM (Ollama in prod, a scripted fake in tests)."""

    name: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """One assistant message: {"content": str, "tool_calls": [{"name", "arguments"}]}."""

    def json(self, messages: list[dict], schema: dict) -> dict:
        """An answer constrained to a JSON schema (structured output)."""


class OllamaChat:
    """Ollama /api/chat with tool calling and JSON-schema output, temperature 0."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.name = settings.ollama_model
        self._url = settings.ollama_base_url.rstrip("/") + "/api/chat"
        self._client = client or httpx.Client(timeout=settings.llm_timeout_seconds)

    def _post(self, payload: dict[str, Any]) -> dict:
        payload = {
            "model": self.name,
            "stream": False,
            # temperature 0 = same question, same answer (easier to test and to trust)
            "options": {"temperature": 0, "num_ctx": 8192},
            **payload,
        }
        try:
            response = self._client.post(self._url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(f"Ollama call failed: {exc}")
            raise ServiceUnavailableError(
                f"the local LLM ({self.name}) is not reachable: run `make docker-run` and `make ollama-pull`"
            ) from exc
        return response.json().get("message", {})

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        message = self._post({"messages": messages, **({"tools": tools} if tools else {})})
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):  # some models send the arguments as a JSON string
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append({"name": function.get("name", ""), "arguments": arguments})
        return {"content": message.get("content", ""), "tool_calls": calls}

    def json(self, messages: list[dict], schema: dict) -> dict:
        message = self._post({"messages": messages, "format": schema})
        try:
            return json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            return {}
