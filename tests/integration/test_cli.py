"""Integration tests for the CLI (Tasks 12-15).

We invoke the click commands via ``CliRunner`` against a real filesystem
inside ``tmp_path``. The user's real ``%APPDATA%\\winspace`` is never
touched — every test overrides the manifest location and ``Path.home``
to point inside ``tmp_path``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from winspace.cli import format_size, main, parse_size

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="full move/undo flows require mklink /J on Windows"
)


# --- helpers ----------------------------------------------------------------


def _make_tree(root: Path, layout: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, value in layout.items():
        target = root / name
        if isinstance(value, int):
            target.write_text("x" * value)
        elif isinstance(value, str):
            target.write_text(value)
        elif isinstance(value, dict):
            _make_tree(target, value)


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Build (home, drive_d, manifest_path) all under tmp_path.

    Sets Path.home() and manifest's default path so any code that
    relies on them is redirected into tmp_path.
    """
    home = tmp_path / "home"
    drive_d = tmp_path / "drive_d"
    manifest_path = tmp_path / "manifest.json"
    home.mkdir()
    drive_d.mkdir()

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr("winspace.core.manifest.default_manifest_path", lambda: manifest_path)
    return home, drive_d, manifest_path


# --- size helpers -----------------------------------------------------------


def test_parse_size_handles_units() -> None:
    assert parse_size("100") == 100
    assert parse_size("1KB") == 1024
    assert parse_size("1MB") == 1024**2
    assert parse_size("2G") == 2 * 1024**3
    assert parse_size("2.5MB") == int(2.5 * 1024**2)


def test_parse_size_rejects_garbage() -> None:
    import click

    with pytest.raises(click.BadParameter):
        parse_size("oops")
    with pytest.raises(click.BadParameter):
        parse_size("")


def test_format_size() -> None:
    assert format_size(0) == "0.0 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024**2) == "1.0 MB"


# --- top-level entry --------------------------------------------------------


def test_version_command() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "winspace" in result.output


def test_help_lists_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("scan", "move", "undo", "list"):
        assert cmd in result.output


# --- scan -------------------------------------------------------------------


def test_scan_with_nothing_to_find(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1B"])
    assert result.exit_code == 0
    assert "未找到" in result.output or "no candidates" in result.output.lower()


def test_scan_finds_node_modules(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    home, _drive_d, _manifest = isolated_env
    _make_tree(home / "proj", {"node_modules": {"x": "y" * 500}})

    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1B"])
    assert result.exit_code == 0, result.output
    assert "node_modules" in result.output


def test_scan_min_size_filter(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    home, _drive_d, _manifest = isolated_env
    _make_tree(home / "proj", {"node_modules": {"x": 10}})
    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1MB"])
    assert result.exit_code == 0
    # Far too small to appear at min-size 1MB.
    assert "node_modules" not in result.output or "未找到" in result.output


def test_scan_json_output(isolated_env: tuple[Path, Path, Path]) -> None:
    home, _drive_d, _manifest = isolated_env
    _make_tree(home / "proj", {"node_modules": {"x": "y" * 500}})

    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1B", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "results" in data
    assert any("node_modules" in r["category"] for r in data["results"])


# --- move -------------------------------------------------------------------


def test_move_rejects_missing_source(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    _home, drive_d, _manifest = isolated_env
    runner = CliRunner()
    result = runner.invoke(main, ["move", "/no/such/path", "--to", str(drive_d), "--yes"])
    assert result.exit_code != 0


def test_move_user_cancels_at_prompt(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    home, drive_d, _manifest = isolated_env
    src = home / "proj" / "node_modules"
    _make_tree(src, {"a.txt": 10})
    runner = CliRunner()
    # No --yes; provide "n" as the answer to the prompt.
    result = runner.invoke(main, ["move", str(src), "--to", str(drive_d)], input="n\n")
    assert result.exit_code == 1  # user cancel


def test_move_dry_run_does_not_mutate(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    home, drive_d, manifest = isolated_env
    src = home / "proj" / "node_modules"
    _make_tree(src, {"a.txt": 10})
    runner = CliRunner()
    result = runner.invoke(main, ["move", str(src), "--to", str(drive_d), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert src.is_dir()
    assert not (drive_d / "winspace" / "node_modules").exists()
    assert not manifest.exists()


@windows_only
@pytest.mark.windows
def test_move_yes_completes(isolated_env: tuple[Path, Path, Path]) -> None:
    from winspace.core.junction import is_junction

    home, drive_d, manifest = isolated_env
    src = home / "proj" / "node_modules"
    _make_tree(src, {"a.txt": "hello"})

    runner = CliRunner()
    result = runner.invoke(main, ["move", str(src), "--to", str(drive_d), "--yes"])
    assert result.exit_code == 0, result.output
    # Source path is now a junction; target dir has the data.
    assert is_junction(src)
    assert (src / "a.txt").read_text() == "hello"
    assert manifest.exists()


# --- undo + list ------------------------------------------------------------


@windows_only
@pytest.mark.windows
def test_undo_last_round_trip(isolated_env: tuple[Path, Path, Path]) -> None:
    from winspace.core.junction import is_junction

    home, drive_d, _manifest = isolated_env
    src = home / "proj" / "node_modules"
    _make_tree(src, {"a.txt": "hello"})

    runner = CliRunner()
    move_result = runner.invoke(main, ["move", str(src), "--to", str(drive_d), "--yes"])
    assert move_result.exit_code == 0

    undo_result = runner.invoke(main, ["undo", "--last", "--yes"])
    assert undo_result.exit_code == 0, undo_result.output
    assert not is_junction(src)
    assert (src / "a.txt").read_text() == "hello"


def test_undo_with_no_active_entries(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["undo", "--last", "--yes"])
    assert result.exit_code == 2  # bad args / no entries to undo


@windows_only
@pytest.mark.windows
def test_list_shows_active_entry(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    home, drive_d, _manifest = isolated_env
    src = home / "proj" / "node_modules"
    _make_tree(src, {"a.txt": "hello"})

    runner = CliRunner()
    runner.invoke(main, ["move", str(src), "--to", str(drive_d), "--yes"])
    list_result = runner.invoke(main, ["list"])
    assert list_result.exit_code == 0
    assert "node_modules" in list_result.output
    assert "active" in list_result.output


def test_list_empty_manifest(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "manifest 为空" in result.output or "no recorded moves" in result.output


@windows_only
@pytest.mark.windows
def test_list_json_output(isolated_env: tuple[Path, Path, Path]) -> None:
    home, drive_d, _manifest = isolated_env
    src = home / "proj" / "node_modules"
    _make_tree(src, {"a.txt": "hello"})

    runner = CliRunner()
    runner.invoke(main, ["move", str(src), "--to", str(drive_d), "--yes"])
    result = runner.invoke(main, ["list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data["entries"]) == 1
    [entry] = data["entries"]
    assert entry["status"] == "active"
    assert entry["health"] == "ok"
