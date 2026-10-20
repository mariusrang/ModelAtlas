"""ModelAtlas Streamlit app: navigation, sidebar and shared style.

Run with `make ui` (local) or through docker compose (served by Nginx on /).
Every page talks to the API only (ui/api_client.py), never to Neo4j.
"""

from pathlib import Path

import streamlit as st

from ui.api_client import ApiClient
from ui.components import inject_css

PAGES = Path(__file__).parent / "views"  # not "pages": Streamlit would build its own menu from it
REPO_URL = "https://github.com/mariusrang/ModelAtlas"


def sidebar_status() -> None:
    """API and Neo4j status, plus useful links."""
    health = ApiClient().health()
    with st.sidebar:
        if health is None:
            st.error("API not reachable", icon=":material/cloud_off:")
        else:
            deps = " · ".join(f"{d['name']} {'🟢' if d['healthy'] else '🔴'}" for d in health["dependencies"])
            st.caption(f"API {health['version']} 🟢 · {deps}")
        st.caption(f"[API docs (Swagger)](/api/docs) · [GitHub]({REPO_URL})")


def main() -> None:
    """Build the navigation and run the selected page."""
    st.set_page_config(page_title="ModelAtlas", page_icon=":material/hub:", layout="wide")
    inject_css()
    pages = {
        "Explore": [
            st.Page(PAGES / "overview.py", title="Overview", icon=":material/hub:", url_path="overview", default=True),
            st.Page(PAGES / "model.py", title="Model explorer", icon=":material/account_tree:", url_path="model"),
            st.Page(
                PAGES / "compare.py", title="Compare two models", icon=":material/compare_arrows:", url_path="compare"
            ),
        ],
        "Decide": [
            st.Page(PAGES / "license.py", title="License check", icon=":material/gavel:", url_path="license"),
            st.Page(PAGES / "finder.py", title="Model finder", icon=":material/manage_search:", url_path="finder"),
        ],
        "Ask": [
            st.Page(PAGES / "agent.py", title="Ask Atlas (agent)", icon=":material/smart_toy:", url_path="ask"),
        ],
    }
    page = st.navigation(pages)
    sidebar_status()
    page.run()


main()
