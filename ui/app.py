"""ModelAtlas Streamlit app, home page.

Run with `make ui` (local) or through docker compose (served by Nginx on /).
Feature pages (Explorer, License checker, Model finder, Ask Atlas) are added
in `ui/pages/` as the API endpoints land.
"""

import streamlit as st

from ui.api_client import ApiClient

st.set_page_config(page_title="ModelAtlas", page_icon="🧭", layout="wide")

st.title("ModelAtlas")
st.caption("A knowledge graph of the open AI model ecosystem, with an agent that shows its sources.")

client = ApiClient()
health = client.health()

if health is None:
    st.error("The API is not reachable. Is `docker compose` running?")
else:
    cols = st.columns(len(health["dependencies"]) + 1)
    cols[0].metric("API", health["status"].upper(), help=f"version {health['version']}")
    for col, dep in zip(cols[1:], health["dependencies"]):
        col.metric(dep["name"], "up" if dep["healthy"] else "down")

st.markdown("""
**Evidence labels used everywhere in this app**

| Label | Meaning |
| --- | --- |
| `VERIFIED` | Declared in a Hugging Face model card or registry |
| `DERIVED` | Computed by one of our rules from verified data |
| `HYPOTHESIS` | Guessed by our ML model or the LLM, always shown with a confidence |
""")
