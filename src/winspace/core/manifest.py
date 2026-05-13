"""Manifest of every winspace move operation.

Stored as JSON at ``%APPDATA%\\winspace\\manifest.json``. Read on
every operation, written atomically (``.tmp`` + ``os.replace``). The
schema carries a ``version`` field reserved for future migrations; a
manifest written by an incompatible future version is refused with a
clear error.

A corrupted JSON file does not block the tool — it is moved aside as
``manifest.json.broken-<utc-ts>`` and replaced with an empty manifest.
The user can still inspect the broken copy for forensics. Schema
errors in well-formed JSON are NOT auto-recovered; instead we raise
:class:`ManifestError` so the user sees the actual problem.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from winspace.core.errors import ManifestError
from winspace.core.fs import FileSystem, RealFileSystem

MANIFEST_VERSION = 1


class EntryStatus(StrEnum):
    """Lifecycle of a single move record."""

    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    BROKEN = "broken"


@dataclass
class ManifestEntry:
    """One recorded move operation."""

    id: str
    timestamp: str  # ISO 8601 UTC
    original_path: str
    new_path: str
    size_bytes: int
    file_count: int
    tree_hash: str
    status: EntryStatus
    cleanup_pending: bool = False

    @classmethod
    def new(
        cls,
        *,
        original_path: Path | str,
        new_path: Path | str,
        size_bytes: int,
        file_count: int,
        tree_hash: str,
    ) -> ManifestEntry:
        """Construct an entry with a fresh UUID and current UTC timestamp."""
        return cls(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
            original_path=str(original_path),
            new_path=str(new_path),
            size_bytes=size_bytes,
            file_count=file_count,
            tree_hash=tree_hash,
            status=EntryStatus.ACTIVE,
            cleanup_pending=False,
        )


@dataclass
class Manifest:
    """Top-level manifest document."""

    version: int = MANIFEST_VERSION
    entries: list[ManifestEntry] = field(default_factory=list)

    def find_by_id(self, entry_id: str) -> ManifestEntry | None:
        """Locate an entry by UUID, or ``None`` if not present."""
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def active(self) -> list[ManifestEntry]:
        """All active (not rolled-back / not broken) entries."""
        return [e for e in self.entries if e.status == EntryStatus.ACTIVE]


def default_manifest_path() -> Path:
    """Return the OS-appropriate default location.

    Windows uses ``%APPDATA%\\winspace\\manifest.json``; everywhere else
    (mostly used in tests / Linux dev) falls back to
    ``~/.local/share/winspace/manifest.json``.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "winspace" / "manifest.json"
    return Path.home() / ".local" / "share" / "winspace" / "manifest.json"


def load(path: Path | None = None, *, fs: FileSystem | None = None) -> Manifest:
    """Load the manifest, returning an empty one if it does not exist.

    On JSON corruption the file is renamed aside with a ``.broken-<utc>``
    suffix and an empty manifest is returned. Schema mismatches raise
    :class:`ManifestError` so the user can investigate.
    """
    fs = fs or RealFileSystem()
    path = path or default_manifest_path()

    if not fs.exists(path):
        return Manifest()

    try:
        raw = fs.read_text(path)
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        _backup_broken_manifest(path, fs=fs)
        return Manifest()

    try:
        return _from_dict(data)
    except (KeyError, ValueError, TypeError) as e:
        raise ManifestError(f"manifest schema invalid: {e}") from e


def save(
    manifest: Manifest,
    path: Path | None = None,
    *,
    fs: FileSystem | None = None,
) -> None:
    """Persist ``manifest`` atomically. Creates parent directories as needed."""
    fs = fs or RealFileSystem()
    path = path or default_manifest_path()

    fs.mkdir(path.parent, parents=True, exist_ok=True)
    payload = json.dumps(_to_dict(manifest), indent=2, ensure_ascii=False)
    fs.write_text_atomic(path, payload)


def append_entry(
    entry: ManifestEntry,
    path: Path | None = None,
    *,
    fs: FileSystem | None = None,
) -> Manifest:
    """Load the manifest, append ``entry``, and save. Returns the new manifest."""
    manifest = load(path, fs=fs)
    manifest.entries.append(entry)
    save(manifest, path, fs=fs)
    return manifest


def update_status(
    entry_id: str,
    status: EntryStatus,
    path: Path | None = None,
    *,
    fs: FileSystem | None = None,
    cleanup_pending: bool | None = None,
) -> Manifest:
    """Set ``status`` (and optionally ``cleanup_pending``) on the named entry."""
    manifest = load(path, fs=fs)
    found = manifest.find_by_id(entry_id)
    if found is None:
        raise ManifestError(f"no manifest entry with id={entry_id}")
    found.status = status
    if cleanup_pending is not None:
        found.cleanup_pending = cleanup_pending
    save(manifest, path, fs=fs)
    return manifest


# --- helpers -----------------------------------------------------------------


def _backup_broken_manifest(path: Path, *, fs: FileSystem) -> None:
    """Rename a corrupted manifest aside; swallow rename failures."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(path.name + f".broken-{ts}")
    try:
        fs.rename(path, backup)
    except OSError:
        # If we can't even rename, give up — the next save will overwrite.
        return


def _to_dict(manifest: Manifest) -> dict[str, Any]:
    return {
        "version": manifest.version,
        "entries": [
            {
                "id": e.id,
                "timestamp": e.timestamp,
                "original_path": e.original_path,
                "new_path": e.new_path,
                "size_bytes": e.size_bytes,
                "file_count": e.file_count,
                "tree_hash": e.tree_hash,
                "status": e.status.value,
                "cleanup_pending": e.cleanup_pending,
            }
            for e in manifest.entries
        ],
    }


def _from_dict(data: Any) -> Manifest:
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object at root, got {type(data).__name__}")

    version = data.get("version")
    if not isinstance(version, int):
        raise ValueError(f"missing or non-int version: {version!r}")
    if version != MANIFEST_VERSION:
        raise ManifestError(
            f"unsupported manifest version: {version} "
            f"(this winspace understands version {MANIFEST_VERSION})"
        )

    raw_entries = data.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ValueError(f"'entries' must be a list, got {type(raw_entries).__name__}")

    entries: list[ManifestEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("each entry must be an object")
        entries.append(
            ManifestEntry(
                id=str(raw["id"]),
                timestamp=str(raw["timestamp"]),
                original_path=str(raw["original_path"]),
                new_path=str(raw["new_path"]),
                size_bytes=int(raw["size_bytes"]),
                file_count=int(raw["file_count"]),
                tree_hash=str(raw["tree_hash"]),
                status=EntryStatus(raw["status"]),
                cleanup_pending=bool(raw.get("cleanup_pending", False)),
            )
        )
    return Manifest(version=version, entries=entries)
