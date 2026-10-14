"""Small cross-platform helpers called by the Makefile.

We keep these in Python instead of shell one-liners so `make` behaves the
same on Linux, macOS and Windows (where `rm`, `find` or `cp` may not exist).

Usage: python scripts/dev.py {help,venv,env,clean,tree}
"""

import argparse
import re
import shutil
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIRS = {"__pycache__", ".pytest_cache", "htmlcov"}
TREE_SKIP = {".git", ".venv", "__pycache__", ".pytest_cache", "htmlcov", "data", ".idea", ".vscode"}


def show_help() -> None:
    """Print every Makefile target that has a `## description` comment."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    print("ModelAtlas - available commands:")
    for target, description in re.findall(r"^([a-zA-Z_-]+):.*?## (.*)$", makefile, re.MULTILINE):
        print(f"  make {target:<18} {description}")


def create_venv() -> None:
    """Create .venv with pip, unless it already exists."""
    venv_dir = ROOT / ".venv"
    if venv_dir.is_dir():
        print(".venv already exists")
        return
    venv.create(venv_dir, with_pip=True)
    print("created .venv")


def create_env_file() -> None:
    """Copy .env.example to .env the first time, so docker compose can start."""
    env_file, example = ROOT / ".env", ROOT / ".env.example"
    if env_file.exists():
        print(".env already exists")
        return
    shutil.copy(example, env_file)
    print("created .env from .env.example -> change the passwords in it!")


def clean() -> None:
    """Delete caches and coverage leftovers (never touches .venv or data)."""
    for path in ROOT.rglob("*"):
        if path.is_dir() and path.name in CACHE_DIRS and ".venv" not in path.parts:
            shutil.rmtree(path, ignore_errors=True)
    for path in [*ROOT.glob(".coverage*"), ROOT / "coverage.xml"]:
        path.unlink(missing_ok=True)
    print("clean")


def print_tree(folder: Path = ROOT, depth: int = 0, max_depth: int = 3) -> None:
    """Print the project tree, skipping heavy or generated folders."""
    for child in sorted(folder.iterdir()):
        if child.name in TREE_SKIP:
            continue
        print("  " * depth + child.name + ("/" if child.is_dir() else ""))
        if child.is_dir() and depth + 1 < max_depth:
            print_tree(child, depth + 1, max_depth)


COMMANDS = {"help": show_help, "venv": create_venv, "env": create_env_file, "clean": clean, "tree": print_tree}


def main(argv: list[str] | None = None) -> int:
    """Parse the command name and run it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    COMMANDS[args.command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
