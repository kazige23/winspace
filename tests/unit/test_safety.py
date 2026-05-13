"""Unit tests for :mod:`winspace.core.safety`.

Coverage target is ≥ 95% because this module is the last line of
defense before any destructive operation; an undetected bug here can
let winspace touch a system path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from winspace.core.safety import is_never, rule_names

# --- positive matches: each rule must trigger ---------------------------------


@pytest.mark.parametrize(
    ("path", "expected_rule"),
    [
        ("C:\\Windows", "windows-system-dir"),
        ("C:\\Windows\\System32", "windows-system-dir"),
        ("C:\\windows\\system32\\drivers\\etc", "windows-system-dir"),
        ("C:\\Program Files", "program-files"),
        ("C:\\Program Files\\Adobe\\Reader", "program-files"),
        ("C:\\Program Files (x86)", "program-files-x86"),
        ("C:\\Program Files (x86)\\Steam", "program-files-x86"),
        ("C:\\ProgramData\\Microsoft", "programdata-microsoft"),
        ("C:\\ProgramData\\Microsoft\\Windows Defender", "programdata-microsoft"),
        ("C:\\$Recycle.Bin", "recycle-bin"),
        ("C:\\$Recycle.Bin\\S-1-5-21-foo", "recycle-bin"),
        ("C:\\System Volume Information", "system-volume-info"),
        ("D:\\System Volume Information", "system-volume-info"),
        ("C:\\hiberfil.sys", "hibernation-file"),
        ("C:\\pagefile.sys", "page-file"),
        ("C:\\swapfile.sys", "swap-file"),
    ],
)
def test_never_rules_match_canonical_paths(path: str, expected_rule: str) -> None:
    blocked, rule = is_never(path)
    assert blocked is True
    assert rule == expected_rule


# --- per-drive: NEVER rules ignore the drive letter --------------------------


def test_rules_match_on_any_drive() -> None:
    # Imagine a system where Windows got installed on D:. The rules
    # protect by name, not by drive letter — defense in depth.
    assert is_never("D:\\Windows\\System32") == (True, "windows-system-dir")
    assert is_never("E:\\$Recycle.Bin") == (True, "recycle-bin")


# --- negative matches: similar names must NOT match --------------------------


@pytest.mark.parametrize(
    "path",
    [
        "C:\\Windows.bak",
        "C:\\Windows-old",
        "C:\\Users\\me\\Downloads",
        "C:\\Users\\me\\AppData\\Local\\Foo",
        "C:\\Users\\me\\Documents\\Windows.txt",
        "C:\\ProgramData\\Anaconda3",
        "C:\\ProgramData",  # the parent of the protected Microsoft subtree
        "C:\\Recycle.Bin",  # missing the $
        "C:\\pagefile.sys.bak",
        "D:\\Games\\Steam",
    ],
)
def test_safe_paths_do_not_match(path: str) -> None:
    blocked, rule = is_never(path)
    assert blocked is False
    assert rule == ""


# --- case insensitivity ------------------------------------------------------


def test_case_insensitive_matching() -> None:
    assert is_never("C:\\WiNdOwS\\sYsTeM32") == (True, "windows-system-dir")
    assert is_never("c:\\PROGRAM FILES (X86)") == (True, "program-files-x86")
    assert is_never("c:\\PROGRAMDATA\\microsoft") == (True, "programdata-microsoft")


# --- slash / separator normalisation -----------------------------------------


def test_forward_slashes_normalised() -> None:
    """``pathlib.Path`` collapses ``/`` and ``\\`` so safety doesn't care."""
    blocked, rule = is_never("C:/Windows/System32")
    assert blocked is True
    assert rule == "windows-system-dir"


# --- drive-root and very short paths -----------------------------------------


def test_drive_root_alone_is_not_blocked() -> None:
    # The drive root itself ("C:\\") is not on the NEVER list — we'd
    # never try to relocate it, but is_never shouldn't lie.
    blocked, rule = is_never("C:\\")
    assert blocked is False
    assert rule == ""


# --- pathological inputs all fail safe ---------------------------------------


def test_none_input_blocked_as_invalid() -> None:
    assert is_never(None) == (True, "invalid-input")


def test_empty_string_blocked_as_invalid() -> None:
    assert is_never("") == (True, "invalid-input")


def test_null_byte_input_blocked_as_invalid() -> None:
    """A path containing a NUL byte cannot be a real filesystem entry."""
    blocked, rule = is_never("C:\\foo\x00bar")
    assert blocked is True
    assert rule == "invalid-input"


def test_resolve_oserror_blocked_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Path.resolve() raises OSError we must still fail safe, not crash."""

    def boom(self: Path, *_: object, **__: object) -> Path:
        raise OSError("simulated")

    monkeypatch.setattr(Path, "resolve", boom)
    blocked, rule = is_never("C:\\does\\not\\matter")
    assert blocked is True
    assert rule == "invalid-input"


def test_resolve_runtimeerror_blocked_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: Path, *_: object, **__: object) -> Path:
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(Path, "resolve", boom)
    blocked, rule = is_never("C:\\loop")
    assert blocked is True
    assert rule == "invalid-input"


# --- Path object input is accepted too ---------------------------------------


def test_path_object_input(tmp_path: Path) -> None:
    """is_never accepts both str and Path."""
    blocked, _ = is_never(tmp_path)
    assert blocked is False  # tmp_path is under user temp, not a system path


def test_rule_names_includes_all_rules_plus_invalid() -> None:
    names = rule_names()
    # 9 hardcoded rules + the invalid-input sentinel.
    assert len(names) == 10
    assert "windows-system-dir" in names
    assert "invalid-input" in names
    # No duplicates.
    assert len(set(names)) == len(names)


# --- non-existent paths still match by segments ------------------------------


def test_non_existent_path_still_matched() -> None:
    """is_never never touches the filesystem — segment match is enough."""
    blocked, rule = is_never("Z:\\Windows\\System32\\nope-not-real.exe")
    assert blocked is True
    assert rule == "windows-system-dir"


# --- regression: parts indexing must be safe for very short inputs ----------


def test_relative_single_segment_resolved_to_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bare segment like ``Windows`` resolves under cwd, which is unlikely
    to look like a system path on a test runner — must not crash.
    """
    monkeypatch.chdir(tmp_path)
    blocked, _ = is_never("Windows")
    # Either result is acceptable as long as the call returns cleanly.
    assert isinstance(blocked, bool)


# --- documentation: rules are stable ----------------------------------------


def test_rules_cover_all_spec_section_7_hardcoded_entries() -> None:
    """The hardcoded NEVER list must match spec.md §7's enumeration."""
    expected = {
        "windows-system-dir",
        "program-files",
        "program-files-x86",
        "programdata-microsoft",
        "recycle-bin",
        "system-volume-info",
        "hibernation-file",
        "page-file",
        "swap-file",
    }
    actual = set(rule_names()) - {"invalid-input"}
    assert actual == expected


# --- API hardening: Any unusual type gets rejected cleanly -------------------


def test_unsupported_type_handled() -> None:
    """Passing something Path() cannot consume must fail safe."""
    # mypy: silenced because we are intentionally testing runtime safety.
    weird: Any = 12345
    blocked, rule = is_never(weird)
    assert blocked is True
    assert rule == "invalid-input"
