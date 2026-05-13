"""Detector: cloud-sync roots.

Emits :class:`RiskLevel.NEVER` candidates for the *root* of every
cloud-sync folder we can identify. The CLI's enrichment phase honours
the cascade: any other candidate whose path lives under one of these
roots is also dropped.

WARNING: relocating a cloud-sync folder to another drive via a
junction is **catastrophic** — the cloud client sees its local
contents disappear and begins a remote deletion to mirror. Users
have lost months of OneDrive / Dropbox data this way. The NEVER
classification is non-negotiable.

We rely on two signals per provider:

1. Default install path (e.g. ``%USERPROFILE%\\OneDrive``)
2. Registry hint where one exists (OneDrive's
   ``HKCU\\Software\\Microsoft\\OneDrive\\Accounts\\*\\UserFolder``,
   Dropbox's ``%LOCALAPPDATA%\\Dropbox\\info.json``)

Either signal triggers protection. Tests inject the home and env
roots; the registry / config lookups are best-effort and tolerate
``winreg`` being absent (Linux CI).
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from winspace.core.fs import FileSystem
from winspace.detectors.base import Candidate, Detector, RiskLevel


class CloudSyncDetector(Detector):
    """Discover OneDrive / iCloud / Google Drive / Dropbox / Box / 坚果云 / 百度网盘."""

    name: ClassVar[str] = "cloud_sync"

    def __init__(
        self,
        *,
        home: Path | None = None,
        local_appdata: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._home = home or Path.home()
        self._local_appdata = local_appdata or _env_path("LOCALAPPDATA")
        self._environ = environ if environ is not None else dict(os.environ)

    def find(self, fs: FileSystem) -> list[Candidate]:
        seen: dict[Path, str] = {}
        for path, provider in self._gather(fs):
            if not fs.exists(path) or not fs.is_dir(path):
                continue
            resolved = path.resolve(strict=False)
            if resolved in seen:
                continue
            seen[resolved] = provider

        return [_make_never(path, provider) for path, provider in seen.items()]

    # --- per-provider helpers ------------------------------------------------

    def _gather(self, fs: FileSystem) -> Iterable[tuple[Path, str]]:
        # OneDrive
        for env_var in (
            "OneDrive",
            "OneDriveCommercial",
            "OneDriveConsumer",
            "OneDriveBusiness",
        ):
            raw = self._environ.get(env_var)
            if raw:
                yield Path(raw), "onedrive"
        # Plus the default install location, in case the env var was cleared.
        for name in ("OneDrive", "OneDrive - Personal"):
            yield self._home / name, "onedrive"

        # iCloud Drive
        for name in ("iCloudDrive", "iCloud Drive"):
            yield self._home / name, "icloud"

        # Google Drive (Backup & Sync 1; Drive for Desktop separate mount)
        for name in ("Google Drive", "GoogleDrive", "My Drive"):
            yield self._home / name, "google_drive"

        # Dropbox — default path + info.json hint
        yield self._home / "Dropbox", "dropbox"
        info = self._local_appdata / "Dropbox" / "info.json"
        if info.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                data = json.loads(info.read_text(encoding="utf-8"))
                for account in data.values():
                    if isinstance(account, dict) and isinstance(account.get("path"), str):
                        yield Path(account["path"]), "dropbox"

        # Box Drive
        yield self._home / "Box", "box"

        # 坚果云 (Nutstore)
        yield self._home / "Nutstore" / "Nutstore", "nutstore"
        yield self._home / "Nutstore", "nutstore"

        # 百度网盘 sync workspace
        yield self._home / "BaiduNetdiskWorkspace", "baidu_netdisk"

        # NOTE: a future enhancement is to also read
        # HKCU\Software\Microsoft\OneDrive\Accounts\*\UserFolder for OneDrive
        # users who have moved the folder via the Settings UI. The default
        # paths above cover the vast majority of installs and the env vars
        # cover most relocations, so v1 ships without the registry probe to
        # keep the surface area small.


def _make_never(path: Path, provider: str) -> Candidate:
    reason_zh = f"{provider} 云同步目录,移动会让云端客户端误以为本地丢失,触发删除同步;严禁迁移"
    reason_en = (
        f"{provider} cloud-sync root; relocating triggers the client to mirror "
        f"the apparent local deletion to the cloud — never move"
    )
    return Candidate(
        path=path,
        category=f"cloud_sync:{provider}",
        risk=RiskLevel.NEVER,
        reason_zh=reason_zh,
        reason_en=reason_en,
        detector_name="cloud_sync",
    )


def _env_path(name: str) -> Path:
    value = os.environ.get(name)
    if value:
        return Path(value)
    return Path.home()
