"""Second data source: the Hugging Face dataset cards.

Model cards only give dataset *ids* (`dataset:HuggingFaceH4/ultrachat_200k`).
The dataset cards dump tells us what those datasets are: their license, their
languages, their size and task. With it, the license check can look at the
training data and not only at the model weights.

We only read the datasets that at least one model of the graph cites (~15k
out of ~630k cards), and only the metadata columns (the card text is skipped).
Dataset tags look like model tags but use explicit prefixes:

    ["license:cc-by-nc-4.0", "language:fr", "task_categories:text-generation",
     "size_categories:10K<n<100K", "modality:text", "region:us"]
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

from scripts.etl.tags import ISO_639_1

logger = logging.getLogger("modelatlas.dataset_cards")

CARD_COLUMNS = ["datasetId", "author", "downloads", "likes", "tags", "createdAt"]
DATASET_COLUMNS = ["id", "author", "downloads", "likes", "created_at", "size_category", "task_categories"]


@dataclass
class DatasetTags:
    """What we read from one dataset's tag list."""

    license: str | None = None
    languages: list[str] = field(default_factory=list)
    task_categories: list[str] = field(default_factory=list)
    size_category: str | None = None


def parse_dataset_tags(tags: list[str] | None) -> DatasetTags:
    """Split a dataset tag list into typed fields (first license wins, like for models)."""
    parsed = DatasetTags()
    for raw in tags or []:
        prefix, _, value = raw.strip().partition(":")
        if not value:
            continue
        if prefix == "license" and parsed.license is None:
            parsed.license = value.lower()
        elif prefix == "language" and value.lower() in ISO_639_1:
            parsed.languages.append(value.lower())
        elif prefix == "task_categories":
            parsed.task_categories.append(value)
        elif prefix == "size_categories" and parsed.size_category is None:
            parsed.size_category = value
    return parsed


def card_files(cards_dir: Path) -> list[str]:
    """Parquet files of the dump (empty list if it was not downloaded)."""
    return sorted(str(p) for p in Path(cards_dir).rglob("*.parquet") if ".cache" not in p.parts)


def read_cards(cards_dir: Path, wanted: set[str]) -> pd.DataFrame:
    """Metadata of the wanted datasets only (pyarrow filters while reading)."""
    files = card_files(cards_dir)
    if not files or not wanted:
        return pd.DataFrame(columns=CARD_COLUMNS)
    dataset = ds.dataset(files, format="parquet")
    columns = [c for c in CARD_COLUMNS if c in dataset.schema.names]
    table = dataset.to_table(columns=columns, filter=ds.field("datasetId").isin(sorted(wanted)))
    return table.to_pandas().drop_duplicates("datasetId")


def build_dataset_tables(cards: pd.DataFrame, cited: pd.Series) -> tuple[dict[str, pd.DataFrame], dict]:
    """Node properties + (dataset -> license) and (dataset -> language) link tables."""
    parsed = [parse_dataset_tags(list(t) if t is not None else []) for t in cards.get("tags", [])]
    datasets = pd.DataFrame(
        {
            "id": cards.get("datasetId", pd.Series(dtype=str)).astype(str),
            "author": cards.get("author"),
            "downloads": cards.get("downloads", pd.Series(dtype="int64")).fillna(0).astype("int64"),
            "likes": cards.get("likes", pd.Series(dtype="int64")).fillna(0).astype("int64"),
            "created_at": cards.get("createdAt", pd.Series(dtype=str)).map(lambda v: None if pd.isna(v) else str(v)),
            "size_category": [p.size_category for p in parsed],
            "task_categories": [p.task_categories for p in parsed],
        },
        columns=DATASET_COLUMNS,
    )
    licenses = pd.DataFrame({"dataset": datasets["id"], "license": [p.license for p in parsed]}).dropna()
    languages = pd.DataFrame(
        [(d, lang) for d, p in zip(datasets["id"], parsed) for lang in sorted(set(p.languages))],
        columns=["dataset", "language"],
    )
    cited_ids = set(cited.dropna().unique())
    stats = {
        "datasets_cited": len(cited_ids),
        "datasets_with_card": int(len(datasets)),
        "dataset_card_coverage_pct": round(100 * len(datasets) / len(cited_ids), 1) if cited_ids else 0.0,
        "datasets_with_license": int(len(licenses)),
    }
    tables = {
        "datasets": datasets.reset_index(drop=True),
        "dataset_license": licenses.reset_index(drop=True),
        "dataset_language": languages,
    }
    return tables, stats


def enrich_datasets(cited: pd.Series, cards_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    """Read and parse the cards of every cited dataset. Missing dump = empty tables + a warning."""
    if not card_files(cards_dir):
        logger.warning(f"no dataset cards under {cards_dir}: Dataset nodes will only have their id (run `make data`)")
    cards = read_cards(cards_dir, set(cited.dropna().unique()))
    tables, stats = build_dataset_tables(cards, cited)
    logger.info(
        f"dataset cards: {stats['datasets_with_card']:,} of {stats['datasets_cited']:,} cited datasets "
        f"({stats['dataset_card_coverage_pct']}%)"
    )
    return tables, stats
