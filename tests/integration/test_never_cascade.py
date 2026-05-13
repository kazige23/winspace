"""Phase 4 integration tests — NEVER cascade + RISKY guard.

Two key behaviours that the cloud_sync + im_data detectors must
deliver end-to-end through the CLI:

1. **Cascade**: a node_modules sitting *inside* a fake OneDrive root
   does not appear in ``winspace scan`` results, even though the
   node_modules detector would otherwise find it.
2. **RISKY guard**: ``winspace move`` on a WeChat Files path is
   refused with a clear message; passing ``--i-know-what-im-doing``
   lets it through (we verify the precheck passes; we don't actually
   complete the move in this test to keep things hermetic).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from winspace.cli import main


@pytest.fixture
def phase4_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "AppData" / "Local").mkdir(parents=True)
    (home / "AppData" / "Roaming").mkdir(parents=True)
    (home / "Documents").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(
        "winspace.core.manifest.default_manifest_path",
        lambda: tmp_path / "manifest.json",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    # Clear any OneDrive env overrides that would point at the dev machine.
    for var in (
        "OneDrive",
        "OneDriveCommercial",
        "OneDriveConsumer",
        "OneDriveBusiness",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


# --- cascade -----------------------------------------------------------------


def test_node_modules_under_onedrive_does_not_appear_in_scan(
    phase4_env: Path,
) -> None:
    """The cloud_sync detector should mask a real node_modules sitting
    inside ~/OneDrive so the user never sees it as a candidate.
    """
    home = phase4_env
    nm = home / "OneDrive" / "my-app" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text("{}")

    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1B", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    paths = [r["path"] for r in data["results"]]
    assert str(nm) not in paths


def test_node_modules_outside_onedrive_still_appears(
    phase4_env: Path,
) -> None:
    """The cascade must NOT mask a node_modules that's outside cloud sync."""
    home = phase4_env
    safe = home / "projects" / "real-app" / "node_modules"
    safe.mkdir(parents=True)
    (safe / "package.json").write_text("{}")

    # Make a OneDrive too so cloud_sync has something to report.
    (home / "OneDrive").mkdir()

    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1B", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    paths = [r["path"] for r in data["results"]]
    assert str(safe) in paths


# --- NEVER guard on move ----------------------------------------------------


def test_move_into_onedrive_subpath_is_refused(phase4_env: Path) -> None:
    home = phase4_env
    (home / "OneDrive" / "Documents" / "thing").mkdir(parents=True)
    target = home / "OneDrive" / "Documents" / "thing"

    runner = CliRunner()
    result = runner.invoke(main, ["move", str(target), "--to", str(home / "drive_d"), "--yes"])
    assert result.exit_code != 0
    assert "never" in result.output.lower() or "NEVER" in result.output


# --- RISKY guard on move ----------------------------------------------------


def test_move_wechat_data_refused_without_override(phase4_env: Path) -> None:
    home = phase4_env
    wechat = home / "Documents" / "WeChat Files"
    wechat.mkdir(parents=True)
    (wechat / "file.txt").write_text("important")

    runner = CliRunner()
    result = runner.invoke(main, ["move", str(wechat), "--to", str(home / "drive_d"), "--yes"])
    assert result.exit_code != 0
    assert "i-know-what-im-doing" in result.output


def test_move_wechat_passes_guard_with_override(phase4_env: Path) -> None:
    """With ``--i-know-what-im-doing`` set, the precheck stops blocking.

    The move itself may still fail later (e.g. no real D: drive in tests)
    but the test just verifies the guard does NOT exit with EXIT_BAD_ARGS
    on the RISKY rule.
    """
    home = phase4_env
    wechat = home / "Documents" / "WeChat Files"
    wechat.mkdir(parents=True)
    (wechat / "file.txt").write_text("important")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "move",
            str(wechat),
            "--to",
            str(home / "drive_d"),
            "--yes",
            "--i-know-what-im-doing",
            "--dry-run",
        ],
    )
    # With dry-run + override, the command should pass the RISKY guard
    # and reach the dry-run summary path. exit_code should be 0.
    assert result.exit_code == 0, result.output
    assert "i-know-what-im-doing" not in result.output  # no rejection text


# --- scan default does not include RISKY -----------------------------------


def test_scan_default_hides_im_data(phase4_env: Path) -> None:
    home = phase4_env
    wechat = home / "Documents" / "WeChat Files"
    wechat.mkdir(parents=True)
    (wechat / "file.txt").write_text("important")

    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1B", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert all("im_data" not in r["category"] for r in data["results"])


def test_scan_include_risky_shows_im_data(phase4_env: Path) -> None:
    home = phase4_env
    wechat = home / "Documents" / "WeChat Files"
    wechat.mkdir(parents=True)
    (wechat / "file.txt").write_text("important")

    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1B", "--include-risky", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert any(r["category"] == "im_data:wechat" for r in data["results"])
