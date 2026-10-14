"""Step 3 of the ETL: turn the raw dump into clean node and relationship tables.

Usage:
    python -m scripts.etl.transform              # full dump  -> data/staged/
    python -m scripts.etl.transform --sample     # test fixture -> data/staged/

What happens, in order (each rule is justified by reports/data_profile.md):

1. keep popular models (`config/scope.toml`) and drop the auto-generated spam;
2. read the lineage from the tags and drop self-loops (a model citing itself);
3. add back every ancestor of a kept model, so family trees have no holes;
   parents that are not in the public dump become `Unlisted` nodes;
4. break the few remaining cycles (a graph of "derived from" must be a DAG);
5. precompute lineage depth, number of descendants and root models, because
   the API shows them on every model page and recomputing them per request
   would mean a full traversal each time;
6. build one table per relationship type (license, datasets, papers...).

Output: one Parquet file per table in data/staged/, plus manifest.json with
counts and everything we dropped, and reports/etl_report.md.
"""

import argparse
import json
import logging
import re
import sys
import tomllib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scripts.etl.raw import add_parsed_tags, load_metadata
from scripts.etl.sources import DATA_DIR, RAW_DIR, REPORTS_DIR, ROOT

logger = logging.getLogger("modelatlas.transform")

SCOPE_FILE = ROOT / "config" / "scope.toml"
STAGED_DIR = DATA_DIR / "staged"
SAMPLE_DIR = ROOT / "tests" / "fixtures" / "sample_models"

# Hub relation kind -> Neo4j relationship type (child -> parent).
RELATION_TYPES = {
    "finetune": "FINETUNED_FROM",
    "adapter": "ADAPTER_OF",
    "quantized": "QUANTIZED_FROM",
    "merge": "MERGED_FROM",
}
ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")


@dataclass(frozen=True)
class Scope:
    """Rules from config/scope.toml."""

    min_downloads: int
    min_likes: int
    add_ancestors: bool
    spam_tags: frozenset[str]
    template_papers: frozenset[str]
    fake_datasets: frozenset[str]
    fake_dataset_suffixes: tuple[str, ...]
    architectures: frozenset[str]
    formats: frozenset[str]

    @classmethod
    def load(cls, path: Path = SCOPE_FILE) -> "Scope":
        """Parse the TOML file."""
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        return cls(
            min_downloads=raw["keep"]["min_downloads"],
            min_likes=raw["keep"]["min_likes"],
            add_ancestors=raw["keep"]["add_ancestors"],
            spam_tags=frozenset(raw["drop"]["spam_tags"]),
            template_papers=frozenset(raw["drop"]["template_papers"]),
            fake_datasets=frozenset(raw["drop"]["fake_datasets"]),
            fake_dataset_suffixes=tuple(raw["drop"]["fake_dataset_suffixes"]),
            architectures=frozenset(raw["architectures"]["known"]),
            formats=frozenset(raw["formats"]["known"]),
        )


@dataclass
class StagedGraph:
    """Everything the loader needs, as plain DataFrames."""

    models: pd.DataFrame
    unlisted: pd.DataFrame
    lineage: pd.DataFrame
    links: dict[str, pd.DataFrame] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------- selection


def is_spam(other_tags: list[str], scope: Scope) -> bool:
    """True when a model carries one of the auto-generated spam tags."""
    return any(tag in scope.spam_tags for tag in other_tags)


def select_seeds(df: pd.DataFrame, scope: Scope) -> tuple[set[str], dict]:
    """Models we keep for their own sake (popular and not spam)."""
    spam = df["other_tags"].map(lambda tags: is_spam(tags, scope))
    popular = (df["downloads"] >= scope.min_downloads) | (df["likes"] >= scope.min_likes)
    seeds = set(df.loc[popular & ~spam, "modelId"])
    return seeds, {"spam_models_dropped": int(spam.sum()), "popular_models": int((popular & ~spam).sum())}


def lineage_edges(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Every declared (child, parent, relation) edge, without self-loops."""
    rows = [
        (child, parent, relation)
        for child, parents in zip(df["modelId"], df["parents"])
        for relation, parent in parents
    ]
    edges = pd.DataFrame(rows, columns=["child", "parent", "relation"]).drop_duplicates(["child", "parent"])
    self_loops = edges["child"] == edges["parent"]
    return edges[~self_loops].reset_index(drop=True), int(self_loops.sum())


def ancestors_closure(seeds: set[str], parents_of: dict[str, list[str]]) -> set[str]:
    """Seeds plus every ancestor reachable through lineage edges."""
    closure, frontier = set(seeds), list(seeds)
    while frontier:
        for parent in parents_of.get(frontier.pop(), []):
            if parent not in closure:
                closure.add(parent)
                frontier.append(parent)
    return closure


def break_cycles(nodes: set[str], parents_of: dict[str, list[str]]) -> set[tuple[str, str]]:
    """Return the (child, parent) edges to drop so that lineage becomes a DAG.

    Iterative depth-first search: an edge that points back to a node still on
    the current path closes a cycle, so we drop it. The profile found ~3 real
    cycles in 400k edges, so this removes almost nothing, but it guarantees
    that "walk up to the root" queries always terminate.
    """
    state: dict[str, int] = {}  # 1 = on current path, 2 = done
    removed: set[tuple[str, str]] = set()
    for start in nodes:
        if start in state:
            continue
        state[start] = 1
        stack = [(start, iter(parents_of.get(start, [])))]
        while stack:
            node, parents = stack[-1]
            parent = next(parents, None)
            if parent is None:
                state[node] = 2
                stack.pop()
            elif state.get(parent) == 1:
                removed.add((node, parent))
            elif parent not in state:
                state[parent] = 1
                stack.append((parent, iter(parents_of.get(parent, []))))
    return removed


# ---------------------------------------------------------------- metrics


def _depths(nodes: set[str], parents_of: dict[str, list[str]]) -> dict[str, int]:
    """Longest chain of parents above each node (0 for a root). Needs a DAG."""
    depth: dict[str, int] = {}
    for start in nodes:
        stack = [start]
        while stack:
            node = stack[-1]
            if node in depth:
                stack.pop()
                continue
            pending = [p for p in parents_of.get(node, []) if p not in depth]
            if pending:
                stack.extend(pending)
            else:
                depth[node] = 1 + max((depth[p] for p in parents_of.get(node, [])), default=-1)
                stack.pop()
    return depth


def _descendant_counts(nodes: set[str], children_of: dict[str, list[str]]) -> dict[str, int]:
    """Number of distinct descendants of each node (BFS from every parent).

    Looks expensive but isn't: the total work is the sum over all models of
    their number of ancestors, and the profile shows most chains are short.
    """
    counts = {}
    for node in nodes:
        if node not in children_of:
            counts[node] = 0
            continue
        seen, queue = set(), deque(children_of[node])
        while queue:
            child = queue.popleft()
            if child not in seen:
                seen.add(child)
                queue.extend(children_of.get(child, []))
        counts[node] = len(seen)
    return counts


def _roots(nodes: set[str], parents_of: dict[str, list[str]], limit: int = 5) -> dict[str, list[str]]:
    """Foundation models (no parent) each node descends from, capped at `limit`."""
    memo: dict[str, frozenset[str]] = {}
    for start in nodes:
        stack = [start]
        while stack:
            node = stack[-1]
            if node in memo:
                stack.pop()
                continue
            parents = parents_of.get(node, [])
            pending = [p for p in parents if p not in memo]
            if pending:
                stack.extend(pending)
            else:
                memo[node] = frozenset().union(*(memo[p] for p in parents)) if parents else frozenset({node})
                stack.pop()
    return {node: sorted(memo[node])[:limit] for node in nodes}


def lineage_metrics(nodes: set[str], edges: pd.DataFrame) -> pd.DataFrame:
    """Depth, child count, descendant count and roots for every node."""
    parents_of = edges.groupby("child")["parent"].apply(list).to_dict()
    children_of = edges.groupby("parent")["child"].apply(list).to_dict()
    depth = _depths(nodes, parents_of)
    descendants = _descendant_counts(nodes, children_of)
    roots = _roots(nodes, parents_of)
    return pd.DataFrame(
        {
            "id": sorted(nodes),
            "lineage_depth": [depth[n] for n in sorted(nodes)],
            "child_count": [len(children_of.get(n, [])) for n in sorted(nodes)],
            "descendant_count": [descendants[n] for n in sorted(nodes)],
            "root_ids": [roots[n] for n in sorted(nodes)],
        }
    )


# ---------------------------------------------------------------- tables


def _iso(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value)


def model_table(df: pd.DataFrame, metrics: pd.DataFrame, scope: Scope) -> pd.DataFrame:
    """One row per Model node, with the properties we store on it."""
    models = pd.DataFrame(
        {
            "id": df["modelId"],
            "author": df["author"],
            "downloads": df["downloads"],
            "likes": df["likes"],
            "library": df["library_name"],
            "task": df["pipeline_tag"],
            "created_at": df["createdAt"].map(_iso),
            "last_modified": df["last_modified"].map(_iso) if "last_modified" in df else None,
            "params_b": df["params_b"],
            "card_length": df["card_length"],
            "card_is_template": df["card_is_template"],
            "formats": df["other_tags"].map(lambda tags: sorted({t for t in tags if t in scope.formats})),
        }
    )
    return models.merge(metrics, on="id", how="left")


def _explode(df: pd.DataFrame, column: str, target: str) -> pd.DataFrame:
    out = df[["modelId", column]].explode(column).dropna()
    return out.rename(columns={"modelId": "model", column: target}).drop_duplicates().reset_index(drop=True)


def link_tables(df: pd.DataFrame, scope: Scope) -> tuple[dict[str, pd.DataFrame], dict]:
    """(model -> X) tables for every non-lineage relationship type."""
    datasets = _explode(df, "datasets", "dataset")
    fake = datasets["dataset"].str.lower().isin(scope.fake_datasets) | datasets["dataset"].str.lower().str.endswith(
        scope.fake_dataset_suffixes
    )
    papers = _explode(df, "papers", "paper")
    bad_papers = papers["paper"].isin(scope.template_papers) | ~papers["paper"].str.match(ARXIV_ID)
    df = df.assign(architectures=df["other_tags"].map(lambda tags: [t for t in tags if t in scope.architectures]))
    links = {
        "organization": df[["modelId", "author"]].rename(columns={"modelId": "model", "author": "organization"}),
        "license": df[["modelId", "license"]].dropna().rename(columns={"modelId": "model"}),
        "task": df[["modelId", "pipeline_tag"]].dropna().rename(columns={"modelId": "model", "pipeline_tag": "task"}),
        "dataset": datasets[~fake].reset_index(drop=True),
        "paper": papers[~bad_papers].reset_index(drop=True),
        "language": _explode(df, "languages", "language"),
        "architecture": _explode(df, "architectures", "architecture"),
    }
    stats = {"fake_dataset_links_dropped": int(fake.sum()), "template_paper_links_dropped": int(bad_papers.sum())}
    return links, stats


def lineage_table(edges: pd.DataFrame) -> pd.DataFrame:
    """Lineage edges with their Neo4j relationship type and a `declared` flag.

    The Hub types almost every edge (60 untyped out of 398k in the profile);
    untyped ones are stored as FINETUNED_FROM with declared=false so the
    information "we guessed the kind" is never lost.
    """
    table = edges.copy()
    table["declared"] = table["relation"].notna()
    table["rel_type"] = table["relation"].map(RELATION_TYPES).fillna("FINETUNED_FROM")
    return table[["child", "parent", "rel_type", "declared"]]


# ---------------------------------------------------------------- pipeline


def select_nodes(df: pd.DataFrame, scope: Scope, stats: dict) -> tuple[set[str], pd.DataFrame]:
    """Steps 1 to 4: which models are in the graph, and the clean lineage edges between them."""
    seeds, seed_stats = select_seeds(df, scope)
    stats.update(seed_stats)
    edges, stats["self_loops_dropped"] = lineage_edges(df)
    parents_of = edges.groupby("child")["parent"].apply(list).to_dict()
    nodes = ancestors_closure(seeds, parents_of) if scope.add_ancestors else seeds
    edges = edges[edges["child"].isin(nodes) & edges["parent"].isin(nodes)]

    removed = break_cycles(nodes, edges.groupby("child")["parent"].apply(list).to_dict())
    if removed:
        pairs = pd.Series(list(zip(edges["child"], edges["parent"])), index=edges.index)
        edges = edges[~pairs.isin(removed)]
    stats["cycle_edges_dropped"] = len(removed)
    stats["seeds"] = len(seeds)
    return nodes, edges


def build_graph(df: pd.DataFrame, scope: Scope) -> StagedGraph:
    """Run the whole transform on an already-parsed DataFrame."""
    stats: dict = {"raw_models": int(len(df))}
    nodes, edges = select_nodes(df, scope, stats)

    kept = df[df["modelId"].isin(nodes)].reset_index(drop=True)
    unlisted_ids = sorted(nodes - set(df["modelId"]))
    unlisted = pd.DataFrame({"id": unlisted_ids, "author": [i.split("/", 1)[0] for i in unlisted_ids]})

    metrics = lineage_metrics(nodes, edges)
    links, link_stats = link_tables(kept, scope)
    # parents that are not in the dump still have a publisher (the id prefix)
    links["organization"] = pd.concat(
        [links["organization"], unlisted.rename(columns={"id": "model", "author": "organization"})],
        ignore_index=True,
    )
    stats.update(link_stats)
    stats.update(
        {
            "models_kept": int(len(kept)),
            "ancestors_added": int(len(nodes) - stats.pop("seeds") - len(unlisted)),
            "unlisted_parents": int(len(unlisted)),
            "lineage_edges": int(len(edges)),
        }
    )
    return StagedGraph(
        models=model_table(kept, metrics, scope),
        unlisted=unlisted.merge(metrics, on="id", how="left"),
        lineage=lineage_table(edges),
        links=links,
        stats=stats,
    )


def write_staged(graph: StagedGraph, out_dir: Path, source: str) -> dict:
    """Write every table to Parquet plus a manifest with counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {"models": graph.models, "unlisted": graph.unlisted, "lineage": graph.lineage}
    tables.update({f"link_{name}": table for name, table in graph.links.items()})
    for name, table in tables.items():
        table.to_parquet(out_dir / f"{name}.parquet", index=False)
    manifest = {
        "source": source,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "row_counts": {name: int(len(table)) for name, table in tables.items()},
        "stats": graph.stats,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def render_report(manifest: dict) -> str:
    """Markdown summary of what the transform kept and dropped."""
    stats, counts = manifest["stats"], manifest["row_counts"]
    lines = [
        "# ETL report: transform step",
        f"Source: `{manifest['source']}`, built {manifest['built_at']}.",
        "## What we kept and dropped",
        "| step | count |",
        "|---|---|",
        *[f"| {key.replace('_', ' ')} | {value:,} |" for key, value in stats.items()],
        "## Staged tables",
        "| table | rows |",
        "|---|---|",
        *[f"| {name} | {rows:,} |" for name, rows in counts.items()],
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true", help="use the small test fixture instead of the full dump")
    parser.add_argument("--out-dir", type=Path, default=STAGED_DIR)
    args = parser.parse_args(argv)

    raw_dir = SAMPLE_DIR if args.sample else RAW_DIR / "model_cards"
    logger.info(f"reading {raw_dir}")
    df = add_parsed_tags(load_metadata(raw_dir))
    graph = build_graph(df, Scope.load())
    manifest = write_staged(graph, args.out_dir, "sample fixture" if args.sample else "model_cards dump")
    REPORTS_DIR.mkdir(exist_ok=True)
    report_name = "etl_report_sample.md" if args.sample else "etl_report.md"
    (REPORTS_DIR / report_name).write_text(render_report(manifest), encoding="utf-8")
    logger.info(f"staged {manifest['row_counts']} into {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
