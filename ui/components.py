"""Pieces shared by every page: badges, formatting, graph drawings, the model picker.

The functions that build text (DOT graphs, badges, numbers) are plain Python, so
they are unit-tested without Streamlit.
"""

from html import escape
from urllib.parse import quote

import streamlit as st

from ui.api_client import ApiError
from ui.data import fetch

# ------------------------------------------------------------ look and feel

CSS = """
<style>
.block-container {padding-top: 2rem;}
.ma-badge {display:inline-block; padding:0.1rem 0.55rem; border-radius:999px; font-size:0.75rem;
  font-weight:600; letter-spacing:0.02em; margin-right:0.3rem; border:1px solid transparent;}
.ma-verified {background:rgba(43,138,62,0.14); color:#2b8a3e; border-color:rgba(43,138,62,0.35);}
.ma-derived {background:rgba(59,91,219,0.12); color:#3b5bdb; border-color:rgba(59,91,219,0.35);}
.ma-hypothesis {background:rgba(230,119,0,0.14); color:#d9480f; border-color:rgba(230,119,0,0.4);}
.ma-neutral {background:rgba(134,142,150,0.14); color:#5c636a; border-color:rgba(134,142,150,0.35);}
.ma-verdict {border-radius:14px; padding:1.1rem 1.3rem; margin:0.4rem 0 1rem 0; border:1px solid;}
.ma-verdict h3 {margin:0 0 0.3rem 0;}
.ma-ok {background:rgba(43,138,62,0.10); border-color:rgba(43,138,62,0.45);}
.ma-conditional {background:rgba(240,140,0,0.10); border-color:rgba(240,140,0,0.5);}
.ma-review {background:rgba(59,91,219,0.08); border-color:rgba(59,91,219,0.45);}
.ma-no {background:rgba(224,49,49,0.10); border-color:rgba(224,49,49,0.5);}
.ma-card {border:1px solid rgba(134,142,150,0.3); border-radius:14px; padding:1rem 1.1rem; min-height:10.5rem;}
.ma-card:hover {border-color:#3b5bdb; box-shadow:0 2px 12px rgba(59,91,219,0.12);}
.ma-card h4 {margin:0 0 0.4rem 0;}
.ma-muted {color:rgba(120,128,136,1); font-size:0.85rem;}
</style>
"""

EVIDENCE_HELP = {
    "VERIFIED": "Declared in a Hugging Face model card or dataset card",
    "DERIVED": "Computed by our rules or graph algorithms from verified facts",
    "HYPOTHESIS": "Guessed by our ML model or the LLM, always with a confidence",
}

# commercial_use_ok / conditional / needs_review / not_allowed -> (css class, title, icon)
VERDICTS = {
    "commercial_use_ok": ("ma-ok", "Commercial use looks OK", "✅"),
    "conditional": ("ma-conditional", "Allowed under conditions", "⚠️"),
    "needs_review": ("ma-review", "Needs a human review", "🔎"),
    "not_allowed": ("ma-no", "Commercial use not allowed", "⛔"),
}
STATUS_COLORS = {  # node fill in the license graph
    "commercial_use_ok": "#d3f9d8",
    "conditional": "#fff3bf",
    "needs_review": "#dbe4ff",
    "not_allowed": "#ffe3e3",
}
NODE_STYLE = (
    'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, fillcolor="#f1f3f5", color="#adb5bd"];'
)
RELATION_COLORS = {"finetune": "#3b5bdb", "adapter": "#2b8a3e", "quantized": "#e8590c", "merge": "#9c36b5"}
TASKS = [
    "text-generation",
    "text-classification",
    "token-classification",
    "translation",
    "summarization",
    "question-answering",
    "feature-extraction",
    "sentence-similarity",
    "fill-mask",
    "image-classification",
    "automatic-speech-recognition",
    "text-to-image",
]


def inject_css() -> None:
    """Add our small stylesheet (badges, verdict boxes, cards)."""
    st.markdown(CSS, unsafe_allow_html=True)


def evidence_badge(label: str) -> str:
    """HTML pill for VERIFIED / DERIVED / HYPOTHESIS."""
    css = {"VERIFIED": "ma-verified", "DERIVED": "ma-derived", "HYPOTHESIS": "ma-hypothesis"}.get(label, "ma-neutral")
    tip = escape(EVIDENCE_HELP.get(label, ""))
    return f'<span class="ma-badge {css}" title="{tip}">{escape(label)}</span>'


def badge(text: str) -> str:
    """Neutral HTML pill (task, license...)."""
    return f'<span class="ma-badge ma-neutral">{escape(str(text))}</span>'


def section(title: str, evidence: str | None = None) -> None:
    """A sub-header with its evidence label."""
    pill = evidence_badge(evidence) if evidence else ""
    st.markdown(f"#### {escape(title)} {pill}", unsafe_allow_html=True)


def human(number: int | float | None) -> str:
    """12345678 -> 12.3M, None -> '-'."""
    if number is None:
        return "-"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(number) >= limit:
            return f"{number / limit:.1f}{suffix}"
    return f"{number:g}" if isinstance(number, float) else str(number)


def model_url(model_id: str) -> str:
    """Link to the model page of this app (works behind Nginx and locally)."""
    return f"/model?id={quote(model_id, safe='/')}"


def model_link(model_id: str) -> str:
    """Markdown link to a model page."""
    return f"[{model_id}]({model_url(model_id)})"


def show_error(error: ApiError, what: str = "this") -> None:
    """Explain an API error in plain words."""
    if error.not_found:
        st.warning(f"Not found: {error.detail}")
    elif error.status == 503:
        st.info(f"Not available yet: {error.detail}")
    elif error.status is None:
        st.error(f"{error.detail}. Is the stack running (`make docker-run`)?")
    else:
        st.error(f"Could not load {what}: {error.detail}")


# ------------------------------------------------------------ graph drawings (Graphviz DOT)


def _node_id(model_id: str) -> str:
    return '"' + model_id.replace('"', "'") + '"'


def _label(model_id: str, downloads: int | None = None) -> str:
    org, _, name = model_id.partition("/")
    text = f"{name or org}\\n{org if name else ''}"
    if downloads:
        text += f"\\n⬇ {human(downloads)}"
    return text


def _collect(paths: list[dict]) -> tuple[dict[str, dict], dict[tuple[str, str], str], set[str]]:
    """Unique nodes, unique (child, parent) edges and the nodes where a path was cut."""
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], str] = {}
    cut: set[str] = set()
    for path in paths:
        chain = path["nodes"]
        for node in chain:
            nodes.setdefault(node["id"], node)
        for child, parent, relation in zip(chain, chain[1:], path["relations"]):
            edges[(child["id"], parent["id"])] = relation
        if not path.get("complete", True):
            cut.add(chain[-1]["id"])
    return nodes, edges, cut


def lineage_dot(model_id: str, paths: list[dict], fills: dict[str, str] | None = None) -> str:
    """Family tree above a model, child at the bottom, roots on top.

    `paths` come from /models/{id}/lineage (or are rebuilt from the license check).
    `fills` optionally colours each node (e.g. by license status).
    """
    fills = fills or {}
    nodes, edges, cut = _collect(paths)
    nodes.setdefault(model_id, {"id": model_id})
    lines = [
        "digraph lineage {",
        '  rankdir="BT"; bgcolor="transparent"; nodesep=0.35; ranksep=0.5;',
        f"  {NODE_STYLE}",
        '  edge [fontname="Helvetica", fontsize=9, arrowsize=0.7];',
    ]
    for node_id, node in nodes.items():
        attrs = [f'label="{_label(node_id, node.get("downloads"))}"']
        if node_id == model_id:
            attrs += ["penwidth=2.5", 'color="#3b5bdb"']
        if node.get("unlisted"):
            attrs.append('style="rounded,dashed"')
        if node_id in fills:
            attrs.append(f'fillcolor="{fills[node_id]}"')
        lines.append(f"  {_node_id(node_id)} [{', '.join(attrs)}];")
    for (child, parent), relation in edges.items():
        color = RELATION_COLORS.get(relation, "#868e96")
        lines.append(
            f'  {_node_id(child)} -> {_node_id(parent)} [label="{relation}", color="{color}", fontcolor="{color}"];'
        )
    for node_id in sorted(cut):
        more = _node_id(node_id + " (more)")
        lines.append(f'  {more} [label="… higher ancestors\\nnot shown", shape=plaintext, style=""];')
        lines.append(f"  {_node_id(node_id)} -> {more} [style=dotted, arrowhead=none];")
    lines.append("}")
    return "\n".join(lines)


def license_paths(model_id: str, ancestors: list[dict]) -> list[dict]:
    """Rebuild lineage paths from the license check's ancestors (each has its path and relations)."""
    paths = []
    for ancestor in ancestors:
        ids = ancestor.get("path") or [model_id, ancestor["id"]]
        nodes = [{"id": node_id, "unlisted": node_id == ancestor["id"] and ancestor.get("unlisted")} for node_id in ids]
        paths.append({"nodes": nodes, "relations": ancestor.get("relations", []), "complete": True})
    return paths


def license_status(license_id: str | None, commercial_use) -> str:
    """Same rule as the API (app/services/license.py), to colour the nodes."""
    if not license_id:
        return "needs_review"
    if commercial_use is True:
        return "commercial_use_ok"
    if commercial_use is False:
        return "not_allowed"
    if commercial_use == "conditional":
        return "conditional"
    return "needs_review"


def connection_dot(connection: dict) -> str:
    """Shortest lineage path between two models, both models highlighted."""
    path = connection.get("shortest_path")
    lines = [
        "graph connection {",
        '  rankdir="LR"; bgcolor="transparent";',
        f"  {NODE_STYLE}",
        '  edge [fontname="Helvetica", fontsize=9];',
    ]
    ends = {connection["model_a"], connection["model_b"]}
    common = (connection.get("common_ancestor") or {}).get("id")
    if path:
        for node in path["nodes"]:
            attrs = [f'label="{_label(node["id"], node.get("downloads"))}"']
            if node["id"] in ends:
                attrs += ["penwidth=2.5", 'color="#3b5bdb"']
            if node["id"] == common:
                attrs += ['fillcolor="#fff3bf"']
            lines.append(f"  {_node_id(node['id'])} [{', '.join(attrs)}];")
        for left, right, relation in zip(path["nodes"], path["nodes"][1:], path["relations"]):
            color = RELATION_COLORS.get(relation, "#868e96")
            lines.append(f'  {_node_id(left["id"])} -- {_node_id(right["id"])} [label="{relation}", color="{color}"];')
    else:
        for node_id in sorted(ends):
            lines.append(f'  {_node_id(node_id)} [label="{_label(node_id)}", penwidth=2.5, color="#3b5bdb"];')
    lines.append("}")
    return "\n".join(lines)


def trail_dot(result: dict) -> str:
    """The agent's path for one question: guardrail, tool calls, generated queries, checked answer."""
    lines = [
        "digraph trail {",
        '  rankdir="LR"; bgcolor="transparent";',
        f"  {NODE_STYLE}",
        '  edge [color="#868e96", arrowsize=0.7];',
        '  q [label="question", shape=oval, fillcolor="#e7f5ff"];',
    ]
    refused = result.get("status") == "refused"
    guard_fill = "#ffe3e3" if refused else "#d3f9d8"
    lines.append(f'  guard [label="input\\nguardrail", fillcolor="{guard_fill}"];')
    lines.append("  q -> guard;")
    previous = "guard"
    if refused:
        lines.append('  stop [label="refused\\nbefore any LLM call", fillcolor="#ffe3e3"];')
        lines.append("  guard -> stop;")
        lines.append("}")
        return "\n".join(lines)
    queries = iter(result.get("cypher", []))
    for number, step in enumerate(result.get("steps", []), start=1):
        fill = "#dbe4ff" if step["success"] else "#ffe3e3"
        lines.append(f'  t{number} [label="{number}. {step["tool"]}\\n{step["facts"]} fact(s)", fillcolor="{fill}"];')
        lines.append(f"  {previous} -> t{number};")
        previous = f"t{number}"
        if step["tool"] == "text_to_cypher":
            for attempt_no, attempt in enumerate(queries, start=1):
                ok = attempt["stage"] == "ok"
                fill = "#d3f9d8" if ok else "#fff3bf"
                text = "query ran" if ok else f"rejected at\\n{attempt['stage']}"
                lines.append(
                    f'  c{number}_{attempt_no} [label="Cypher #{attempt_no}\\n{text}", fillcolor="{fill}", fontsize=9];'
                )
                lines.append(f"  t{number} -> c{number}_{attempt_no} [style=dashed];")
                if ok:
                    break
    confidence = result.get("confidence")
    label = "answer\\n(cites facts)" if confidence is None else f"answer\\nconfidence {confidence:.2f}"
    lines.append(f'  a [label="{label}", shape=oval, fillcolor="#fff3bf"];')
    lines.append(f"  {previous} -> a;")
    lines.append("}")
    return "\n".join(lines)


# ------------------------------------------------------------ model picker


def model_picker(key: str, label: str = "Model", default: str = "") -> str | None:
    """Search box + result list. Accepts an exact id too, and ?id= in the URL.

    Returns the chosen model id, or None.
    """
    initial = st.query_params.get("id", default) if key == "main" else default
    text = st.text_input(label, value=initial, key=f"{key}_text", placeholder="e.g. mistral 7b instruct, or org/model")
    text = (text or "").strip()
    if not text:
        return None
    if "/" in text and " " not in text:
        return text  # looks like an exact id
    if len(text) < 2:
        return None
    try:
        hits = fetch("/models/search", q=text, limit=10)["hits"]
    except ApiError as error:
        show_error(error, "the search")
        return None
    if not hits:
        st.info("No model matches this search.")
        return None
    labels = {hit["model"]["id"]: f'{hit["model"]["id"]} · ⬇ {human(hit["model"].get("downloads"))}' for hit in hits}
    return st.selectbox("Pick one", list(labels), format_func=labels.get, key=f"{key}_pick")
