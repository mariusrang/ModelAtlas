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
import pyarrow.compute as pc
import pyarrow.dataset as ds

from scripts.etl.tags import ISO_639_1

logger = logging.getLogger("modelatlas.dataset_cards")

CARD_COLUMNS = ["datasetId", "author", "downloads", "likes", "tags", "createdAt"]
DATASET_COLUMNS = ["id", "author", "downloads", "likes", "created_at", "size_category", "task_categories"]


@dataclass
class DatasetTags:
    """What we read from one dataset's tag list."""

    licenses: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    task_categories: list[str] = field(default_factory=list)
    size_category: str | None = None


def parse_dataset_tags(tags: list[str] | None) -> DatasetTags:
    """Split a dataset tag list into typed fields.

    Unlike model cards, a dataset can really mix sources under several licenses
    (e.g. cc-by-4.0 + cc-by-nc-4.0), so we keep all of them: the license check
    must see the non-commercial one even if it is listed second.
    """
    parsed = DatasetTags()
    for raw in tags or []:
        prefix, _, value = raw.strip().partition(":")
        if not value:
            continue
        if prefix == "license" and value.lower() not in parsed.licenses:
            parsed.licenses.append(value.lower())
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
    """Metadata of the wanted datasets only (pyarrow filters while reading).

    Hub ids are case-insensitive (`openai/GSM8K` and `openai/gsm8k` are the same
    repo), and model cards often cite them with another case, so we compare
    lower-cased ids.
    """
    files = card_files(cards_dir)
    if not files or not wanted:
        return pd.DataFrame(columns=CARD_COLUMNS)
    dataset = ds.dataset(files, format="parquet")
    columns = [c for c in CARD_COLUMNS if c in dataset.schema.names]
    lowered = sorted({w.lower() for w in wanted})
    table = dataset.to_table(columns=columns, filter=pc.utf8_lower(pc.field("datasetId")).isin(lowered))
    cards = table.to_pandas()
    return cards.loc[~cards["datasetId"].str.lower().duplicated()].reset_index(drop=True)


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
    licenses = pd.DataFrame(
        [(d, lic) for d, p in zip(datasets["id"], parsed) for lic in p.licenses],
        columns=["dataset", "license"],
    )
    languages = pd.DataFrame(
        [(d, lang) for d, p in zip(datasets["id"], parsed) for lang in sorted(set(p.languages))],
        columns=["dataset", "language"],
    )
    cited_ids = set(cited.dropna().unique())
    stats = {
        "datasets_cited": len(cited_ids),
        "datasets_with_card": int(len(datasets)),
        "dataset_card_coverage_pct": round(100 * len(datasets) / len(cited_ids), 1) if cited_ids else 0.0,
        "datasets_with_license": int(licenses["dataset"].nunique()),
        "datasets_with_several_licenses": int(sum(len(p.licenses) > 1 for p in parsed)),
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
    cited_ids = set(cited.dropna().unique())
    cards = read_cards(cards_dir, cited_ids)
    # use the id exactly as the models cite it (that is the Dataset node id); if two
    # spellings of the same id are cited, both nodes get the card
    spellings: dict[str, list[str]] = {}
    for cited_id in sorted(cited_ids):
        spellings.setdefault(cited_id.lower(), []).append(cited_id)
    if not cards.empty:
        cards = cards.assign(datasetId=cards["datasetId"].str.lower().map(spellings)).explode("datasetId")
    tables, stats = build_dataset_tables(cards.reset_index(drop=True), cited)
    logger.info(
        f"dataset cards: {stats['datasets_with_card']:,} of {stats['datasets_cited']:,} cited datasets "
        f"({stats['dataset_card_coverage_pct']}%)"
    )
    return tables, stats
