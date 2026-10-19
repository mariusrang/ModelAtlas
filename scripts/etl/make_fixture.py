"""Build the small, versioned test fixture from the full dump.

Usage (needs `make data` first):
    python -m scripts.etl.make_fixture

Why a fixture: CI and the Cypher tests must run in seconds, without a 1.5 GB
download. The fixture is a *real* slice of the dump, not invented data, so the
tests exercise the real messiness (spam, self-loops, missing parents...).

What goes in (fixed seed, so it is reproducible):
- a few well-known families (Mistral, Llama, Qwen, BERT, FLUX...): the root,
  its most downloaded descendants up to 3 generations, and all their ancestors;
- a handful of spam models and self-loops, so the filters are tested too;
- the dataset cards of every dataset those models cite (second source,
  metadata only, in tests/fixtures/sample_datasets/).
Card text is cut to 3,000 characters to keep the file small.
"""

import argparse
import logging
import sys
from collections import deque
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

from scripts.etl.dataset_cards import card_files, read_cards
from scripts.etl.raw import parquet_files
from scripts.etl.sources import RAW_DIR, ROOT
from scripts.etl.tags import parse_tags

logger = logging.getLogger("modelatlas.fixture")

OUT_FILE = ROOT / "tests" / "fixtures" / "sample_models" / "sample.parquet"
DATASETS_OUT_FILE = ROOT / "tests" / "fixtures" / "sample_datasets" / "sample.parquet"
ANCHORS = [
    "mistralai/Mistral-7B-v0.1",
    "meta-llama/Llama-3.1-8B-Instruct",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen3-0.6B",
    "google-bert/bert-base-uncased",
    "sentence-transformers/all-MiniLM-L6-v2",
    "black-forest-labs/FLUX.1-dev",
]
PER_ANCHOR = 250
MAX_GENERATIONS = 3
CARD_CHARS = 3000
SEED = 42


def _family(anchor: str, children_of: dict[str, list[str]], downloads: dict[str, int]) -> set[str]:
    """Top descendants of `anchor` (by downloads), at most MAX_GENERATIONS below it."""
    found, queue = {}, deque([(anchor, 0)])
    while queue:
        node, depth = queue.popleft()
        if depth >= MAX_GENERATIONS:
            continue
        for child in children_of.get(node, []):
            if child not in found:
                found[child] = depth + 1
                queue.append((child, depth + 1))
    best = sorted(found, key=lambda m: downloads.get(m, 0), reverse=True)[:PER_ANCHOR]
    return {anchor, *best}


def _with_ancestors(ids: set[str], parents_of: dict[str, list[str]]) -> set[str]:
    out, stack = set(ids), list(ids)
    while stack:
        for parent in parents_of.get(stack.pop(), []):
            if parent not in out:
                out.add(parent)
                stack.append(parent)
    return out


def select_ids(meta: pd.DataFrame) -> set[str]:
    """Pick the model ids that go into the fixture."""
    parents_of, children_of = {}, {}
    for model_id, tags in zip(meta["modelId"], meta["tags"]):
        for _, parent in parse_tags(list(tags) if tags is not None else []).parents:
            parents_of.setdefault(model_id, []).append(parent)
            children_of.setdefault(parent, []).append(model_id)
    downloads = dict(zip(meta["modelId"], meta["downloads"]))
    chosen: set[str] = set()
    for anchor in ANCHORS:
        chosen |= _family(anchor, children_of, downloads)
    chosen = _with_ancestors(chosen, parents_of)

    tag_text = meta["tags"].map(lambda tags: " ".join(tags) if tags is not None else "")
    spam_ids = meta.loc[tag_text.str.contains("blockassist"), "modelId"]
    spam = spam_ids.sample(n=min(20, len(spam_ids)), random_state=SEED)
    self_loops = [m for m, ps in parents_of.items() if m in ps][:5]
    return chosen | set(spam) | set(self_loops)


def cited_datasets(table: pd.DataFrame) -> set[str]:
    """Every dataset id cited in the tags of the fixture models."""
    return {d for tags in table["tags"] for d in parse_tags(list(tags) if tags is not None else []).datasets}


def write_dataset_fixture(table: pd.DataFrame, cards_dir: Path, out: Path) -> int:
    """Dataset cards of the datasets the fixture models cite; 0 if the dump is missing."""
    if not card_files(cards_dir):
        logger.warning(f"no dataset cards under {cards_dir}, dataset fixture not rebuilt (run `make data`)")
        return 0
    cards = read_cards(cards_dir, cited_datasets(table))
    out.parent.mkdir(parents=True, exist_ok=True)
    cards.sort_values("datasetId").to_parquet(out, index=False, compression="zstd")
    logger.info(f"dataset fixture: {len(cards)} dataset cards -> {out}")
    return len(cards)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR / "model_cards")
    parser.add_argument("--out", type=Path, default=OUT_FILE)
    parser.add_argument("--cards-dir", type=Path, default=RAW_DIR / "dataset_cards")
    parser.add_argument("--datasets-out", type=Path, default=DATASETS_OUT_FILE)
    args = parser.parse_args(argv)

    dataset = ds.dataset(parquet_files(args.raw_dir), format="parquet")
    meta = dataset.to_table(columns=["modelId", "tags", "downloads"]).to_pandas()
    ids = select_ids(meta)
    table = dataset.to_table(filter=ds.field("modelId").isin(sorted(ids))).to_pandas()
    table["card"] = table["card"].str.slice(0, CARD_CHARS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.sort_values("modelId").to_parquet(args.out, index=False, compression="zstd")
    logger.info(f"fixture: {len(table)} models -> {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    write_dataset_fixture(table, args.cards_dir, args.datasets_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
