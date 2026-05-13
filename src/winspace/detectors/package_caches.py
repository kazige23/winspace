"""Detector: developer-tool global caches.

Targets caches that ``npm install`` / ``pip install`` / ``cargo build``
will re-download or re-build on demand — i.e. cheap to lose, often
larger than expected, and the most common quick win when freeing
space on a developer machine.

Layout per tool:

* pip       : ``%LOCALAPPDATA%\\pip\\cache``
* npm       : ``%APPDATA%\\npm-cache`` and ``%LOCALAPPDATA%\\npm-cache``
* yarn      : ``%LOCALAPPDATA%\\Yarn\\Cache``
* pnpm      : ``%LOCALAPPDATA%\\pnpm\\store``
* cargo     : ``~/.cargo/registry/cache``
* gradle    : ``~/.gradle/caches``
* maven     : ``~/.m2/repository``
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from winspace.core.fs import FileSystem
from winspace.core.safety import is_never
from winspace.detectors.base import Candidate, Detector, RiskLevel


@dataclass(frozen=True)
class _ToolLocation:
    """One concrete (tool, relative-path, root) tuple for the detector."""

    tool: str
    root_key: str  # "home" | "local_appdata" | "appdata"
    sub_path: str
    reason_zh: str
    reason_en: str


_LOCATIONS: tuple[_ToolLocation, ...] = (
    _ToolLocation(
        tool="pip",
        root_key="local_appdata",
        sub_path="pip/cache",
        reason_zh="pip 包缓存,可由 pip install 重建",
        reason_en="pip wheel cache; rebuilt by `pip install`",
    ),
    _ToolLocation(
        tool="npm",
        root_key="appdata",
        sub_path="npm-cache",
        reason_zh="npm 包缓存,可由 npm install 重建",
        reason_en="npm package cache; rebuilt by `npm install`",
    ),
    _ToolLocation(
        tool="npm",
        root_key="local_appdata",
        sub_path="npm-cache",
        reason_zh="npm 包缓存,可由 npm install 重建",
        reason_en="npm package cache; rebuilt by `npm install`",
    ),
    _ToolLocation(
        tool="yarn",
        root_key="local_appdata",
        sub_path="Yarn/Cache",
        reason_zh="yarn 包缓存,可由 yarn install 重建",
        reason_en="yarn package cache; rebuilt by `yarn install`",
    ),
    _ToolLocation(
        tool="pnpm",
        root_key="local_appdata",
        sub_path="pnpm/store",
        reason_zh="pnpm 内容寻址存储,可由 pnpm install 重建",
        reason_en="pnpm content-addressed store; rebuilt by `pnpm install`",
    ),
    _ToolLocation(
        tool="cargo",
        root_key="home",
        sub_path=".cargo/registry/cache",
        reason_zh="Cargo 注册表缓存,可由 cargo build 重建",
        reason_en="Cargo registry cache; rebuilt by `cargo build`",
    ),
    _ToolLocation(
        tool="gradle",
        root_key="home",
        sub_path=".gradle/caches",
        reason_zh="Gradle 全局缓存,下次构建会重新下载依赖",
        reason_en="Gradle global cache; dependencies re-downloaded on next build",
    ),
    _ToolLocation(
        tool="maven",
        root_key="home",
        sub_path=".m2/repository",
        reason_zh="Maven 本地仓库,下次构建会重新下载依赖",
        reason_en="Maven local repository; dependencies re-downloaded on next build",
    ),
)


class PackageCachesDetector(Detector):
    """Locate every dev-tool global cache that's safe to relocate."""

    name: ClassVar[str] = "package_caches"

    def __init__(
        self,
        *,
        home: Path | None = None,
        local_appdata: Path | None = None,
        appdata: Path | None = None,
    ) -> None:
        self._home = home or Path.home()
        self._local_appdata = local_appdata or _env_path("LOCALAPPDATA")
        self._appdata = appdata or _env_path("APPDATA")

    def find(self, fs: FileSystem) -> list[Candidate]:
        roots: dict[str, Path] = {
            "home": self._home,
            "local_appdata": self._local_appdata,
            "appdata": self._appdata,
        }
        results: list[Candidate] = []
        for loc in _LOCATIONS:
            base = roots[loc.root_key]
            # sub_path uses '/' for portability; pathlib handles it.
            candidate = base.joinpath(*loc.sub_path.split("/"))
            if not fs.exists(candidate) or not fs.is_dir(candidate):
                continue
            if fs.is_reparse_point(candidate):
                continue
            blocked, _ = is_never(candidate)
            if blocked:
                continue
            results.append(
                Candidate(
                    path=candidate,
                    category=f"package_cache:{loc.tool}",
                    risk=RiskLevel.SAFE,
                    reason_zh=loc.reason_zh,
                    reason_en=loc.reason_en,
                    detector_name=self.name,
                    prerequisite_note_zh="迁移前建议先停止后台构建或 IDE",
                    prerequisite_note_en="Pause active builds / IDE before relocating",
                    deletable=True,
                )
            )
        return results


def _env_path(name: str) -> Path:
    value = os.environ.get(name)
    if value:
        return Path(value)
    return Path.home()
