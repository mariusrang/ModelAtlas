"""Cached reads for the pages: the graph only changes when we reload it, so a
5-minute cache makes the app feel instant without hiding a reload for long."""

from typing import Any

import streamlit as st

from ui.api_client import ApiClient


@st.cache_data(ttl=300, show_spinner=False)
def fetch(path: str, **params: Any) -> dict[str, Any]:
    """GET through the API. Errors are raised (and never cached)."""
    return ApiClient().get(path, **params)
