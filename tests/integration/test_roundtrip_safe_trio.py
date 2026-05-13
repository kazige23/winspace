"""Task 19 — SAFE 三件套 integration test.

Cover the three Phase-3 SAFE detectors in one place:

* node_modules (Phase 2)
* browser_cache (Task 17)
* package_caches (Task 18)

We materialise plausible directory trees for each, run ``winspace scan
--json`` to confirm all three categories surface together, then move
one of each through the CLI and undo it. The roundtrip's tree-hash
must match byte-for-byte before / after.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from winspace.cli import main
from winspace.core.fs import RealFileSystem
from winspace.core.junction import is_junction
from winspace.core.manifest import EntryStatus, load
from winspace.core.verify import fingerprint

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="real-junction roundtrip requires mklink /J"
)


def _make(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def trio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Set up home + AppData layout + drive_d, redirect all env vars."""
    home = tmp_path / "home"
    drive_d = tmp_path / "drive_d"
    manifest_path = tmp_path / "manifest.json"
    home.mkdir()
    drive_d.mkdir()
    local = home / "AppData" / "Local"
    roaming = home / "AppData" / "Roaming"
    local.mkdir(parents=True)
    roaming.mkdir(parents=True)

    # node_modules under home
    _make(home / "proj" / "node_modules" / "lodash" / "index.js", "module.exports")
    _make(home / "proj" / "node_modules" / "lodash" / "package.json", "{}")
    _make(home / "proj" / "node_modules" / "react" / "index.js", "//r")
    # Chrome cache under LOCALAPPDATA
    _make(
        local / "Google" / "Chrome" / "User Data" / "Default" / "Cache" / "data_0",
        "y" * 200,
    )
    # pip cache under LOCALAPPDATA
    _make(local / "pip" / "cache" / "wheels" / "hash" / "wheel.whl", "z" * 200)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr("winspace.core.manifest.default_manifest_path", lambda: manifest_path)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    return home, drive_d, manifest_path


def test_scan_surfaces_all_three_categories(
    trio_env: tuple[Path, Path, Path],
) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["scan", "--min-size", "1B", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    categories = {r["category"] for r in data["results"]}
    assert "node_modules" in categories
    assert "browser_cache" in categories
    assert "package_cache:pip" in categories


@windows_only
@pytest.mark.windows
def test_safe_trio_roundtrip_byte_identical(
    trio_env: tuple[Path, Path, Path],
) -> None:
    home, drive_d, manifest_path = trio_env

    # Locate each of the three categories of source path.
    node_modules_path = home / "proj" / "node_modules"
    chrome_cache_path = (
        home / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    )
    pip_cache_path = home / "AppData" / "Local" / "pip" / "cache"

    fs = RealFileSystem()
    before = {
        "node_modules": fingerprint(node_modules_path, fs=fs),
        "browser_cache": fingerprint(chrome_cache_path, fs=fs),
        "package_cache:pip": fingerprint(pip_cache_path, fs=fs),
    }
    for category, fp in before.items():
        assert fp.file_count > 0, f"{category} fingerprint should be non-empty"

    runner = CliRunner()

    # --- move every category -------------------------------------------------
    for src in (node_modules_path, chrome_cache_path, pip_cache_path):
        result = runner.invoke(main, ["move", str(src), "--to", str(drive_d), "--yes"])
        assert result.exit_code == 0, f"move failed for {src}: {result.output}"
        assert is_junction(src), f"{src} should be a junction after move"

    # Manifest has three active entries.
    m = load(manifest_path)
    assert len(m.entries) == 3
    assert all(e.status == EntryStatus.ACTIVE for e in m.entries)

    # Match each manifest entry back to its category by ORIGINAL PATH —
    # using size to match would collide whenever two categories happen
    # to hold the same number of bytes.
    path_to_category = {
        str(node_modules_path): "node_modules",
        str(chrome_cache_path): "browser_cache",
        str(pip_cache_path): "package_cache:pip",
    }
    for entry in m.entries:
        relocated = Path(entry.new_path)
        assert relocated.exists(), f"missing relocated path: {relocated}"
        category = path_to_category[entry.original_path]
        moved_fp = fingerprint(relocated, fs=fs)
        assert moved_fp == before[category], (
            f"fingerprint diverged for {category}:\nbefore={before[category]}\nafter={moved_fp}"
        )

    # --- undo everything -----------------------------------------------------
    undo_result = runner.invoke(main, ["undo", "--all", "--yes"])
    assert undo_result.exit_code == 0, undo_result.output

    # All originals are now plain dirs (no longer junctions), all
    # destinations are gone.
    for src in (node_modules_path, chrome_cache_path, pip_cache_path):
        assert not is_junction(src), f"{src} should not be a junction after undo"
        assert src.exists(), f"{src} should be restored after undo"

    after = {
        "node_modules": fingerprint(node_modules_path, fs=fs),
        "browser_cache": fingerprint(chrome_cache_path, fs=fs),
        "package_cache:pip": fingerprint(pip_cache_path, fs=fs),
    }
    assert after == before, "byte-level mismatch after SAFE trio roundtrip"

    # Manifest entries should all be rolled_back.
    m = load(manifest_path)
    assert all(e.status == EntryStatus.ROLLED_BACK for e in m.entries)
