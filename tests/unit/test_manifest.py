"""Unit tests for :mod:`winspace.core.manifest`.

Coverage target ≥ 90%. Tests use real ``tmp_path`` for IO; the manifest
location is overridden so the user's real ``%APPDATA%\\winspace`` is
never touched by the test suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from winspace.core.errors import ManifestError
from winspace.core.fs import RealFileSystem
from winspace.core.manifest import (
    MANIFEST_VERSION,
    EntryStatus,
    Manifest,
    ManifestEntry,
    append_entry,
    default_manifest_path,
    load,
    save,
    update_status,
)

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    return tmp_path / "manifest.json"


@pytest.fixture
def sample_entry() -> ManifestEntry:
    return ManifestEntry.new(
        original_path="C:\\Users\\me\\node_modules",
        new_path="D:\\winspace\\node_modules",
        size_bytes=12345,
        file_count=42,
        tree_hash="deadbeef",
    )


# --- ManifestEntry construction ---------------------------------------------


def test_entry_new_generates_unique_id_and_timestamp() -> None:
    e1 = ManifestEntry.new(
        original_path="a", new_path="b", size_bytes=1, file_count=1, tree_hash="x"
    )
    e2 = ManifestEntry.new(
        original_path="a", new_path="b", size_bytes=1, file_count=1, tree_hash="x"
    )
    assert e1.id != e2.id
    assert e1.status == EntryStatus.ACTIVE
    assert e1.cleanup_pending is False
    assert e1.timestamp  # non-empty ISO 8601 string


# --- load on missing file ----------------------------------------------------


def test_load_returns_empty_when_file_missing(manifest_path: Path) -> None:
    m = load(manifest_path)
    assert isinstance(m, Manifest)
    assert m.entries == []
    assert m.version == MANIFEST_VERSION


# --- round-trip save / load --------------------------------------------------


def test_save_then_load_roundtrip(manifest_path: Path, sample_entry: ManifestEntry) -> None:
    m = Manifest(entries=[sample_entry])
    save(m, manifest_path)
    loaded = load(manifest_path)
    assert len(loaded.entries) == 1
    [round_tripped] = loaded.entries
    assert round_tripped.id == sample_entry.id
    assert round_tripped.original_path == sample_entry.original_path
    assert round_tripped.size_bytes == sample_entry.size_bytes
    assert round_tripped.status == EntryStatus.ACTIVE


# --- save creates parent directories ----------------------------------------


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "manifest.json"
    save(Manifest(), nested)
    assert nested.is_file()


# --- atomic write: no .tmp residue -------------------------------------------


def test_atomic_write_leaves_no_temp(manifest_path: Path) -> None:
    save(Manifest(), manifest_path)
    assert manifest_path.is_file()
    assert not manifest_path.with_name(manifest_path.name + ".tmp").exists()


# --- append + find_by_id -----------------------------------------------------


def test_append_entry_persists(manifest_path: Path, sample_entry: ManifestEntry) -> None:
    append_entry(sample_entry, manifest_path)
    reloaded = load(manifest_path)
    assert reloaded.find_by_id(sample_entry.id) is not None


def test_find_by_id_returns_none_for_missing() -> None:
    m = Manifest()
    assert m.find_by_id("does-not-exist") is None


def test_active_filters_status() -> None:
    e1 = ManifestEntry(
        id="a",
        timestamp="t",
        original_path="o",
        new_path="n",
        size_bytes=1,
        file_count=1,
        tree_hash="x",
        status=EntryStatus.ACTIVE,
    )
    e2 = ManifestEntry(
        id="b",
        timestamp="t",
        original_path="o",
        new_path="n",
        size_bytes=1,
        file_count=1,
        tree_hash="x",
        status=EntryStatus.ROLLED_BACK,
    )
    e3 = ManifestEntry(
        id="c",
        timestamp="t",
        original_path="o",
        new_path="n",
        size_bytes=1,
        file_count=1,
        tree_hash="x",
        status=EntryStatus.BROKEN,
    )
    m = Manifest(entries=[e1, e2, e3])
    assert m.active() == [e1]


# --- update_status -----------------------------------------------------------


def test_update_status_changes_entry(manifest_path: Path, sample_entry: ManifestEntry) -> None:
    append_entry(sample_entry, manifest_path)
    update_status(sample_entry.id, EntryStatus.ROLLED_BACK, manifest_path)
    reloaded = load(manifest_path)
    found = reloaded.find_by_id(sample_entry.id)
    assert found is not None
    assert found.status == EntryStatus.ROLLED_BACK


def test_update_status_can_set_cleanup_pending(
    manifest_path: Path, sample_entry: ManifestEntry
) -> None:
    append_entry(sample_entry, manifest_path)
    update_status(sample_entry.id, EntryStatus.ACTIVE, manifest_path, cleanup_pending=True)
    reloaded = load(manifest_path)
    found = reloaded.find_by_id(sample_entry.id)
    assert found is not None
    assert found.cleanup_pending is True


def test_update_status_missing_id_raises(manifest_path: Path) -> None:
    save(Manifest(), manifest_path)
    with pytest.raises(ManifestError, match="no manifest entry"):
        update_status("ghost-id", EntryStatus.ROLLED_BACK, manifest_path)


# --- corruption: JSON parse error -> backup + empty ------------------------


def test_corrupted_json_is_moved_aside(manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{not valid json")
    m = load(manifest_path)
    assert m.entries == []
    # A backup file with .broken-<ts> suffix must exist.
    backups = list(manifest_path.parent.glob(manifest_path.name + ".broken-*"))
    assert backups, "expected a .broken-<ts> backup file"


def test_corrupted_json_when_rename_fails_does_not_crash(
    manifest_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the corruption-rename fails, we still return an empty manifest."""
    manifest_path.write_text("{not valid json")

    def boom_rename(self: RealFileSystem, old: Path, new: Path) -> None:
        raise OSError("rename denied")

    monkeypatch.setattr(RealFileSystem, "rename", boom_rename)
    m = load(manifest_path)
    assert m.entries == []


# --- schema validation: well-formed JSON, wrong shape -> raise --------------


def test_unsupported_version_raises(manifest_path: Path) -> None:
    manifest_path.write_text(json.dumps({"version": 999, "entries": []}))
    with pytest.raises(ManifestError, match="unsupported manifest version"):
        load(manifest_path)


def test_missing_version_raises(manifest_path: Path) -> None:
    manifest_path.write_text(json.dumps({"entries": []}))
    with pytest.raises(ManifestError, match="missing or non-int version"):
        load(manifest_path)


def test_non_object_root_raises(manifest_path: Path) -> None:
    manifest_path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ManifestError, match="JSON object at root"):
        load(manifest_path)


def test_entries_not_list_raises(manifest_path: Path) -> None:
    manifest_path.write_text(json.dumps({"version": MANIFEST_VERSION, "entries": "oops"}))
    with pytest.raises(ManifestError, match="entries"):
        load(manifest_path)


def test_entry_not_object_raises(manifest_path: Path) -> None:
    manifest_path.write_text(json.dumps({"version": MANIFEST_VERSION, "entries": ["nope"]}))
    with pytest.raises(ManifestError):
        load(manifest_path)


def test_entry_missing_required_field_raises(manifest_path: Path) -> None:
    manifest_path.write_text(
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "entries": [{"id": "x"}],  # missing everything else
            }
        )
    )
    with pytest.raises(ManifestError, match="manifest schema invalid"):
        load(manifest_path)


# --- default_manifest_path --------------------------------------------------


def test_default_path_uses_appdata_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    result = default_manifest_path()
    assert result == tmp_path / "winspace" / "manifest.json"


def test_default_path_falls_back_when_no_appdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = default_manifest_path()
    assert result == tmp_path / ".local" / "share" / "winspace" / "manifest.json"


# --- preserves unknown extra fields gracefully -------------------------------


def test_cleanup_pending_defaults_when_missing_in_json(
    manifest_path: Path,
) -> None:
    """Older manifests without cleanup_pending must load successfully."""
    manifest_path.write_text(
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "entries": [
                    {
                        "id": "x",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "original_path": "a",
                        "new_path": "b",
                        "size_bytes": 1,
                        "file_count": 1,
                        "tree_hash": "h",
                        "status": "active",
                        # no cleanup_pending
                    }
                ],
            }
        )
    )
    m = load(manifest_path)
    assert m.entries[0].cleanup_pending is False


# --- unicode round-trip ------------------------------------------------------


def test_unicode_paths_round_trip(manifest_path: Path) -> None:
    entry = ManifestEntry.new(
        original_path="C:\\用户\\下载",
        new_path="D:\\winspace\\下载",
        size_bytes=999,
        file_count=3,
        tree_hash="字符串",
    )
    append_entry(entry, manifest_path)
    reloaded = load(manifest_path)
    assert reloaded.entries[0].original_path == "C:\\用户\\下载"
    assert reloaded.entries[0].tree_hash == "字符串"
