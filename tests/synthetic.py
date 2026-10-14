"""A tiny hand-made model-card dump that exercises every ETL rule.

Family tree (child -> parent):

    base/Root-7B                      popular foundation model
      <- a/Chat-7B        finetune    popular
          <- b/Chat-7B-GGUF quantized  popular
      <- c/Merge-7B        merge       (also merges a/Chat-7B), liked
      <- d/Old-Base        finetune    NOT popular, but an ancestor of e/ -> kept anyway
          <- e/Child-of-old finetune   popular
    ghost/Missing          not in the dump -> Unlisted node
      <- f/Uses-ghost      untyped parent, popular
    g/Self                 its own parent (self-loop) -> edge dropped
    h/LoopA <-> h/LoopB    a 2-cycle, both popular -> one edge dropped
    spam/bot               blockassist spam -> dropped
    z/Nobody               0 downloads, 0 likes -> dropped
"""

import pandas as pd

from scripts.etl.raw import add_parsed_tags

TEMPLATE = "This is the model card of a \U0001f917 transformers model that has been pushed on the Hub. x"

ROWS = [
    # modelId, downloads, likes, tags, pipeline_tag, card
    (
        "base/Root-7B",
        9000,
        40,
        ["license:apache-2.0", "en", "fr", "llama", "safetensors", "arxiv:2310.06825", "arxiv:1910.09700"],
        "text-generation",
        "# Root",
    ),
    (
        "a/Chat-7B",
        800,
        3,
        ["base_model:finetune:base/Root-7B", "dataset:HuggingFaceH4/ultrachat_200k", "dataset:custom", "llama"],
        "text-generation",
        "Chat",
    ),
    ("b/Chat-7B-GGUF", 300, 0, ["base_model:quantized:a/Chat-7B", "gguf", "license:apache-2.0"], None, "Quant"),
    (
        "c/Merge-7B",
        0,
        1,
        ["base_model:merge:a/Chat-7B", "base_model:merge:base/Root-7B", "license:llama3.1", "dataset:my_data.jsonl"],
        "text-generation",
        TEMPLATE,
    ),
    ("d/Old-Base", 2, 0, ["base_model:finetune:base/Root-7B"], None, "old"),
    ("e/Child-of-old", 120, 0, ["base_model:adapter:d/Old-Base", "lora"], None, "adapter"),
    ("f/Uses-ghost", 75, 0, ["base_model:ghost/Missing"], None, "ghost child"),
    ("g/Self", 500, 0, ["base_model:finetune:g/Self"], None, "self"),
    ("h/LoopA", 60, 0, ["base_model:finetune:h/LoopB"], None, "loop"),
    ("h/LoopB", 60, 0, ["base_model:finetune:h/LoopA"], None, "loop"),
    ("spam/bot", 5000, 9, ["blockassist", "gensyn"], None, "spam"),
    ("z/Nobody", 0, 0, ["license:mit"], None, "nothing"),
]


def raw_frame() -> pd.DataFrame:
    """The rows above as a raw-dump DataFrame (before tag parsing)."""
    return pd.DataFrame(
        {
            "modelId": [r[0] for r in ROWS],
            "author": [r[0].split("/", maxsplit=1)[0] for r in ROWS],
            "downloads": [r[1] for r in ROWS],
            "likes": [r[2] for r in ROWS],
            "library_name": ["transformers"] * len(ROWS),
            "tags": [r[3] for r in ROWS],
            "pipeline_tag": [r[4] for r in ROWS],
            "createdAt": ["2024-01-01T00:00:00Z"] * len(ROWS),
            "last_modified": ["2024-06-01T00:00:00Z"] * len(ROWS),
            "card": [r[5] for r in ROWS],
        }
    )


def parsed_frame() -> pd.DataFrame:
    """What the transform step works on: raw columns + parsed tags + card stats."""
    df = raw_frame()
    df["card_length"] = df["card"].str.len()
    df["card_is_template"] = df["card"].str.contains("This is the model card of a")
    return add_parsed_tags(df.drop(columns=["card"]))
