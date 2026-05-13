"""Detector: browser caches (Chrome / Edge / Firefox).

We target the *specific* cache sub-directories that browsers
regenerate freely (Cache, Code Cache, GPUCache, Firefox's cache2) —
never the whole profile directory, which holds bookmarks, history,
passwords, and extension data.

Multiple profiles per browser are supported (Chrome / Edge's
``Default``, ``Profile 1``, ``Profile 2``, …, and every Firefox
profile directory under ``Profiles/<random>.<name>``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from winspace.core.fs import FileSystem
from winspace.core.safety import is_never
from winspace.detectors.base import Candidate, Detector, RiskLevel

# Cache sub-directories that Chrome / Edge regenerate freely. We avoid
# the larger profile-private folders (History, Cookies, Login Data, …)
# because moving those silently can break the profile.
_CHROMIUM_CACHE_SUBDIRS = ("Cache", "Code Cache", "GPUCache")

_REASON_ZH = "浏览器缓存,关闭浏览器后可随时移动,下次访问会重新生成"
_REASON_EN = "Browser cache, regenerated on next visit; safe to relocate when the browser is closed"


class BrowserCacheDetector(Detector):
    """Locate Chromium-family (Chrome/Edge) + Firefox cache directories."""

    name: ClassVar[str] = "browser_cache"

    def __init__(
        self,
        *,
        local_appdata: Path | None = None,
        appdata: Path | None = None,
    ) -> None:
        self._local_appdata = local_appdata or _env_path("LOCALAPPDATA")
        self._appdata = appdata or _env_path("APPDATA")

    def find(self, fs: FileSystem) -> list[Candidate]:
        out: list[Candidate] = []
        out.extend(self._find_chromium(fs, self._local_appdata / "Google" / "Chrome"))
        out.extend(self._find_chromium(fs, self._local_appdata / "Microsoft" / "Edge"))
        out.extend(self._find_firefox(fs, self._appdata / "Mozilla" / "Firefox"))
        return out

    # --- Chromium-family helpers --------------------------------------------

    def _find_chromium(self, fs: FileSystem, browser_root: Path) -> list[Candidate]:
        user_data = browser_root / "User Data"
        if not fs.exists(user_data) or not fs.is_dir(user_data):
            return []
        results: list[Candidate] = []
        try:
            profiles = list(fs.iterdir(user_data))
        except (PermissionError, OSError):
            return []
        for profile in profiles:
            if not fs.is_dir(profile):
                continue
            if not _looks_like_chromium_profile(profile.name):
                continue
            for sub in _CHROMIUM_CACHE_SUBDIRS:
                candidate_path = profile / sub
                if not fs.exists(candidate_path) or not fs.is_dir(candidate_path):
                    continue
                if fs.is_reparse_point(candidate_path):
                    continue
                blocked, _ = is_never(candidate_path)
                if blocked:
                    continue
                results.append(self._make_candidate(candidate_path))
        return results

    def _find_firefox(self, fs: FileSystem, firefox_root: Path) -> list[Candidate]:
        profiles_root = firefox_root / "Profiles"
        if not fs.exists(profiles_root) or not fs.is_dir(profiles_root):
            return []
        results: list[Candidate] = []
        try:
            profiles = list(fs.iterdir(profiles_root))
        except (PermissionError, OSError):
            return []
        for profile in profiles:
            if not fs.is_dir(profile):
                continue
            cache_dir = profile / "cache2"
            if not fs.exists(cache_dir) or not fs.is_dir(cache_dir):
                continue
            if fs.is_reparse_point(cache_dir):
                continue
            blocked, _ = is_never(cache_dir)
            if blocked:
                continue
            results.append(self._make_candidate(cache_dir))
        return results

    def _make_candidate(self, path: Path) -> Candidate:
        return Candidate(
            path=path,
            category="browser_cache",
            risk=RiskLevel.SAFE,
            reason_zh=_REASON_ZH,
            reason_en=_REASON_EN,
            detector_name=self.name,
            prerequisite_note_zh="迁移前请先关闭对应浏览器",
            prerequisite_note_en="Close the browser before relocating",
        )


def _env_path(name: str) -> Path:
    """Read an environment variable as a Path, falling back to ~ if unset.

    Returning Path(home) rather than raising lets the detector run on
    Linux CI where LOCALAPPDATA / APPDATA don't exist — find() will
    just yield nothing because the Windows-shaped directories aren't
    there.
    """
    value = os.environ.get(name)
    if value:
        return Path(value)
    return Path.home()


def _looks_like_chromium_profile(name: str) -> bool:
    """Names that Chrome/Edge use for profile directories."""
    if name in ("Default", "Guest Profile", "System Profile"):
        return True
    return name.startswith("Profile ")
