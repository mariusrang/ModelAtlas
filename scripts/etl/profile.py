"""Step 2 of the ETL: profile the raw model-card dump BEFORE we design the load.

Usage:
    python -m scripts.etl.profile            # reads data/raw/model_cards/*.parquet

Writes:
    reports/data_profile.md     human-readable report (goes in the README / report)
    reports/data_profile.json   the same numbers, machine-readable

Why profile first: the graph model and the scoping rule ("which models do we
keep?") must be based on real numbers, not guesses. For example, how many
models declare a parent, how deep family trees go, or how much of the dump is
zero-download spam.
"""

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from scripts.etl.raw import add_parsed_tags, load_metadata
from scripts.etl.sources import RAW_DIR, REPORTS_DIR

logger = logging.getLogger("modelatlas.profile")

DOWNLOAD_THRESHOLDS = (1, 10, 50, 100, 1000, 10000)
TOP_N = 20


def _top(counter: Counter, n: int = TOP_N) -> list[dict]:
    return [{"value": value, "count": int(count)} for value, count in counter.most_common(n)]


def _share(mask: pd.Series) -> float:
    return round(float(mask.mean()) * 100, 2) if len(mask) else 0.0


def overview(df: pd.DataFrame) -> dict:
    """Size, dates and duplicates."""
    return {
        "rows": int(len(df)),
        "unique_model_ids": int(df["modelId"].nunique()),
        "unique_authors": int(df["author"].nunique()),
        "created_min": str(df["createdAt"].min()),
        "created_max": str(df["createdAt"].max()),
    }


def popularity(df: pd.DataFrame) -> dict:
    """How skewed downloads and likes are (spoiler: very)."""
    quantiles = df["downloads"].quantile([0.5, 0.75, 0.9, 0.99, 0.999]).round(0)
    return {
        "downloads_quantiles": {str(q): int(v) for q, v in quantiles.items()},
        "share_zero_downloads_pct": _share(df["downloads"] == 0),
        "models_with_downloads_at_least": {str(t): int((df["downloads"] >= t).sum()) for t in DOWNLOAD_THRESHOLDS},
        "models_with_likes_at_least_1": int((df["likes"] >= 1).sum()),
        "top_models_by_downloads": df.nlargest(TOP_N, "downloads")[["modelId", "downloads", "likes"]].to_dict(
            "records"
        ),
    }


def _edges(df: pd.DataFrame) -> pd.DataFrame:
    rows = [
        (child, rel or "unspecified", parent) for child, ps in zip(df["modelId"], df["parents"]) for rel, parent in ps
    ]
    return pd.DataFrame(rows, columns=["child", "relation", "parent"])


def _max_depths(children_to_parents: dict[str, list[str]]) -> tuple[Counter, int]:
    """Depth of every node = longest chain of parents above it (iterative, cycle-safe)."""
    depth: dict[str, int] = {}
    cycles = 0
    for start in children_to_parents:
        if start in depth:
            continue
        stack, on_path = [(start, iter(children_to_parents.get(start, [])))], {start}
        while stack:
            node, parents = stack[-1]
            parent = next(parents, None)
            if parent is None:
                stack.pop()
                on_path.discard(node)
                depth[node] = 1 + max((depth.get(p, 0) for p in children_to_parents.get(node, [])), default=-1)
            elif parent in on_path:
                cycles += 1
            elif parent not in depth:
                on_path.add(parent)
                stack.append((parent, iter(children_to_parents.get(parent, []))))
    return Counter(depth.values()), cycles


def lineage(df: pd.DataFrame) -> dict:
    """Family-tree stats: who declares a parent, which kind, how deep trees go."""
    edges = _edges(df)
    known_ids = set(df["modelId"])
    parents_per_child = edges.groupby("child").size()
    children_to_parents = edges.groupby("child")["parent"].apply(list).to_dict()
    depth_histogram, cycles = _max_depths(children_to_parents)
    return {
        "models_with_parent_pct": _share(df["parents"].str.len() > 0),
        "edges": int(len(edges)),
        "relation_counts": {k: int(v) for k, v in edges["relation"].value_counts().items()},
        "children_with_several_parents": int((parents_per_child > 1).sum()),
        "dangling_parent_edges": int((~edges["parent"].isin(known_ids)).sum()),
        "self_loops": int((edges["child"] == edges["parent"]).sum()),
        "cycles_found": int(cycles),
        "depth_histogram": {str(k): int(v) for k, v in sorted(depth_histogram.items())},
        "top_parents_by_children": _top(Counter(edges["parent"])),
    }


def metadata_coverage(df: pd.DataFrame) -> dict:
    """How often each metadata field is filled, and its most common values."""

    def explode_counter(column: str) -> Counter:
        return Counter(value for values in df[column] for value in values)

    return {
        "license_missing_pct": _share(df["license"].isna()),
        "top_licenses": _top(Counter(df["license"].dropna())),
        "pipeline_tag_missing_pct": _share(df["pipeline_tag"].isna()),
        "top_pipeline_tags": _top(Counter(df["pipeline_tag"].dropna())),
        "top_libraries": _top(Counter(df["library_name"].dropna()), 15),
        "with_dataset_pct": _share(df["datasets"].str.len() > 0),
        "top_datasets": _top(explode_counter("datasets"), 15),
        "with_paper_pct": _share(df["papers"].str.len() > 0),
        "top_papers": _top(explode_counter("papers"), 10),
        "with_language_pct": _share(df["languages"].str.len() > 0),
        "top_languages": _top(explode_counter("languages"), 15),
        "params_estimable_pct": _share(df["params_b"].notna()),
        "top_other_tags": _top(explode_counter("other_tags"), 40),
    }


def card_quality(df: pd.DataFrame) -> dict:
    """Card text length and how many cards are the empty auto-generated template."""
    quantiles = df["card_length"].quantile([0.1, 0.5, 0.9]).round(0)
    return {
        "card_length_quantiles": {str(q): int(v) for q, v in quantiles.items()},
        "template_card_pct": _share(df["card_is_template"].astype(bool)),
    }


def authors(df: pd.DataFrame) -> dict:
    """Who publishes the most models, and who gets the most downloads."""
    by_author = df.groupby("author").agg(models=("modelId", "size"), downloads=("downloads", "sum"))
    return {
        "top_by_model_count": by_author.nlargest(15, "models").reset_index().to_dict("records"),
        "top_by_downloads": by_author.nlargest(15, "downloads").reset_index().to_dict("records"),
    }


def scope_options(df: pd.DataFrame) -> dict:
    """Graph size for different keep-rules, including the ancestors we must add back."""
    children_to_parents = _edges(df).groupby("child")["parent"].apply(list).to_dict()
    options = {}
    for threshold in (10, 50, 100, 500):
        kept = set(df.loc[(df["downloads"] >= threshold) | (df["likes"] >= 1), "modelId"])
        frontier, closure = list(kept), set(kept)
        while frontier:
            for parent in children_to_parents.get(frontier.pop(), []):
                if parent not in closure:
                    closure.add(parent)
                    frontier.append(parent)
        options[f"downloads>={threshold} or likes>=1"] = {"kept": len(kept), "with_ancestors": len(closure)}
    return options


def build_profile(df: pd.DataFrame) -> dict:
    """Run every section and return one JSON-friendly dict."""
    return {
        "overview": overview(df),
        "popularity": popularity(df),
        "lineage": lineage(df),
        "metadata": metadata_coverage(df),
        "cards": card_quality(df),
        "authors": authors(df),
        "scope_options": scope_options(df),
    }


def _table(rows: list[dict], columns: list[str]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(str(row.get(col, "")) for col in columns) + " |" for row in rows]
    return "\n".join(lines)


def render_markdown(profile: dict) -> str:
    """Turn the profile dict into a readable Markdown report."""
    ov, pop, lin, meta = profile["overview"], profile["popularity"], profile["lineage"], profile["metadata"]
    parts = [
        "# Data profile: Hugging Face model cards",
        "Generated by `python -m scripts.etl.profile`. Numbers come from the raw dump, before any cleaning.",
        "## Overview",
        _table([ov], list(ov)),
        "## Popularity",
        f"- {pop['share_zero_downloads_pct']}% of models have **zero** downloads.",
        "- Downloads quantiles: "
        + ", ".join(f"p{float(q) * 100:g} = {v}" for q, v in pop["downloads_quantiles"].items()),
        _table(
            [{"min downloads": k, "models": v} for k, v in pop["models_with_downloads_at_least"].items()],
            ["min downloads", "models"],
        ),
        "### Top models by downloads",
        _table(pop["top_models_by_downloads"], ["modelId", "downloads", "likes"]),
        "## Lineage (family trees)",
        f"- {lin['models_with_parent_pct']}% of models declare at least one parent ({lin['edges']} edges).",
        f"- {lin['children_with_several_parents']} models have several parents (merges).",
        f"- {lin['dangling_parent_edges']} edges point to a parent that is not in the dump.",
        f"- Self-loops: {lin['self_loops']}, cycles: {lin['cycles_found']}.",
        _table([{"relation": k, "edges": v} for k, v in lin["relation_counts"].items()], ["relation", "edges"]),
        "### Depth of family trees (longest chain of parents)",
        _table([{"depth": k, "models": v} for k, v in lin["depth_histogram"].items()], ["depth", "models"]),
        "### Most-used parents",
        _table(lin["top_parents_by_children"], ["value", "count"]),
        "## Metadata coverage",
        f"- License missing: {meta['license_missing_pct']}%, "
        f"pipeline tag missing: {meta['pipeline_tag_missing_pct']}%",
        f"- With dataset: {meta['with_dataset_pct']}%, with paper: {meta['with_paper_pct']}%, "
        f"with language: {meta['with_language_pct']}%, size guessable from name: {meta['params_estimable_pct']}%",
    ]
    for key, title in [
        ("top_licenses", "Licenses"),
        ("top_pipeline_tags", "Tasks (pipeline tags)"),
        ("top_libraries", "Libraries"),
        ("top_datasets", "Training datasets"),
        ("top_languages", "Languages"),
        ("top_papers", "Papers (arXiv)"),
        ("top_other_tags", "Other tags (architectures, formats, techniques)"),
    ]:
        parts += [f"### {title}", _table(meta[key], ["value", "count"])]
    parts += [
        "## Card quality",
        f"- {profile['cards']['template_card_pct']}% of cards are the empty auto-generated template.",
        "- Card length quantiles (characters): "
        + ", ".join(f"p{float(q) * 100:g} = {v}" for q, v in profile["cards"]["card_length_quantiles"].items()),
        "## Authors",
        _table(profile["authors"]["top_by_model_count"], ["author", "models", "downloads"]),
        _table(profile["authors"]["top_by_downloads"], ["author", "models", "downloads"]),
        "## Scoping options (how big would the graph be?)",
        _table([{"rule": k, **v} for k, v in profile["scope_options"].items()], ["rule", "kept", "with_ancestors"]),
    ]
    return "\n\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR / "model_cards")
    parser.add_argument("--out-dir", type=Path, default=REPORTS_DIR)
    args = parser.parse_args(argv)

    logger.info(f"loading {args.raw_dir}")
    df = add_parsed_tags(load_metadata(args.raw_dir))
    logger.info(f"{len(df)} models loaded, computing profile")
    profile = build_profile(df)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "data_profile.json").write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")
    (args.out_dir / "data_profile.md").write_text(render_markdown(profile), encoding="utf-8")
    logger.info(f"wrote {args.out_dir / 'data_profile.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
