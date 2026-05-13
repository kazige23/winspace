"""Hard NEVER-touch path rules.

The :func:`is_never` predicate is the last line of defense before any
write operation. Even when the user has explicitly typed
``winspace move <path>``, the path must pass through this check first;
a match means immediate refusal.

Dynamic NEVER detection — cloud sync folders (OneDrive, iCloud, etc.)
and IM data directories — lives in dedicated detectors that emit NEVER
candidates so the scanner excludes whole subtrees. The rules here cover
paths that are NEVER safe regardless of user setup.

Matching is segment-wise and case-insensitive: ``C:\\Windows``,
``C:\\Windows\\System32``, and ``c:\\windows`` all hit the
``windows-system-dir`` rule, but ``C:\\Windows.bak`` does not.
"""

from __future__ import annotations

from pathlib import Path

# Each rule is (relative-to-drive segments, rule_name). Segments are
# stored lowercase; the input path is also lowercased at match time so
# the comparison is platform-stable.
_NEVER_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("windows",), "windows-system-dir"),
    (("program files",), "program-files"),
    (("program files (x86)",), "program-files-x86"),
    (("programdata", "microsoft"), "programdata-microsoft"),
    (("$recycle.bin",), "recycle-bin"),
    (("system volume information",), "system-volume-info"),
    (("hiberfil.sys",), "hibernation-file"),
    (("pagefile.sys",), "page-file"),
    (("swapfile.sys",), "swap-file"),
)

# Marker returned for any input that can't be turned into a usable
# absolute path. Returning a NEVER instead of raising means callers
# always fail safe: bad input -> reject.
_INVALID_INPUT_RULE = "invalid-input"


def is_never(path: Path | str | None) -> tuple[bool, str]:
    """Return ``(blocked, rule_name)`` for ``path``.

    ``blocked`` is True iff the path is on the NEVER list or the input
    is pathological. ``rule_name`` carries the human-readable reason
    (or ``"invalid-input"`` for unparseable inputs).

    The function never raises — it is the safety net, so it has to be
    callable from any context including error-handling paths.
    """
    if path is None:
        return True, _INVALID_INPUT_RULE
    if isinstance(path, str) and path == "":
        return True, _INVALID_INPUT_RULE

    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, ValueError, RuntimeError, TypeError):
        return True, _INVALID_INPUT_RULE

    parts = resolved.parts
    # parts[0] is the drive root ("C:\\" on Windows, "/" on POSIX).
    # parts[1:] are the named segments we want to match against rules.
    if len(parts) < 2:
        return False, ""

    segments = tuple(p.lower() for p in parts[1:])

    for rule_segments, rule_name in _NEVER_RULES:
        if len(segments) >= len(rule_segments) and segments[: len(rule_segments)] == rule_segments:
            return True, rule_name

    return False, ""


def rule_names() -> tuple[str, ...]:
    """All defined NEVER rule names. Useful for diagnostics and tests."""
    return (*(name for _, name in _NEVER_RULES), _INVALID_INPUT_RULE)
