"""Unit tests for :mod:`winspace.detectors.package_caches`."""

from __future__ import annotations

from pathlib import Path

import pytest

from winspace.core.fs import RealFileSystem
from winspace.detectors.base import RiskLevel
from winspace.detectors.package_caches import PackageCachesDetector


@pytest.fixture
def fs() -> RealFileSystem:
    return RealFileSystem()


@pytest.fixture
def env_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    local = tmp_path / "local"
    roaming = tmp_path / "roaming"
    home.mkdir()
    local.mkdir()
    roaming.mkdir()
    return home, local, roaming


def _make(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "marker").write_text("x")


# --- baseline ----------------------------------------------------------------


def test_nothing_present_returns_empty(
    fs: RealFileSystem, env_roots: tuple[Path, Path, Path]
) -> None:
    home, local, roaming = env_roots
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    assert det.find(fs) == []


# --- per-tool detection ------------------------------------------------------


def test_finds_pip_cache(fs: RealFileSystem, env_roots: tuple[Path, Path, Path]) -> None:
    home, local, roaming = env_roots
    _make(local / "pip" / "cache")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    [c] = det.find(fs)
    assert c.category == "package_cache:pip"
    assert c.risk == RiskLevel.SAFE


def test_finds_both_npm_locations(fs: RealFileSystem, env_roots: tuple[Path, Path, Path]) -> None:
    home, local, roaming = env_roots
    _make(local / "npm-cache")
    _make(roaming / "npm-cache")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    results = det.find(fs)
    cats = sorted(c.category for c in results)
    # Both npm entries appear because we want to relocate either form.
    assert cats == ["package_cache:npm", "package_cache:npm"]


def test_finds_yarn_cache(fs: RealFileSystem, env_roots: tuple[Path, Path, Path]) -> None:
    home, local, roaming = env_roots
    _make(local / "Yarn" / "Cache")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    [c] = det.find(fs)
    assert c.category == "package_cache:yarn"


def test_finds_pnpm_store(fs: RealFileSystem, env_roots: tuple[Path, Path, Path]) -> None:
    home, local, roaming = env_roots
    _make(local / "pnpm" / "store")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    [c] = det.find(fs)
    assert c.category == "package_cache:pnpm"


def test_finds_cargo_registry(fs: RealFileSystem, env_roots: tuple[Path, Path, Path]) -> None:
    home, local, roaming = env_roots
    _make(home / ".cargo" / "registry" / "cache")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    [c] = det.find(fs)
    assert c.category == "package_cache:cargo"


def test_finds_gradle_caches(fs: RealFileSystem, env_roots: tuple[Path, Path, Path]) -> None:
    home, local, roaming = env_roots
    _make(home / ".gradle" / "caches")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    [c] = det.find(fs)
    assert c.category == "package_cache:gradle"


def test_finds_maven_repository(fs: RealFileSystem, env_roots: tuple[Path, Path, Path]) -> None:
    home, local, roaming = env_roots
    _make(home / ".m2" / "repository")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    [c] = det.find(fs)
    assert c.category == "package_cache:maven"


def test_finds_all_seven_tools_together(
    fs: RealFileSystem, env_roots: tuple[Path, Path, Path]
) -> None:
    """Sanity: every supported tool present at once yields 8 entries
    (npm has two locations so it gets two candidates).
    """
    home, local, roaming = env_roots
    _make(local / "pip" / "cache")
    _make(local / "npm-cache")
    _make(roaming / "npm-cache")
    _make(local / "Yarn" / "Cache")
    _make(local / "pnpm" / "store")
    _make(home / ".cargo" / "registry" / "cache")
    _make(home / ".gradle" / "caches")
    _make(home / ".m2" / "repository")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    results = det.find(fs)
    tools = sorted({c.category for c in results})
    assert tools == [
        "package_cache:cargo",
        "package_cache:gradle",
        "package_cache:maven",
        "package_cache:npm",
        "package_cache:pip",
        "package_cache:pnpm",
        "package_cache:yarn",
    ]
    # 7 distinct categories; 8 entries because npm has two paths.
    assert len(results) == 8


# --- safety: reparse / NEVER ------------------------------------------------


def test_skips_reparse_point(
    fs: RealFileSystem,
    env_roots: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, local, roaming = env_roots
    pip_cache = local / "pip" / "cache"
    _make(pip_cache)
    monkeypatch.setattr(RealFileSystem, "is_reparse_point", lambda self, p: p == pip_cache)
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    assert det.find(fs) == []


def test_skips_never_path(
    fs: RealFileSystem,
    env_roots: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If something pathological put pip cache under a NEVER path,
    the detector must skip it.
    """
    import winspace.detectors.package_caches as pkg_mod

    home, local, roaming = env_roots
    _make(local / "pip" / "cache")

    real_is_never = pkg_mod.is_never

    def fake_is_never(p: Path | str | None) -> tuple[bool, str]:
        if p is not None and "pip" in str(p):
            return True, "synthetic"
        return real_is_never(p)

    monkeypatch.setattr(pkg_mod, "is_never", fake_is_never)
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    assert det.find(fs) == []


def test_skips_when_path_is_a_file_not_dir(
    fs: RealFileSystem, env_roots: tuple[Path, Path, Path]
) -> None:
    home, local, roaming = env_roots
    pip_root = local / "pip"
    pip_root.mkdir(parents=True)
    (pip_root / "cache").write_text("not a directory")
    det = PackageCachesDetector(home=home, local_appdata=local, appdata=roaming)
    assert det.find(fs) == []


# --- default constructor --------------------------------------------------


def test_default_constructor_reads_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    det = PackageCachesDetector()
    assert det._home == tmp_path / "home"
    assert det._local_appdata == tmp_path / "local"
    assert det._appdata == tmp_path / "roaming"
