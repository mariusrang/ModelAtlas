"""License check: can a company use this model commercially, given its whole lineage and training data?"""

from html import escape

import pandas as pd
import streamlit as st

from ui.api_client import ApiError, model_path
from ui.components import (
    STATUS_COLORS,
    VERDICTS,
    evidence_badge,
    license_paths,
    license_status,
    lineage_dot,
    model_link,
    model_picker,
    section,
    show_error,
)
from ui.data import fetch


def verdict_box(check: dict) -> None:
    """The answer, big and coloured, with its explanation."""
    css, title, icon = VERDICTS.get(check["verdict"], ("ma-review", check["verdict"], "🔎"))
    declared = (check.get("declared_license") or {}).get("id") or "no license declared"
    st.markdown(
        f'<div class="ma-verdict {css}"><h3>{icon} {escape(title)}</h3>'
        f"<div>{escape(check['explanation'])}</div>"
        f'<div class="ma-muted" style="margin-top:0.5rem">Declared license: <code>{escape(declared)}</code> '
        f"{evidence_badge('VERIFIED')} · verdict {evidence_badge('DERIVED')}</div></div>",
        unsafe_allow_html=True,
    )


def numbers(check: dict) -> None:
    """What was checked."""
    restricted = [
        d
        for d in check.get("training_data", [])
        if license_status(d.get("license"), d.get("commercial_use")) in ("not_allowed", "conditional")
    ]
    cols = st.columns(4)
    cols[0].metric("Ancestors checked", len(check.get("ancestors", [])))
    cols[1].metric("Stricter ancestors", len(check.get("conflicts", [])))
    cols[2].metric("Training datasets", len({d["id"] for d in check.get("training_data", [])}))
    cols[3].metric("Restricted datasets", len({d["id"] for d in restricted}))
    if check.get("incomplete_lineage"):
        st.warning(
            "Incomplete lineage: part of the family tree is not in the public dump (Unlisted) or is higher than "
            f"{check['depth_limit']} generations. The verdict can only be as good as what we can see."
        )


def tree(check: dict) -> None:
    """Family tree coloured by each model's license status."""
    section("Every license on the way up", "VERIFIED")
    model_id = check["model_id"]
    ancestors = check.get("ancestors", [])
    if not ancestors:
        st.caption("Root model: only its own license counts.")
        return
    fills = {a["id"]: STATUS_COLORS[license_status(a.get("license"), a.get("commercial_use"))] for a in ancestors}
    own = check.get("declared_license") or {}
    fills[model_id] = STATUS_COLORS[license_status(own.get("id"), own.get("commercial_use"))]
    st.graphviz_chart(lineage_dot(model_id, license_paths(model_id, ancestors), fills))
    legend = " ".join(
        f'<span style="background:{color};padding:0 0.4rem;border-radius:4px;color:#212529">{VERDICTS[name][1]}</span>'
        for name, color in STATUS_COLORS.items()
    )
    st.markdown(f'<span class="ma-muted">{legend}</span>', unsafe_allow_html=True)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ancestor": a["id"],
                    "generations up": a["distance"],
                    "license": a.get("license") or "none",
                    "family": a.get("family"),
                    "commercial use": str(a.get("commercial_use")),
                    "unlisted": a.get("unlisted", False),
                }
                for a in ancestors
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def conflicts(check: dict) -> None:
    """Ancestors with a stricter license than the one the model declares."""
    if not check.get("conflicts"):
        return
    section("License conflicts", "DERIVED")
    for conflict in check["conflicts"]:
        st.markdown(f"- ⚠️ {escape(conflict['reason'])}")


def training_data(check: dict) -> None:
    """Datasets used by the model and its ancestors, with their own license."""
    data = check.get("training_data", [])
    section("Training data on the lineage", "VERIFIED")
    if not data:
        st.caption("No training dataset declared on this lineage.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "dataset": d["id"],
                    "used by": d["used_by"],
                    "generations up": d["distance"],
                    "license": d.get("license") or ("unknown (no card)" if not d.get("listed") else "none"),
                    "status": (
                        VERDICTS[license_status(d.get("license"), d.get("commercial_use"))][1]
                        if d.get("license")
                        else "-"
                    ),
                }
                for d in data
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "A non-commercial dataset raises the verdict to *needs review*, not *not allowed*: the legal effect of "
        "training data licenses on a model is still unsettled."
    )


def main() -> None:
    """Page body."""
    st.title("License check")
    st.markdown(
        "Licenses on Hugging Face are **self-declared**, and a fine-tune or a quantized copy often declares a more "
        "permissive license than its parent. We walk the whole family tree and the training data, and keep the "
        "**most restrictive** status."
    )
    model_id = model_picker("main", "Model to check", default="TheBloke/Mistral-7B-Instruct-v0.2-GGUF")
    if not model_id:
        st.stop()
    st.query_params["id"] = model_id
    depth = st.slider("Generations to check", 1, 6, 6, key="license_depth")
    try:
        check = fetch(f"/models/{model_path(model_id)}/license-check", depth=depth)
    except ApiError as error:
        show_error(error, "the license check")
        st.stop()
    st.markdown(f"Model: {model_link(model_id)}")
    verdict_box(check)
    numbers(check)
    conflicts(check)
    tree(check)
    training_data(check)
    st.caption(check.get("disclaimer", ""))


main()
