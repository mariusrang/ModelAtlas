"""Step 1 of the ETL: download the raw Parquet dumps from the Hugging Face Hub.

Usage:
    python -m scripts.etl.extract                 # every source in config/sources.toml
    python -m scripts.etl.extract model_cards     # only one source
    python -m scripts.etl.extract --pin           # write the downloaded commits into sources.toml

The download is idempotent: if `data/raw/<source>/manifest.json` already
records the same commit hash, nothing is downloaded again. The manifest also
stores file sizes and SHA-256 checksums, so we can prove which exact data
version the graph was built from (reproducibility).
"""

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

from scripts.etl.sources import SOURCES_FILE, Source, load_sources

logger = logging.getLogger("modelatlas.extract")
MANIFEST = "manifest.json"


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """Checksum a (possibly big) file without loading it all in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(source: Source) -> dict | None:
    """Return the manifest of a previous download, if any."""
    path = source.local_dir / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def resolve_commit(source: Source, api: HfApi) -> str:
    """Turn a branch name like `main` into the exact commit hash it points to."""
    info = api.dataset_info(source.repo_id, revision=source.revision)
    return info.sha


def download(source: Source, api: HfApi | None = None) -> dict:
    """Download one source (if needed) and return its manifest."""
    api = api or HfApi()
    commit = resolve_commit(source, api)
    previous = read_manifest(source)
    if previous and previous.get("commit") == commit:
        logger.info(f"{source.name}: already at {commit[:10]}, skipping download")
        return previous

    logger.info(f"{source.name}: downloading {source.repo_id}@{commit[:10]} (this can take a few minutes)")
    snapshot_download(
        source.repo_id,
        repo_type=source.repo_type,
        revision=commit,
        local_dir=source.local_dir,
        allow_patterns=list(source.allow_patterns),
    )
    files = sorted(p for p in source.local_dir.rglob("*.parquet"))
    manifest = {
        "source": source.name,
        "repo_id": source.repo_id,
        "requested_revision": source.revision,
        "commit": commit,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": [
            {"path": str(p.relative_to(source.local_dir)), "bytes": p.stat().st_size, "sha256": sha256_of(p)}
            for p in files
        ],
    }
    (source.local_dir / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    total_mb = sum(f["bytes"] for f in manifest["files"]) / 1e6
    logger.info(f"{source.name}: {len(files)} files, {total_mb:.0f} MB, commit {commit}")
    return manifest


def pin_revisions(sources: dict[str, Source], path: Path = SOURCES_FILE) -> dict[str, str]:
    """Replace `revision = "..."` of each downloaded source by the exact commit of its manifest.

    After that, a fresh clone downloads exactly the data we built the graph from,
    even if the dataset gets new commits on the Hub.
    """
    text = path.read_text(encoding="utf-8")
    pinned = {}
    for name, source in sources.items():
        manifest = read_manifest(source)
        if not manifest:
            continue
        section = re.compile(rf'(\[{re.escape(name)}\][^\[]*?revision = )"[^"]*"', re.S)
        text, count = section.subn(rf'\g<1>"{manifest["commit"]}"', text)
        if count:
            pinned[name] = manifest["commit"]
        else:
            # e.g. `revision` written after `allow_patterns`: say it instead of doing nothing
            logger.warning(f"{name}: downloaded but not pinned, put `revision = ...` right after `repo_type`")
    path.write_text(text, encoding="utf-8")
    return pinned


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sources = load_sources()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", choices=sorted(sources), help="sources to download (default: all)")
    parser.add_argument("--pin", action="store_true", help="pin sources.toml to the commits already downloaded")
    args = parser.parse_args(argv)
    if args.pin:
        for name, commit in pin_revisions(sources).items():
            logger.info(f"{name}: pinned to {commit}")
        return 0
    for name in args.names or sorted(sources):
        download(sources[name])
    return 0


if __name__ == "__main__":
    sys.exit(main())
