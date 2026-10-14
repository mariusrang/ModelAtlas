"""Read the raw Hugging Face model-card dump into a pandas DataFrame.

Shared by the profiler, the transform step and the fixture builder, so every
step sees the data exactly the same way.
"""

from pathlib import Path

import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds

from scripts.etl.tags import estimate_params_billions, parse_tags

META_COLUMNS = [
    "modelId",
    "author",
    "downloads",
    "likes",
    "library_name",
    "tags",
    "pipeline_tag",
    "createdAt",
    "last_modified",
]
# Auto-generated card text that says nothing about the model.
TEMPLATE_CARD_MARKER = "This is the model card of a \U0001f917 transformers model that has been pushed on the Hub"


def parquet_files(raw_dir: Path) -> list[str]:
    """Every data file under `raw_dir`, ignoring manifest.json and the download cache."""
    files = sorted(str(p) for p in Path(raw_dir).rglob("*.parquet") if ".cache" not in p.parts)
    if not files:
        raise FileNotFoundError(f"no .parquet files under {raw_dir}, run `make data` first")
    return files


def load_metadata(raw_dir: Path, keep_card: bool = False) -> pd.DataFrame:
    """Load every metadata column, plus card length stats (and the card text if asked).

    The card text is ~1.5 GB, so by default we stream it batch by batch and only
    keep its length and whether it is the empty auto-generated template.
    """
    dataset = ds.dataset(parquet_files(raw_dir), format="parquet")
    columns = [c for c in META_COLUMNS if c in dataset.schema.names]
    frames = []
    for batch in dataset.to_batches(columns=[*columns, "card"], batch_size=50_000):
        card = batch.column("card")
        frame = batch.select(columns).to_pandas()
        frame["card_length"] = pc.utf8_length(card).to_numpy(zero_copy_only=False)
        frame["card_is_template"] = pc.match_substring(card, TEMPLATE_CARD_MARKER).to_numpy(zero_copy_only=False)
        if keep_card:
            frame["card"] = card.to_pylist()
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)
    df["downloads"] = df["downloads"].fillna(0).astype("int64")
    df["likes"] = df["likes"].fillna(0).astype("int64")
    df["card_length"] = df["card_length"].fillna(0).astype("int64")
    df["card_is_template"] = df["card_is_template"].fillna(False).astype(bool)
    return df


def add_parsed_tags(df: pd.DataFrame) -> pd.DataFrame:
    """Add one column per tag family (license, parents, datasets...)."""
    parsed = [parse_tags(list(tags) if tags is not None else []) for tags in df["tags"]]
    df["license"] = [p.license for p in parsed]
    df["parents"] = [p.parents for p in parsed]
    df["datasets"] = [p.datasets for p in parsed]
    df["papers"] = [p.papers for p in parsed]
    df["languages"] = [p.languages for p in parsed]
    df["other_tags"] = [p.other for p in parsed]
    df["params_b"] = [estimate_params_billions(model_id) for model_id in df["modelId"]]
    return df
