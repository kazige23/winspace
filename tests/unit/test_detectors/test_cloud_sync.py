"""Unit tests for :mod:`winspace.detectors.cloud_sync`.

Coverage target ≥ 95% — this detector is the firewall keeping
winspace from corrupting the user's cloud-synced data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from winspace.core.fs import RealFileSystem
from winspace.detectors.base import RiskLevel
from winspace.detectors.cloud_sync import CloudSyncDetector


@pytest.fixture
def fs() -> RealFileSystem:
    return RealFileSystem()


@pytest.fixture
def envs(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    local = tmp_path / "local"
    home.mkdir()
    local.mkdir()
    return home, local


# --- baseline ----------------------------------------------------------------


def test_no_cloud_clients_returns_empty(fs: RealFileSystem, envs: tuple[Path, Path]) -> None:
    home, local = envs
    det = CloudSyncDetector(home=home, local_appdata=local, environ={})
    assert det.find(fs) == []


# --- per-provider default-path detection -------------------------------------


@pytest.mark.parametrize(
    ("dirname", "expected_provider"),
    [
        ("OneDrive", "onedrive"),
        ("OneDrive - Personal", "onedrive"),
        ("iCloudDrive", "icloud"),
        ("iCloud Drive", "icloud"),
        ("Google Drive", "google_drive"),
        ("GoogleDrive", "google_drive"),
        ("Dropbox", "dropbox"),
        ("Box", "box"),
        ("Nutstore", "nutstore"),
        ("BaiduNetdiskWorkspace", "baidu_netdisk"),
    ],
)
def test_default_install_locations_detected(
    fs: RealFileSystem,
    envs: tuple[Path, Path],
    dirname: str,
    expected_provider: str,
) -> None:
    home, local = envs
    (home / dirname).mkdir()
    det = CloudSyncDetector(home=home, local_appdata=local, environ={})
    results = det.find(fs)
    [c] = results
    assert c.risk == RiskLevel.NEVER
    assert c.category == f"cloud_sync:{expected_provider}"
    assert c.detector_name == "cloud_sync"


def test_nutstore_nested_layout_also_detected(fs: RealFileSystem, envs: tuple[Path, Path]) -> None:
    """Some Nutstore installs use ``~/Nutstore/Nutstore`` as the actual
    sync root. We check both that AND the parent to be safe.
    """
    home, local = envs
    (home / "Nutstore" / "Nutstore").mkdir(parents=True)
    det = CloudSyncDetector(home=home, local_appdata=local, environ={})
    paths = sorted(str(c.path) for c in det.find(fs))
    # Both ~/Nutstore/Nutstore and ~/Nutstore exist as directories,
    # so both are detected.
    assert len(paths) == 2


# --- env-var overrides -------------------------------------------------------


def test_onedrive_env_var_overrides_default(fs: RealFileSystem, envs: tuple[Path, Path]) -> None:
    home, local = envs
    custom = home / "Cloud" / "OneDriveCustom"
    custom.mkdir(parents=True)
    det = CloudSyncDetector(
        home=home,
        local_appdata=local,
        environ={"OneDrive": str(custom)},
    )
    paths = [c.path for c in det.find(fs)]
    assert custom.resolve() in [p.resolve() for p in paths]


def test_onedrive_commercial_and_consumer_env_vars(
    fs: RealFileSystem, envs: tuple[Path, Path]
) -> None:
    home, local = envs
    biz = home / "OneDriveBiz"
    personal = home / "OneDrivePersonal"
    biz.mkdir()
    personal.mkdir()
    det = CloudSyncDetector(
        home=home,
        local_appdata=local,
        environ={
            "OneDriveCommercial": str(biz),
            "OneDriveConsumer": str(personal),
        },
    )
    paths = sorted(str(c.path) for c in det.find(fs))
    assert str(biz) in paths
    assert str(personal) in paths


# --- Dropbox info.json reading -----------------------------------------------


def test_dropbox_info_json_paths_picked_up(fs: RealFileSystem, envs: tuple[Path, Path]) -> None:
    home, local = envs
    custom = home / "Documents" / "Dropbox"
    custom.mkdir(parents=True)
    info = local / "Dropbox" / "info.json"
    info.parent.mkdir(parents=True)
    info.write_text(json.dumps({"personal": {"path": str(custom)}, "business": {"path": "..."}}))
    det = CloudSyncDetector(home=home, local_appdata=local, environ={})
    paths = [str(c.path) for c in det.find(fs)]
    assert str(custom) in paths


def test_dropbox_info_json_malformed_is_tolerated(
    fs: RealFileSystem, envs: tuple[Path, Path]
) -> None:
    home, local = envs
    (home / "Dropbox").mkdir()
    info = local / "Dropbox" / "info.json"
    info.parent.mkdir(parents=True)
    info.write_text("not valid json {{")
    det = CloudSyncDetector(home=home, local_appdata=local, environ={})
    # Default ~/Dropbox still gets detected; malformed JSON just gets skipped.
    results = det.find(fs)
    assert any(c.category == "cloud_sync:dropbox" for c in results)


def test_dropbox_info_json_with_non_dict_account_skipped(
    fs: RealFileSystem, envs: tuple[Path, Path]
) -> None:
    home, local = envs
    (home / "Dropbox").mkdir()
    info = local / "Dropbox" / "info.json"
    info.parent.mkdir(parents=True)
    info.write_text(json.dumps({"weird": "not-a-dict", "ok": {"path": "no-such"}}))
    det = CloudSyncDetector(home=home, local_appdata=local, environ={})
    # No crash; default Dropbox dir still found.
    results = det.find(fs)
    assert any(c.category == "cloud_sync:dropbox" for c in results)


# --- de-duplication ---------------------------------------------------------


def test_same_path_via_env_and_default_dedup(fs: RealFileSystem, envs: tuple[Path, Path]) -> None:
    """If OneDrive lives at the default path AND the env var points there,
    we should not emit two candidates for the same directory.
    """
    home, local = envs
    one = home / "OneDrive"
    one.mkdir()
    det = CloudSyncDetector(home=home, local_appdata=local, environ={"OneDrive": str(one)})
    results = det.find(fs)
    assert len(results) == 1


# --- never-emits-candidates-for-missing-paths -------------------------------


def test_missing_paths_yield_no_candidates(fs: RealFileSystem, envs: tuple[Path, Path]) -> None:
    home, local = envs
    # No directory exists, but the env var still points at one.
    det = CloudSyncDetector(
        home=home,
        local_appdata=local,
        environ={"OneDrive": str(home / "no-such")},
    )
    assert det.find(fs) == []


def test_path_that_is_a_file_skipped(fs: RealFileSystem, envs: tuple[Path, Path]) -> None:
    home, local = envs
    (home / "OneDrive").write_text("not a directory")
    det = CloudSyncDetector(home=home, local_appdata=local, environ={})
    assert det.find(fs) == []


# --- Candidate carries the right warnings ----------------------------------


def test_candidate_messaging_warns_loudly(fs: RealFileSystem, envs: tuple[Path, Path]) -> None:
    home, local = envs
    (home / "OneDrive").mkdir()
    det = CloudSyncDetector(home=home, local_appdata=local, environ={})
    [c] = det.find(fs)
    assert c.risk == RiskLevel.NEVER
    # Reason text must explicitly warn about the deletion-cascade risk.
    assert "删除同步" in c.reason_zh or "remote" in c.reason_en.lower()


# --- default constructor uses env + Path.home ------------------------------


def test_default_constructor_reads_env_and_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    det = CloudSyncDetector()
    assert det._home == tmp_path / "home"
    assert det._local_appdata == tmp_path / "local"
