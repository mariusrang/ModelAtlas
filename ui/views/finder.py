"""Model finder: a shortlist for a client brief (task, size, language, commercial use)."""

from html import escape

import streamlit as st

from ui.api_client import ApiClient, ApiError
from ui.components import TASKS, VERDICTS, badge, evidence_badge, human, model_url, show_error


def brief_form() -> dict | None:
    """The brief, as the API expects it (RecommendRequest)."""
    with st.form("brief"):
        left, middle, right = st.columns(3)
        task = left.selectbox("Task", TASKS, index=0)
        language = left.text_input("Language (ISO code, optional)", placeholder="fr, de, ja…")
        max_params = middle.select_slider(
            "Maximum size",
            options=[0.5, 1, 3, 8, 14, 32, 70, 0],
            value=8,
            format_func=lambda v: "no limit" if v == 0 else f"{v:g}B params",
        )
        min_downloads = middle.select_slider(
            "Minimum downloads", options=[0, 100, 1_000, 10_000, 100_000], value=1_000, format_func=human
        )
        commercial = right.toggle("Must be usable commercially", value=True)
        limit = right.slider("How many models", 3, 15, 5)
        submitted = st.form_submit_button("Find models", type="primary", icon=":material/search:")
    if not submitted:
        return None
    return {
        "task": task,
        "language": language.strip().lower() or None,
        "max_params_b": max_params or None,
        "min_downloads": min_downloads or None,
        "commercial_use": commercial,
        "limit": limit,
    }


def result_card(rank: int, rec: dict) -> None:
    """One recommended model with why it was picked."""
    model = rec["model"]
    verdict = rec.get("license_verdict")
    with st.container(border=True):
        left, right = st.columns([4, 1])
        left.markdown(f"**#{rank} [{model['id']}]({model_url(model['id'])})**")
        pills = [evidence_badge("DERIVED")]
        pills += [badge(v) for v in (model.get("task"), model.get("license")) if v]
        if model.get("params_b"):
            pills.append(badge(f"{model['params_b']:g}B"))
        if verdict:
            pills.append(badge(f"{VERDICTS[verdict][2]} {VERDICTS[verdict][1]}"))
        left.markdown(" ".join(pills), unsafe_allow_html=True)
        left.markdown("\n".join(f"- {escape(reason)}" for reason in rec.get("reasons", [])))
        right.metric("Score", f"{rec['score']:.2f}", help="log10(downloads+1) + 0.5·log10(likes+1)")
        right.caption(f"⬇ {human(model.get('downloads'))} · ♥ {human(model.get('likes'))}")


def main() -> None:
    """Page body."""
    st.title("Model finder")
    st.markdown(
        "Describe what the client needs. We filter on the graph, **check the license of every candidate's whole "
        "lineage in one query**, and rank what is left by popularity."
    )
    brief = brief_form()
    if brief is None:
        st.stop()
    try:
        with st.spinner("Searching the graph…"):
            data = ApiClient().post("/recommend", {k: v for k, v in brief.items() if v is not None})
    except ApiError as error:
        show_error(error, "the recommendation")
        st.stop()
    st.caption(f"{data['considered']:,} models matched the filters before ranking.")
    if not data["results"]:
        st.info("No model fits this brief. Try a bigger size limit or drop the language.")
    for rank, rec in enumerate(data["results"], start=1):
        result_card(rank, rec)


main()
