"""End-to-end test of the profiler on a tiny synthetic dump."""

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.etl import profile

TEMPLATE = "This is the model card of a \U0001f917 transformers model that has been pushed on the Hub. Blah."
ROWS = [
    # (modelId, downloads, likes, tags, card)
    ("base/Root-7B", 5000, 50, ["license:apache-2.0", "en", "text-generation"], "# Root\nA real card."),
    ("a/Child-7B", 300, 2, ["base_model:finetune:base/Root-7B", "base_model:base/Root-7B", "fr"], "Fine-tune"),
    ("b/Child-7B-GGUF", 60, 0, ["base_model:quantized:a/Child-7B", "gguf"], "Quant"),
    ("c/Merge-7B", 0, 1, ["base_model:merge:a/Child-7B", "base_model:merge:base/Root-7B"], "Merge"),
    ("d/Orphan", 0, 0, ["base_model:ghost/NotInDump", "license:mit"], TEMPLATE),
    ("e/LoopA", 0, 0, ["base_model:e/LoopB"], "loop"),
    ("e/LoopB", 0, 0, ["base_model:e/LoopA"], "loop"),
]


@pytest.fixture(name="raw_dir")
def fixture_raw_dir(tmp_path):
    table = pa.table(
        {
            "modelId": [r[0] for r in ROWS],
            "author": [r[0].split("/", maxsplit=1)[0] for r in ROWS],
            "downloads": [r[1] for r in ROWS],
            "likes": [r[2] for r in ROWS],
            "library_name": ["transformers"] * len(ROWS),
            "tags": [r[3] for r in ROWS],
            "pipeline_tag": ["text-generation"] + [None] * (len(ROWS) - 1),
            "createdAt": ["2024-01-01T00:00:00Z"] * len(ROWS),
            "card": [r[4] for r in ROWS],
        }
    )
    (tmp_path / "data").mkdir()
    pq.write_table(table, tmp_path / "data" / "train-00000.parquet")
    # what `make data` also leaves in the folder: must be ignored by the profiler
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "stale.parquet").write_bytes(b"not parquet")
    return tmp_path


def test_profile_numbers_on_synthetic_dump(raw_dir):
    df = profile.add_parsed_tags(profile.load_metadata(raw_dir))
    result = profile.build_profile(df)

    assert result["overview"]["rows"] == 7
    lineage = result["lineage"]
    assert lineage["relation_counts"] == {"merge": 2, "unspecified": 3, "finetune": 1, "quantized": 1}
    assert lineage["children_with_several_parents"] == 1
    assert lineage["dangling_parent_edges"] == 1
    assert lineage["cycles_found"] >= 1
    # Root (0) <- Child (1) <- GGUF (2): longest chain is 2
    assert max(int(d) for d in lineage["depth_histogram"]) == 2
    assert result["metadata"]["top_licenses"][0] == {"value": "apache-2.0", "count": 1}
    assert result["cards"]["template_card_pct"] == pytest.approx(14.29)
    # downloads>=50 keeps Root, Child, GGUF; likes>=1 adds Merge; ancestors add nothing new
    assert result["scope_options"]["downloads>=50 or likes>=1"] == {"kept": 4, "with_ancestors": 4}


def test_cli_writes_both_reports(raw_dir, tmp_path):
    out_dir = tmp_path / "reports"
    assert profile.main(["--raw-dir", str(raw_dir), "--out-dir", str(out_dir)]) == 0
    data = json.loads((out_dir / "data_profile.json").read_text(encoding="utf-8"))
    markdown = (out_dir / "data_profile.md").read_text(encoding="utf-8")
    assert data["overview"]["unique_model_ids"] == 7
    assert "# Data profile" in markdown and "| merge | 2 |" in markdown


def test_missing_data_gives_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="make data"):
        profile.load_metadata(tmp_path)
