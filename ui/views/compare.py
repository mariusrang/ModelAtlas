"""Compare two models: closest common ancestor, shortest lineage path, shared data."""

import streamlit as st

from ui.api_client import ApiError
from ui.components import connection_dot, evidence_badge, model_link, show_error
from ui.data import fetch


def main() -> None:
    """Page body."""
    st.title("Compare two models")
    st.markdown(
        "How are two models related? We look for their closest common ancestor and the shortest path between them."
    )
    left, right = st.columns(2)
    model_a = left.text_input("First model (exact id)", value="HuggingFaceH4/zephyr-7b-beta").strip()
    model_b = right.text_input("Second model (exact id)", value="teknium/OpenHermes-2.5-Mistral-7B").strip()
    depth = st.slider("Generations to explore", 1, 6, 4)
    if not model_a or not model_b:
        st.stop()
    try:
        data = fetch("/connection", a=model_a, b=model_b, depth=depth)
    except ApiError as error:
        show_error(error, "the connection")
        st.stop()
    st.markdown(f"{evidence_badge('DERIVED')} {data['summary']}", unsafe_allow_html=True)
    cols = st.columns(3)
    common = data.get("common_ancestor")
    cols[0].metric("Common ancestor", common["id"].split("/")[-1] if common else "none")
    cols[1].metric("Shared datasets", len(data.get("shared_datasets", [])))
    cols[2].metric("Same publisher", "yes" if data.get("same_organization") else "no")
    if common:
        above_a, above_b = common["distance_from_a"], common["distance_from_b"]
        st.markdown(
            f"Closest common ancestor: {model_link(common['id'])} "
            f"({above_a} generation(s) above the first, {above_b} above the second)."
        )
    st.graphviz_chart(connection_dot(data))
    if data.get("shared_datasets"):
        st.markdown("**Shared training data:** " + ", ".join(f"`{d}`" for d in data["shared_datasets"][:15]))
    if data.get("shared_papers"):
        st.markdown(
            "**Shared papers:** " + ", ".join(f"[{p}](https://arxiv.org/abs/{p})" for p in data["shared_papers"][:10])
        )


main()
