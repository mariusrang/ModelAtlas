"""Overview: what is in the graph, which models the ecosystem relies on, how to read the app."""

import altair as alt
import pandas as pd
import streamlit as st

from ui.api_client import ApiError
from ui.components import EVIDENCE_HELP, RELATION_COLORS, TASKS, evidence_badge, human, model_url, section, show_error
from ui.data import fetch

CARDS = [
    (
        "/license",
        "⚖️ Can I ship it?",
        (
            "Checks the license of a model <b>and of every ancestor and training dataset</b>. A fine-tune can't "
            "drop its parent's restrictions."
        ),
    ),
    (
        "/model",
        "🌳 Where does it come from?",
        (
            "Family tree up to the foundation model, derived models, graph analytics and the parent our ML model "
            "guesses when the card says nothing."
        ),
    ),
    (
        "/ask",
        "🤖 Just ask",
        "A local LLM agent that calls graph tools, cites every fact it uses and refuses unsafe requests.",
    ),
]


def hero() -> None:
    """Title and the three things the app is for."""
    st.title("ModelAtlas")
    st.markdown(
        "A knowledge graph of the open AI model ecosystem: **156k Hugging Face models**, their family trees, "
        "licenses, training datasets and papers. Built for one question companies ask every day: "
        "*which open model can we actually use, and why?*"
    )
    for col, (url, title, text) in zip(st.columns(3), CARDS):
        col.markdown(
            f'<a href="{url}" target="_self" style="text-decoration:none;color:inherit">'
            f'<div class="ma-card"><h4>{title}</h4><div class="ma-muted">{text}</div></div></a>',
            unsafe_allow_html=True,
        )


def graph_size() -> None:
    """Node counts and the mix of lineage relationships."""
    section("What is in the graph", "VERIFIED")
    try:
        stats = fetch("/stats")
    except ApiError as error:
        show_error(error, "the graph statistics")
        return
    cols = st.columns(6)
    for col, (label, key) in zip(
        cols,
        [
            ("Models", "models"),
            ("Unlisted parents", "unlisted"),
            ("Organizations", "organizations"),
            ("Licenses", "licenses"),
            ("Datasets", "datasets"),
            ("Papers", "papers"),
        ],
    ):
        col.metric(label, human(stats.get(key)), help=f"{stats.get(key):,}" if stats.get(key) is not None else None)
    edges = pd.DataFrame([{"relation": name, "edges": count} for name, count in stats.get("lineage_edges", {}).items()])
    if not edges.empty:
        chart = (
            alt.Chart(edges)
            .mark_bar(cornerRadiusEnd=4)
            .encode(
                x=alt.X("edges:Q", title="lineage edges"),
                y=alt.Y("relation:N", sort="-x", title=None),
                color=alt.Color(
                    "relation:N",
                    legend=None,
                    scale=alt.Scale(domain=list(RELATION_COLORS), range=list(RELATION_COLORS.values())),
                ),
                tooltip=["relation", alt.Tooltip("edges:Q", format=",")],
            )
            .properties(height=170)
        )
        st.caption(f"{int(edges['edges'].sum()):,} declared parent → child links, by kind")
        st.altair_chart(chart, width="stretch")


def influential() -> None:
    """PageRank on the lineage graph."""
    section("The models the ecosystem is built on", "DERIVED")
    st.caption(
        "PageRank on the lineage graph (GDS): a model ranks high when many models derive from it, "
        "and those are themselves reused."
    )
    task = st.selectbox("Task", ["(all)"] + TASKS, key="influential_task")
    try:
        data = fetch("/graph/influential", task=None if task == "(all)" else task, limit=10)
    except ApiError as error:
        show_error(error, "the PageRank ranking")
        return
    rows = [
        {
            "model": model_url(r["model"]["id"]),
            "PageRank": r["pagerank"],
            "derived models": r.get("descendant_count"),
            "downloads": r["model"].get("downloads"),
            "license": r["model"].get("license"),
        }
        for r in data["results"]
    ]
    if not rows:
        st.info("No model for this task.")
        return
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "model": st.column_config.LinkColumn("model", display_text=r"id=(.*)$"),
            "PageRank": st.column_config.ProgressColumn(
                "PageRank", format="%.1f", min_value=0, max_value=max(r["PageRank"] for r in rows)
            ),
            "downloads": st.column_config.NumberColumn(format="compact"),
        },
    )


def legend() -> None:
    """How to read the evidence labels."""
    section("How to read this app")
    st.markdown(
        "Every fact carries a label, so a fact read in a model card is never mixed up with a guess.",
    )
    for label, text in EVIDENCE_HELP.items():
        st.markdown(f"{evidence_badge(label)} {text}", unsafe_allow_html=True)


def main() -> None:
    """Page body."""
    hero()
    st.write("")
    graph_size()
    left, right = st.columns([3, 2], gap="large")
    with left:
        influential()
    with right:
        legend()


main()
