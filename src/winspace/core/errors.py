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


class InsufficientSpaceError(FsError):
    """Destination volume does not have enough free space for the operation.

    Maps to spec §3 CLI exit code 4.
    """


class MoveAbortedError(WinspaceError):
    """Move failed and we successfully rolled the state back to a clean state.

    Maps to spec §3 CLI exit code 5. The user can retry safely.
    """


class MoveRolledForwardError(WinspaceError):
    """Move failed AND rollback to clean state failed.

    Maps to spec §3 CLI exit code 6. Manual intervention required;
    the manifest carries diagnostic information.
    """


class SafetyViolation(WinspaceError):
    """An operation was refused because it would touch a protected path."""


class JunctionError(WinspaceError):
    """Junction create / inspect / delete failed."""


class ManifestError(WinspaceError):
    """Manifest could not be loaded, parsed, or persisted."""


class VerificationError(WinspaceError):
    """Post-copy fingerprint comparison detected a mismatch."""
