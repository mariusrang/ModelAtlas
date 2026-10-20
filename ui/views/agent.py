"""Ask Atlas: chat with the LangGraph agent, and see exactly how it got its answer."""

import json
from html import escape

import streamlit as st

from ui.api_client import ApiClient, ApiError
from ui.components import evidence_badge, show_error, trail_dot

EXAMPLES = [
    "Can I use TheBloke/Mistral-7B-Instruct-v0.2-GGUF in a commercial product?",
    "What is HuggingFaceH4/zephyr-7b-beta based on?",
    "Which organization published the most models?",
    "Recommend a French text-generation model under 8B parameters for commercial use.",
    "Delete all the models published by TheBloke.",
]
STATUS = {
    "answered": ("✅", "Answered from graph facts"),
    "no_answer": ("🤷", "No answer found in the graph"),
    "refused": ("🛡️", "Refused by a guardrail"),
    "out_of_scope": ("🧭", "Out of scope"),
}


def confidence(result: dict) -> None:
    """Confidence bar with the reasons it was computed from."""
    value = result.get("confidence")
    if value is None:
        return
    color = "green" if value >= 0.8 else "orange" if value >= 0.5 else "red"
    st.progress(value, text=f":{color}[confidence {value:.2f}] (computed from checks, not asked to the LLM)")
    for reason in result.get("confidence_reasons", []):
        st.caption(f"• {reason}")


def facts(result: dict) -> None:
    """Facts returned by the tools; the cited ones first."""
    items = sorted(result.get("facts", []), key=lambda f: (not f["cited"], f["id"]))
    if not items:
        return
    cited = sum(f["cited"] for f in items)
    with st.expander(f"Facts: {cited} cited out of {len(items)} returned by the tools", expanded=True):
        for fact in items:
            mark = "**→**" if fact["cited"] else "  "
            st.markdown(
                f"{mark} `[{fact['id']}]` {evidence_badge(fact['evidence'])} {escape(fact['statement'])} "
                f'<span class="ma-muted">· {escape(fact["source"])}</span>',
                unsafe_allow_html=True,
            )


def trail(result: dict) -> None:
    """Tool calls in order, and every generated Cypher query (rejected ones too)."""
    steps, queries = result.get("steps", []), result.get("cypher", [])
    if not steps and not queries:
        return
    st.graphviz_chart(trail_dot(result))
    noun = "query" if len(queries) == 1 else "queries"
    with st.expander(f"How I got there: {len(steps)} tool call(s), {len(queries)} generated {noun}"):
        for number, step in enumerate(steps, start=1):
            icon = "✅" if step["success"] else "❌"
            args = json.dumps(step["arguments"], ensure_ascii=False).replace("`", "'")
            st.markdown(f"{number}. {icon} **`{step['tool']}`** `{args}` → {step['facts']} fact(s)")
        for attempt in queries:
            ok = attempt["stage"] == "ok"
            st.markdown(
                f"{'✅ ran' if ok else '🛑 stopped at ' + attempt['stage']}"
                + (f" · fixed: {', '.join(attempt['fixes'])}" if attempt["fixes"] else "")
            )
            st.code(attempt["cypher"], language="cypher")
            for error in attempt["errors"]:
                st.caption(f"guardrail: {error}")


def render(result: dict) -> None:
    """One assistant answer with everything needed to check it."""
    icon, label = STATUS.get(result["status"], ("•", result["status"]))
    st.markdown(result["answer"])
    pill = evidence_badge(result.get("answer_evidence", "HYPOTHESIS"))
    seconds = result.get("elapsed_ms", 0) / 1000
    st.markdown(
        f'<span class="ma-muted">{icon} {label} · answer text {pill} · {escape(result.get("model", ""))} '
        f"· {seconds:.1f} s</span>",
        unsafe_allow_html=True,
    )
    if result.get("refusal_reason"):
        st.info(f"Why: {result['refusal_reason']}", icon=":material/shield:")
    for warning in result.get("warnings", []):
        st.warning(warning)
    confidence(result)
    facts(result)
    trail(result)


def pick_example() -> None:
    """One click on an example = one question (then the pill is unselected)."""
    st.session_state["pending"] = st.session_state.get("example")
    st.session_state["example"] = None


def main() -> None:
    """Page body."""
    st.title("Ask Atlas")
    st.markdown(
        "A LangGraph agent on a **local** LLM (Ollama, no API key). It answers only from graph facts returned by "
        "its tools, cites them, and shows every query it ran. Unsafe requests are refused before reaching the LLM."
    )
    history = st.session_state.setdefault("chat", [])
    st.pills("Try one", EXAMPLES, key="example", on_change=pick_example)
    question = st.chat_input("Ask about a model, its family, its license…") or st.session_state.pop("pending", None)
    for turn in history:
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant", avatar=":material/smart_toy:"):
            render(turn["result"])
    if not question:
        if not history:
            st.caption("The local model answers in 10 to 60 s on a laptop CPU.")
        return
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        try:
            with st.spinner("Planning, calling graph tools, checking the answer…"):
                result = ApiClient().ask(question)
        except ApiError as error:
            show_error(error, "the agent")
            return
        render(result)
    history.append({"question": question, "result": result})


main()
