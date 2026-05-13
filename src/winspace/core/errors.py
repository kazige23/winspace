"""Exception hierarchy for winspace.

All non-trivial failures must be raised as a ``WinspaceError`` subclass
so that the CLI layer can map them to spec-defined exit codes without
catching bare ``Exception``.
"""

from __future__ import annotations


class WinspaceError(Exception):
    """Base for all winspace-specific errors."""


class FsError(WinspaceError):
    """A filesystem operation failed in an unexpected way."""


class CrossVolumeRenameError(FsError):
    """Rename refused because src and dst live on different volumes.

    Callers must fall back to copy+delete rather than retrying rename.
    """


class SafetyViolation(WinspaceError):
    """An operation was refused because it would touch a protected path."""


class JunctionError(WinspaceError):
    """Junction create / inspect / delete failed."""


class ManifestError(WinspaceError):
    """Manifest could not be loaded, parsed, or persisted."""


class VerificationError(WinspaceError):
    """Post-copy fingerprint comparison detected a mismatch."""
