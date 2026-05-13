"""Unit tests for :mod:`winspace.detectors.browser_cache`.

We don't rely on the developer machine having any real browser
installed; instead we materialise the canonical layouts under
``tmp_path`` and point the detector at those fake locations via the
``local_appdata`` / ``appdata`` constructor arguments.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from winspace.core.fs import RealFileSystem
from winspace.detectors.base import RiskLevel
from winspace.detectors.browser_cache import BrowserCacheDetector


@pytest.fixture
def fs() -> RealFileSystem:
    return RealFileSystem()


def _make_dir_with_file(path: Path, content: str = "x") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_text(content)


# --- baseline ----------------------------------------------------------------


def test_no_browsers_installed_returns_empty(fs: RealFileSystem, tmp_path: Path) -> None:
    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    assert det.find(fs) == []


# --- Chrome ------------------------------------------------------------------


def test_finds_chrome_default_profile_caches(fs: RealFileSystem, tmp_path: Path) -> None:
    chrome_root = tmp_path / "Google" / "Chrome" / "User Data" / "Default"
    _make_dir_with_file(chrome_root / "Cache")
    _make_dir_with_file(chrome_root / "Code Cache")
    _make_dir_with_file(chrome_root / "GPUCache")

    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    results = det.find(fs)
    names = sorted(c.path.name for c in results)
    assert names == ["Cache", "Code Cache", "GPUCache"]
    for c in results:
        assert c.category == "browser_cache"
        assert c.risk == RiskLevel.SAFE
        assert c.detector_name == "browser_cache"
        assert "浏览器" in c.reason_zh
        assert c.prerequisite_note_zh
        assert c.prerequisite_note_en


def test_finds_chrome_multiple_profiles(fs: RealFileSystem, tmp_path: Path) -> None:
    base = tmp_path / "Google" / "Chrome" / "User Data"
    for profile in ("Default", "Profile 1", "Profile 2"):
        _make_dir_with_file(base / profile / "Cache")

    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    results = det.find(fs)
    profile_names = sorted(c.path.parent.name for c in results)
    assert profile_names == ["Default", "Profile 1", "Profile 2"]


def test_finds_guest_and_system_profiles(fs: RealFileSystem, tmp_path: Path) -> None:
    base = tmp_path / "Google" / "Chrome" / "User Data"
    for profile in ("Default", "Guest Profile", "System Profile"):
        _make_dir_with_file(base / profile / "Cache")
    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    profile_names = sorted(c.path.parent.name for c in det.find(fs))
    assert profile_names == ["Default", "Guest Profile", "System Profile"]


def test_ignores_unrecognised_profile_directories(fs: RealFileSystem, tmp_path: Path) -> None:
    """Chrome creates a few non-profile sibling dirs (``Crashpad``,
    ``ShaderCache``, …). These should be skipped.
    """
    base = tmp_path / "Google" / "Chrome" / "User Data"
    _make_dir_with_file(base / "Default" / "Cache")
    _make_dir_with_file(base / "Crashpad" / "Cache")  # not a profile name
    _make_dir_with_file(base / "ShaderCache")  # is a sibling, not a profile
    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    paths = sorted(str(c.path.relative_to(base)) for c in det.find(fs))
    # Only Default's Cache wins; the other dirs are not Chromium profiles.
    assert paths == [str(Path("Default") / "Cache")]


def test_skips_chrome_cache_that_is_a_reparse_point(
    fs: RealFileSystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "Google" / "Chrome" / "User Data" / "Default"
    _make_dir_with_file(base / "Cache")
    _make_dir_with_file(base / "Code Cache")

    blocked = base / "Cache"
    monkeypatch.setattr(
        RealFileSystem,
        "is_reparse_point",
        lambda self, p: p == blocked,
    )
    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    names = [c.path.name for c in det.find(fs)]
    assert names == ["Code Cache"]


# --- Edge --------------------------------------------------------------------


def test_finds_edge_caches(fs: RealFileSystem, tmp_path: Path) -> None:
    base = tmp_path / "Microsoft" / "Edge" / "User Data" / "Default"
    _make_dir_with_file(base / "Cache")
    _make_dir_with_file(base / "GPUCache")
    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    names = sorted(c.path.name for c in det.find(fs))
    assert names == ["Cache", "GPUCache"]


# --- Firefox -----------------------------------------------------------------


def test_finds_firefox_cache2(fs: RealFileSystem, tmp_path: Path) -> None:
    fx_root = tmp_path / "Mozilla" / "Firefox" / "Profiles"
    _make_dir_with_file(fx_root / "abc123.default-release" / "cache2")
    _make_dir_with_file(fx_root / "xyz789.dev-edition" / "cache2")

    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    results = det.find(fs)
    profile_names = sorted(c.path.parent.name for c in results)
    assert profile_names == ["abc123.default-release", "xyz789.dev-edition"]
    assert all(c.path.name == "cache2" for c in results)


def test_firefox_profile_without_cache_skipped(fs: RealFileSystem, tmp_path: Path) -> None:
    fx_root = tmp_path / "Mozilla" / "Firefox" / "Profiles"
    (fx_root / "fresh.profile").mkdir(parents=True)
    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    assert det.find(fs) == []


def test_firefox_with_no_profiles_dir_handled(fs: RealFileSystem, tmp_path: Path) -> None:
    (tmp_path / "Mozilla" / "Firefox").mkdir(parents=True)
    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    assert det.find(fs) == []


# --- permission errors -------------------------------------------------------


def test_permission_error_on_user_data_is_handled(
    fs: RealFileSystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "Google" / "Chrome" / "User Data"
    _make_dir_with_file(base / "Default" / "Cache")

    real_iterdir = RealFileSystem.iterdir

    def flaky(self: RealFileSystem, path: Path) -> object:
        if path.samefile(base):
            raise PermissionError("denied")
        return real_iterdir(self, path)

    monkeypatch.setattr(RealFileSystem, "iterdir", flaky)
    det = BrowserCacheDetector(local_appdata=tmp_path, appdata=tmp_path)
    assert det.find(fs) == []


# --- default constructor uses env vars --------------------------------------


def test_default_constructor_reads_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    det = BrowserCacheDetector()
    assert det._local_appdata == tmp_path / "local"
    assert det._appdata == tmp_path / "roaming"


def test_constructor_falls_back_to_home_when_env_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    det = BrowserCacheDetector()
    # Both fall back to home, so a missing var doesn't crash.
    assert det._local_appdata == tmp_path
    assert det._appdata == tmp_path
