"""Unit tests for scripts/dev.py (the Makefile helpers)."""

import pytest

from scripts import dev


@pytest.fixture(name="project")
def fixture_project(tmp_path, monkeypatch):
    """A fake project root so the helpers never touch the real repo."""
    (tmp_path / "Makefile").write_text("lint: ## Run pylint\n\tpylint app\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("NEO4J_PASSWORD=x\n", encoding="utf-8")
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    return tmp_path


def test_help_lists_documented_targets(project, capsys):
    assert project.exists()
    dev.show_help()
    assert "make lint" in capsys.readouterr().out


def test_env_is_created_once(project):
    dev.create_env_file()
    assert (project / ".env").read_text(encoding="utf-8") == "NEO4J_PASSWORD=x\n"
    (project / ".env").write_text("custom", encoding="utf-8")
    dev.create_env_file()
    assert (project / ".env").read_text(encoding="utf-8") == "custom"


def test_clean_removes_caches_only(project):
    cache = project / "app" / "__pycache__"
    cache.mkdir(parents=True)
    (project / ".coverage").write_text("", encoding="utf-8")
    dev.clean()
    assert not cache.exists()
    assert not (project / ".coverage").exists()
    assert (project / "Makefile").exists()


def test_tree_skips_heavy_folders(project, capsys):
    (project / "data").mkdir()
    (project / "app").mkdir()
    dev.print_tree(project)
    out = capsys.readouterr().out
    assert "app/" in out and "data" not in out


def test_main_dispatches(project, capsys):
    assert project.exists()
    assert dev.main(["help"]) == 0
    assert "available commands" in capsys.readouterr().out
