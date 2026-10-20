"""Model explorer: card facts, family tree, derived models, graph analytics and the predicted parent."""

import pandas as pd
import streamlit as st

from ui.api_client import ApiError, model_path
from ui.components import (
    RELATION_COLORS,
    badge,
    evidence_badge,
    human,
    lineage_dot,
    model_link,
    model_picker,
    model_url,
    section,
    show_error,
)
from ui.data import fetch

RELATIONS = ["all", "finetune", "adapter", "quantized", "merge"]


def header(detail: dict) -> None:
    """Name, badges and key numbers."""
    model = detail["model"]
    st.subheader(model["id"])
    pills = [evidence_badge("VERIFIED")]
    for value in (model.get("task"), model.get("license"), detail.get("library")):
        if value:
            pills.append(badge(value))
    if model.get("params_b"):
        pills.append(
            badge(f"{model['params_b']:g}B params{' (from the name)' if model.get('params_estimated') else ''}")
        )
    if model.get("unlisted"):
        pills.append(badge("unlisted: no public card"))
    st.markdown(" ".join(pills), unsafe_allow_html=True)
    cols = st.columns(5)
    cols[0].metric("Downloads", human(model.get("downloads")))
    cols[1].metric("Likes", human(model.get("likes")))
    cols[2].metric("Direct children", human(detail.get("child_count")))
    cols[3].metric("All descendants", human(detail.get("descendant_count")), help="DERIVED at load time")
    cols[4].metric("Generations above", human(detail.get("lineage_depth")), help="DERIVED: longest chain of parents")


def card_facts(detail: dict) -> None:
    """What the model card declares."""
    section("Declared in the model card", "VERIFIED")
    left, right = st.columns(2)
    with left:
        st.markdown(f"**Publisher:** {detail.get('organization') or '-'}")
        license_info = detail.get("license_info") or {}
        if license_info:
            st.markdown(
                f"**License:** `{license_info['id']}` ({license_info.get('family') or 'unknown family'}, "
                f"commercial use: `{license_info.get('commercial_use')}`)"
            )
        else:
            st.markdown("**License:** not declared")
        st.markdown(f"**Created:** {(detail.get('created_at') or '-')[:10]}")
        if detail.get("formats"):
            st.markdown("**Formats:** " + ", ".join(f"`{f}`" for f in detail["formats"]))
        if detail.get("architectures"):
            st.markdown("**Architecture:** " + ", ".join(f"`{a}`" for a in detail["architectures"]))
    with right:
        parents = detail.get("parents", [])
        st.markdown(
            "**Parents:** "
            + (", ".join(f"{model_link(p['id'])} ({p['relation']})" for p in parents) or "none (root model)")
        )
        st.markdown(
            "**Training data:** " + (", ".join(f"`{d}`" for d in detail.get("datasets", [])[:10]) or "not declared")
        )
        st.markdown(
            "**Papers:** "
            + (", ".join(f"[{p}](https://arxiv.org/abs/{p})" for p in detail.get("papers", [])[:6]) or "-")
        )
        st.markdown("**Languages:** " + (", ".join(detail.get("languages", [])[:15]) or "-"))


def family_tree(model_id: str) -> None:
    """Every path up to the foundation model(s)."""
    section("Family tree, up to the foundation model", "VERIFIED")
    depth = st.slider("Generations to walk up", 1, 6, 4, key="lineage_depth")
    try:
        lineage = fetch(f"/models/{model_path(model_id)}/lineage", depth=depth)
    except ApiError as error:
        show_error(error, "the lineage")
        return
    if not lineage["paths"]:
        st.success("This is a root model: its card declares no parent.")
        return
    st.graphviz_chart(lineage_dot(model_id, lineage["paths"]))
    legend = " ".join(f'<span style="color:{color}">■ {name}</span>' for name, color in RELATION_COLORS.items())
    st.markdown(f'<span class="ma-muted">{legend} · dashed = parent with no public card</span>', unsafe_allow_html=True)
    if lineage["truncated"]:
        st.warning(
            f"Incomplete lineage: some paths go higher than {lineage['depth_limit']} generations (or there are too "
            "many paths), so the roots shown may not be the real foundation models. Raise the slider to see more."
        )
    elif lineage["roots"]:
        st.markdown("**Foundation model(s):** " + ", ".join(model_link(r) for r in lineage["roots"]))


def descendants(model_id: str) -> None:
    """Models derived from this one, keyset-paginated."""
    section("Models built on it", "VERIFIED")
    relation = st.segmented_control("Kind", RELATIONS, default="all", key="desc_relation") or "all"
    depth = st.slider("Generations to walk down", 1, 4, 2, key="desc_depth")
    pages = st.session_state.setdefault("desc_pages", {})
    state_key = f"{model_id}|{relation}|{depth}"
    cursors = pages.setdefault(state_key, [None])
    items, next_cursor = [], None
    for cursor in cursors:
        try:
            page = fetch(
                f"/models/{model_path(model_id)}/descendants",
                depth=depth,
                relation=None if relation == "all" else relation,
                limit=20,
                cursor=cursor,
            )
        except ApiError as error:
            show_error(error, "the derived models")
            return
        items += page["items"]
        next_cursor = page["next_cursor"]
    if not items:
        st.info("No derived model in the graph.")
        return
    table = pd.DataFrame(
        [
            {
                "model": model_url(i["model"]["id"]),
                "kind": i["relation"],
                "generation": i["distance"],
                "downloads": i["model"].get("downloads"),
                "license": i["model"].get("license"),
            }
            for i in items
        ]
    )
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "model": st.column_config.LinkColumn("model", display_text=r"id=(.*)$"),
            "downloads": st.column_config.NumberColumn(format="compact"),
        },
    )
    if next_cursor and st.button("Load 20 more", key="desc_more"):
        cursors.append(next_cursor)
        st.rerun()
    st.caption(
        "Most downloaded first. Pagination is keyset-based (`next_cursor`), so page 50 costs the same as page 1."
    )


def analytics(model_id: str) -> None:
    """GDS results: family, community, PageRank, similar models."""
    section("Graph analytics", "DERIVED")
    try:
        family = fetch(f"/models/{model_path(model_id)}/family", limit=8)
    except ApiError as error:
        show_error(error, "the graph analytics")
        family = None
    if family:
        cols = st.columns(4)
        cols[0].metric("PageRank", f"{family['pagerank']:.2f}")
        cols[1].metric("Family size (WCC)", human(family["family_size"]))
        cols[2].metric("Rank in its family", f"#{family['rank_in_family']}")
        cols[3].metric("Sub-family (Louvain)", human(family["community_size"]))
        st.markdown(
            "**Most influential in its sub-family:** "
            + ", ".join(model_link(m["id"]) for m in family["top_members"][:8])
        )
    try:
        similar = fetch(f"/models/{model_path(model_id)}/similar", limit=8)
    except ApiError as error:
        show_error(error, "similar models")
        return
    st.markdown("**Trained on the same data** (Node Similarity, Jaccard on datasets and papers)")
    if similar["results"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "model": model_url(s["model"]["id"]),
                        "similarity": s["score"],
                        "shared datasets": s["shared_datasets"],
                        "shared papers": s["shared_papers"],
                    }
                    for s in similar["results"]
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "model": st.column_config.LinkColumn("model", display_text=r"id=(.*)$"),
                "similarity": st.column_config.ProgressColumn("similarity", min_value=0, max_value=1, format="%.2f"),
            },
        )
    else:
        st.caption(similar.get("note") or "No similar model.")


def predicted_parent(model_id: str) -> None:
    """Link prediction result, clearly labelled as a guess."""
    section("Probable parent (machine-learning guess)", "HYPOTHESIS")
    try:
        data = fetch(f"/models/{model_path(model_id)}/predicted-parents")
    except ApiError as error:
        show_error(error, "the predicted parent")
        return
    if not data["results"]:
        st.caption(data.get("note") or "No prediction for this model.")
        return
    for guess in data["results"]:
        st.markdown(
            f"{evidence_badge('HYPOTHESIS')} probably derived from **{model_link(guess['model']['id'])}**",
            unsafe_allow_html=True,
        )
        st.progress(guess["score"], text=f"model score {guess['score']:.2f}")
        st.caption(
            f"name overlap {guess.get('name_score')}, content similarity {guess.get('content_cosine')} · "
            f"{data.get('model_version')} · threshold chosen for ~80 % top-1 precision on unseen models"
        )


def main() -> None:
    """Page body."""
    st.title("Model explorer")
    model_id = model_picker("main", "Search a model", default="TheBloke/Mistral-7B-Instruct-v0.2-GGUF")
    if not model_id:
        st.stop()
    st.query_params["id"] = model_id
    try:
        detail = fetch(f"/models/{model_path(model_id)}")
    except ApiError as error:
        show_error(error, "this model")
        st.stop()
    header(detail)
    tabs = st.tabs(["Card facts", "Family tree", "Derived models", "Graph analytics", "Predicted parent"])
    with tabs[0]:
        card_facts(detail)
    with tabs[1]:
        family_tree(model_id)
    with tabs[2]:
        descendants(model_id)
    with tabs[3]:
        analytics(model_id)
    with tabs[4]:
        predicted_parent(model_id)


main()
