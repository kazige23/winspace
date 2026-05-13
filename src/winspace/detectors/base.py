"""Detector framework.

A :class:`Detector` is the smallest plugin unit: it knows *one* kind
of path (node_modules, browser caches, Steam libraries, …) and how
to find them on the current machine. Each :meth:`Detector.find` call
returns a list of :class:`Candidate` records carrying the path, the
:class:`RiskLevel`, and human-readable reasons in zh + en.

Discovery is automatic — every Python module placed under
``winspace.detectors`` (except ``base`` itself) is imported and any
concrete subclass of :class:`Detector` defined inside is collected
by :func:`discover_detectors`.

Detectors deliberately do NOT compute sizes. Size belongs to the
scanner (``scanner.directory_size``); the CLI enriches each candidate
with its size before ranking. This keeps detectors fast and pure.
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from winspace.core.fs import FileSystem


class RiskLevel(StrEnum):
    """Per-candidate risk classification.

    Values map to spec.md §3's table:

    * ``SAFE``    — caches / regenerables; pre-checked in scan UI
    * ``CONFIRM`` — important but app-aware (Steam, Docker, WSL)
    * ``RISKY``   — mixed-content user data (IM apps); hidden by default
    * ``NEVER``   — system / cloud sync / encrypted; never offered
    """

    SAFE = "safe"
    CONFIRM = "confirm"
    RISKY = "risky"
    NEVER = "never"


@dataclass(frozen=True)
class Candidate:
    """One relocatable directory identified by a detector."""

    path: Path
    category: str  # e.g. "node_modules", "browser_cache", "steam"
    risk: RiskLevel
    reason_zh: str  # short Chinese explanation of WHY this is movable
    reason_en: str  # parallel English explanation
    detector_name: str  # which detector emitted this candidate
    size_bytes: int = 0  # populated by the CLI via scanner.directory_size
    prerequisite_note_zh: str = ""  # e.g. "请先关闭 Steam"
    prerequisite_note_en: str = ""


class Detector(ABC):
    """Base class for every detector.

    Concrete subclasses MUST:

    * set ``name`` as a class attribute (used for de-duplication and
      stable ordering in CLI output)
    * implement :meth:`find`, returning a (possibly empty) list of
      :class:`Candidate`

    Detectors should be cheap to construct — :func:`discover_detectors`
    instantiates every concrete subclass.
    """

    name: ClassVar[str]

    @abstractmethod
    def find(self, fs: FileSystem) -> list[Candidate]:
        """Locate every candidate this detector knows about."""


def discover_detectors(*, package_name: str = "winspace.detectors") -> list[Detector]:
    """Return every concrete :class:`Detector` defined *under* ``package_name``.

    The package is imported, then every non-private, non-``base``
    submodule is imported so that subclass registration happens. We
    then enumerate every concrete :class:`Detector` subclass whose
    ``__module__`` lives under ``package_name``. Subclasses defined
    elsewhere in the process (e.g. inside the test suite, inside other
    libraries) are deliberately excluded so production discovery isn't
    polluted by test fixtures.

    Instances are returned sorted by ``name`` for deterministic output.
    """
    package = importlib.import_module(package_name)
    if not hasattr(package, "__path__"):
        return []

    for _, mod_name, is_pkg in pkgutil.iter_modules(package.__path__):
        if is_pkg or mod_name.startswith("_") or mod_name == "base":
            continue
        importlib.import_module(f"{package_name}.{mod_name}")

    package_prefix = package_name + "."
    seen: dict[str, Detector] = {}
    for cls in _all_concrete_subclasses(Detector):
        if not cls.__module__.startswith(package_prefix):
            continue
        instance = cls()
        seen[instance.name] = instance
    return sorted(seen.values(), key=lambda d: d.name)


def _all_concrete_subclasses(cls: type) -> set[type[Detector]]:
    """Walk Python's class hierarchy returning every concrete Detector subclass.

    The parameter is typed as ``type`` rather than ``type[Detector]`` so the
    function can be called with the abstract ``Detector`` itself without
    confusing mypy — we only ever read ``__subclasses__`` and never
    instantiate via this reference.
    """
    result: set[type[Detector]] = set()
    for sub in cls.__subclasses__():
        result.update(_all_concrete_subclasses(sub))
        if issubclass(sub, Detector) and not getattr(sub, "__abstractmethods__", set()):
            result.add(sub)
    return result
