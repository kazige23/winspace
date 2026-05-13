"""Detector: IM / chat-app local data directories.

These dirs hold a mix of chat history, voice/video calls, downloaded
files the user can't easily get back, and other content that is
**not** trivially regenerable. We flag them as :class:`RiskLevel.RISKY`
which means:

* They never appear in the default ``winspace scan`` output (you'd need
  ``--include-risky`` to see them)
* ``winspace move`` on one of these paths refuses to execute unless
  the user adds ``--i-know-what-im-doing``

Covered apps (per spec §7 and plan T21):

* 微信 / WeChat       — ``%USERPROFILE%\\Documents\\WeChat Files``
* QQ                  — ``%USERPROFILE%\\Documents\\Tencent Files``
* 钉钉 / DingTalk    — ``%LOCALAPPDATA%\\DingTalk``
* 飞书 / Lark         — ``%LOCALAPPDATA%\\Lark``
* Discord            — ``%APPDATA%\\discord``
* Telegram Desktop    — ``%APPDATA%\\Telegram Desktop``
* WhatsApp           — ``%APPDATA%\\WhatsApp``
* Signal             — ``%APPDATA%\\Signal``
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from winspace.core.fs import FileSystem
from winspace.detectors.base import Candidate, Detector, RiskLevel


@dataclass(frozen=True)
class _IMLocation:
    """One (app, root, sub_path) tuple defining where to look."""

    app: str
    root_key: str  # "home" | "local_appdata" | "appdata" | "documents"
    sub_path: str
    display_zh: str


_LOCATIONS: tuple[_IMLocation, ...] = (
    _IMLocation("wechat", "documents", "WeChat Files", "微信"),
    _IMLocation("qq", "documents", "Tencent Files", "QQ"),
    _IMLocation("dingtalk", "local_appdata", "DingTalk", "钉钉"),
    _IMLocation("lark", "local_appdata", "Lark", "飞书"),
    _IMLocation("discord", "appdata", "discord", "Discord"),
    _IMLocation("telegram", "appdata", "Telegram Desktop", "Telegram"),
    _IMLocation("whatsapp", "appdata", "WhatsApp", "WhatsApp"),
    _IMLocation("signal", "appdata", "Signal", "Signal"),
)


class IMDataDetector(Detector):
    """Emit RISKY candidates for chat-app local data directories."""

    name: ClassVar[str] = "im_data"

    def __init__(
        self,
        *,
        home: Path | None = None,
        local_appdata: Path | None = None,
        appdata: Path | None = None,
        documents: Path | None = None,
    ) -> None:
        self._home = home or Path.home()
        self._local_appdata = local_appdata or _env_path("LOCALAPPDATA")
        self._appdata = appdata or _env_path("APPDATA")
        self._documents = documents or self._home / "Documents"

    def find(self, fs: FileSystem) -> list[Candidate]:
        roots: dict[str, Path] = {
            "home": self._home,
            "local_appdata": self._local_appdata,
            "appdata": self._appdata,
            "documents": self._documents,
        }
        results: list[Candidate] = []
        for loc in _LOCATIONS:
            base = roots[loc.root_key]
            candidate = base / loc.sub_path
            if not fs.exists(candidate) or not fs.is_dir(candidate):
                continue
            if fs.is_reparse_point(candidate):
                continue
            results.append(_make_risky(candidate, loc))
        return results


def _make_risky(path: Path, loc: _IMLocation) -> Candidate:
    reason_zh = (
        f"{loc.display_zh}本地数据,含聊天记录与不可再生文件;"
        "默认不显示,需要 --i-know-what-im-doing 才能迁移"
    )
    reason_en = (
        f"{loc.display_zh} ({loc.app}) local data — chat history + "
        "non-regenerable downloads. Hidden by default; requires "
        "--i-know-what-im-doing to move."
    )
    return Candidate(
        path=path,
        category=f"im_data:{loc.app}",
        risk=RiskLevel.RISKY,
        reason_zh=reason_zh,
        reason_en=reason_en,
        detector_name="im_data",
        prerequisite_note_zh="迁移前务必关闭对应应用并备份重要文件",
        prerequisite_note_en="Close the app and back up important files before relocating",
    )


def _env_path(name: str) -> Path:
    value = os.environ.get(name)
    if value:
        return Path(value)
    return Path.home()
