"""Unit test for the fixture builder, on the synthetic dump."""

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.etl import make_fixture
from tests.synthetic import raw_frame


def test_fixture_keeps_families_spam_and_self_loops(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pq.write_table(pa.Table.from_pandas(raw_frame()), raw_dir / "part.parquet")
    monkeypatch.setattr(make_fixture, "ANCHORS", ["base/Root-7B"])
    out = tmp_path / "fixture" / "sample.parquet"

    assert make_fixture.main(["--raw-dir", str(raw_dir), "--out", str(out)]) == 0
    ids = set(pd.read_parquet(out)["modelId"])
    # the Root family (3 generations down), the spam row and the self-loop are kept
    assert {"base/Root-7B", "a/Chat-7B", "b/Chat-7B-GGUF", "c/Merge-7B", "e/Child-of-old"} <= ids
    assert {"spam/bot", "g/Self"} <= ids
    # unrelated models are not
    assert "z/Nobody" not in ids and "h/LoopA" not in ids
