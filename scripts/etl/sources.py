"""Read `config/sources.toml`: where each raw dataset lives and which revision to use."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")  # so MODELATLAS_DATA_DIR in .env is picked up by the scripts
SOURCES_FILE = ROOT / "config" / "sources.toml"
# Big raw files can live outside the repo (e.g. not in a OneDrive-synced folder):
# set MODELATLAS_DATA_DIR=C:\modelatlas-data to override the default ./data
DATA_DIR = Path(os.environ.get("MODELATLAS_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
REPORTS_DIR = ROOT / "reports"


@dataclass(frozen=True)
class Source:
    """One Hugging Face dataset we download."""

    name: str
    repo_id: str
    repo_type: str
    revision: str
    allow_patterns: tuple[str, ...]
    description: str = ""

    @property
    def local_dir(self) -> Path:
        """Folder where the raw files of this source are stored."""
        return RAW_DIR / self.name


def load_sources(path: Path = SOURCES_FILE) -> dict[str, Source]:
    """Parse the TOML file into `Source` objects, keyed by name."""
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return {
        name: Source(
            name=name,
            repo_id=entry["repo_id"],
            repo_type=entry.get("repo_type", "dataset"),
            revision=entry.get("revision", "main"),
            allow_patterns=tuple(entry.get("allow_patterns", ["*.parquet"])),
            description=entry.get("description", ""),
        )
        for name, entry in raw.items()
    }
