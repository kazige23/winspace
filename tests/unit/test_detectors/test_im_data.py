"""Unit tests for :mod:`winspace.detectors.im_data`."""

from __future__ import annotations

from pathlib import Path

import pytest

from winspace.core.fs import RealFileSystem
from winspace.detectors.base import RiskLevel
from winspace.detectors.im_data import IMDataDetector


@pytest.fixture
def fs() -> RealFileSystem:
    return RealFileSystem()


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home"
    local = tmp_path / "local"
    roaming = tmp_path / "roaming"
    documents = home / "Documents"
    home.mkdir()
    local.mkdir()
    roaming.mkdir()
    documents.mkdir()
    return home, local, roaming, documents


def _make_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    (p / "marker").write_text("x")


# --- baseline ----------------------------------------------------------------


def test_no_apps_returns_empty(fs: RealFileSystem, roots: tuple[Path, Path, Path, Path]) -> None:
    home, local, roaming, documents = roots
    det = IMDataDetector(home=home, local_appdata=local, appdata=roaming, documents=documents)
    assert det.find(fs) == []


# --- per-app detection -------------------------------------------------------


@pytest.mark.parametrize(
    ("root_key", "sub_path", "app"),
    [
        ("documents", "WeChat Files", "wechat"),
        ("documents", "Tencent Files", "qq"),
        ("local_appdata", "DingTalk", "dingtalk"),
        ("local_appdata", "Lark", "lark"),
        ("appdata", "discord", "discord"),
        ("appdata", "Telegram Desktop", "telegram"),
        ("appdata", "WhatsApp", "whatsapp"),
        ("appdata", "Signal", "signal"),
    ],
)
def test_each_app_detected_at_canonical_location(
    fs: RealFileSystem,
    roots: tuple[Path, Path, Path, Path],
    root_key: str,
    sub_path: str,
    app: str,
) -> None:
    home, local, roaming, documents = roots
    bases = {
        "home": home,
        "local_appdata": local,
        "appdata": roaming,
        "documents": documents,
    }
    _make_dir(bases[root_key] / sub_path)

    det = IMDataDetector(home=home, local_appdata=local, appdata=roaming, documents=documents)
    [c] = det.find(fs)
    assert c.category == f"im_data:{app}"
    assert c.risk == RiskLevel.RISKY
    assert c.detector_name == "im_data"
    assert c.prerequisite_note_zh
    assert c.prerequisite_note_en


def test_all_eight_apps_together(fs: RealFileSystem, roots: tuple[Path, Path, Path, Path]) -> None:
    home, local, roaming, documents = roots
    _make_dir(documents / "WeChat Files")
    _make_dir(documents / "Tencent Files")
    _make_dir(local / "DingTalk")
    _make_dir(local / "Lark")
    _make_dir(roaming / "discord")
    _make_dir(roaming / "Telegram Desktop")
    _make_dir(roaming / "WhatsApp")
    _make_dir(roaming / "Signal")

    det = IMDataDetector(home=home, local_appdata=local, appdata=roaming, documents=documents)
    apps = sorted(c.category.split(":")[1] for c in det.find(fs))
    assert apps == [
        "dingtalk",
        "discord",
        "lark",
        "qq",
        "signal",
        "telegram",
        "wechat",
        "whatsapp",
    ]


# --- safety: reparse points skipped -----------------------------------------


def test_skips_reparse_point(
    fs: RealFileSystem,
    roots: tuple[Path, Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, local, roaming, documents = roots
    target = documents / "WeChat Files"
    _make_dir(target)
    monkeypatch.setattr(RealFileSystem, "is_reparse_point", lambda self, p: p == target)
    det = IMDataDetector(home=home, local_appdata=local, appdata=roaming, documents=documents)
    assert det.find(fs) == []


def test_skips_when_target_is_a_file(
    fs: RealFileSystem, roots: tuple[Path, Path, Path, Path]
) -> None:
    home, local, roaming, documents = roots
    (documents / "WeChat Files").write_text("not a directory")
    det = IMDataDetector(home=home, local_appdata=local, appdata=roaming, documents=documents)
    assert det.find(fs) == []


# --- default constructor ----------------------------------------------------


def test_default_constructor_uses_env_and_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    det = IMDataDetector()
    assert det._home == tmp_path / "home"
    assert det._local_appdata == tmp_path / "local"
    assert det._appdata == tmp_path / "roaming"
    assert det._documents == tmp_path / "home" / "Documents"
