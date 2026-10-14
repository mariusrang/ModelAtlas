"""Unit tests for the download step (Hub calls are mocked, nothing leaves the machine)."""

import json
from types import SimpleNamespace

from scripts.etl import extract
from scripts.etl.sources import Source, load_sources

SOURCE = Source("model_cards", "org/dump", "dataset", "main", ("*.parquet",))


def test_sources_file_parses():
    sources = load_sources()
    assert sources["model_cards"].repo_id == "librarian-bots/model_cards_with_metadata"
    assert sources["model_cards"].allow_patterns == ("*.parquet",)


def test_download_writes_manifest_then_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(extract.Source, "local_dir", property(lambda self: tmp_path / self.name))
    api = SimpleNamespace(dataset_info=lambda repo_id, revision: SimpleNamespace(sha="abc123"))
    calls = []

    def fake_snapshot_download(repo_id, **kwargs):
        calls.append(repo_id)
        (kwargs["local_dir"] / "data").mkdir(parents=True)
        (kwargs["local_dir"] / "data" / "part-0.parquet").write_bytes(b"fake parquet")

    monkeypatch.setattr(extract, "snapshot_download", fake_snapshot_download)

    manifest = extract.download(SOURCE, api=api)
    assert manifest["commit"] == "abc123"
    assert manifest["files"][0]["path"].endswith("part-0.parquet")
    assert manifest["files"][0]["bytes"] == len(b"fake parquet")
    assert json.loads((tmp_path / "model_cards" / "manifest.json").read_text(encoding="utf-8"))["commit"] == "abc123"

    extract.download(SOURCE, api=api)  # same commit -> no second download
    assert calls == ["org/dump"]


def test_sha256_is_stable(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello")
    assert extract.sha256_of(path) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
