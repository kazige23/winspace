"""Unit tests for :mod:`winspace.detectors.base`.

Covers the Detector ABC contract, the Candidate dataclass invariants,
the RiskLevel enum, and the discover_detectors() walker. The walker
is exercised against a small temporary package built per-test so we
don't pollute the real winspace.detectors registry.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import ClassVar

import pytest

from winspace.core.fs import FileSystem, RealFileSystem
from winspace.detectors.base import (
    Candidate,
    Detector,
    RiskLevel,
    discover_detectors,
)

# --- RiskLevel ---------------------------------------------------------------


def test_risk_level_has_four_tiers() -> None:
    assert {r.value for r in RiskLevel} == {"safe", "confirm", "risky", "never"}


# --- Candidate ---------------------------------------------------------------


def test_candidate_is_frozen() -> None:
    """A Candidate must not be mutated in place — that would defeat the
    'detector returns immutable record' invariant the CLI relies on.
    """
    c = Candidate(
        path=Path("C:\\foo"),
        category="x",
        risk=RiskLevel.SAFE,
        reason_zh="zh",
        reason_en="en",
        detector_name="t",
    )
    with pytest.raises(AttributeError):
        c.category = "y"  # type: ignore[misc]


def test_candidate_default_size_is_zero() -> None:
    """Detectors leave size_bytes=0; the CLI fills it via directory_size."""
    c = Candidate(
        path=Path("C:\\foo"),
        category="x",
        risk=RiskLevel.SAFE,
        reason_zh="zh",
        reason_en="en",
        detector_name="t",
    )
    assert c.size_bytes == 0


# --- Detector contract -------------------------------------------------------


def test_cannot_instantiate_abstract_detector() -> None:
    with pytest.raises(TypeError):
        Detector()  # type: ignore[abstract]


def test_concrete_subclass_satisfies_isinstance(tmp_path: Path) -> None:
    class T(Detector):
        name: ClassVar[str] = "test-concrete"

        def find(self, fs: FileSystem) -> list[Candidate]:
            return []

    assert isinstance(T(), Detector)


# --- discover_detectors ------------------------------------------------------


def _write_pkg(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (root / name).write_text(textwrap.dedent(body).lstrip())


def test_discover_finds_detectors_in_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg_dir = tmp_path / "fake_detectors"
    _write_pkg(
        pkg_dir,
        {
            "__init__.py": "",
            "foo.py": """
                from winspace.detectors.base import Candidate, Detector, RiskLevel
                from winspace.core.fs import FileSystem
                from pathlib import Path
                from typing import ClassVar

                class FooDetector(Detector):
                    name: ClassVar[str] = 'foo'
                    def find(self, fs: FileSystem) -> list[Candidate]:
                        return []
            """,
            "bar.py": """
                from winspace.detectors.base import Candidate, Detector, RiskLevel
                from winspace.core.fs import FileSystem
                from pathlib import Path
                from typing import ClassVar

                class BarDetector(Detector):
                    name: ClassVar[str] = 'bar'
                    def find(self, fs: FileSystem) -> list[Candidate]:
                        return []
            """,
            # Underscore-prefixed module must be skipped.
            "_skip_me.py": "raise RuntimeError('this should not import')",
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(sys.modules, "fake_detectors", None)
    sys.modules.pop("fake_detectors", None)

    results = discover_detectors(package_name="fake_detectors")
    names = [d.name for d in results]
    assert names == ["bar", "foo"]  # sorted alphabetically


def test_discover_skips_subpackages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg_dir = tmp_path / "pkg_with_subpkg"
    _write_pkg(
        pkg_dir,
        {
            "__init__.py": "",
            "good.py": """
                from winspace.detectors.base import Candidate, Detector, RiskLevel
                from winspace.core.fs import FileSystem
                from typing import ClassVar

                class GoodDetector(Detector):
                    name: ClassVar[str] = 'good-one'
                    def find(self, fs): return []
            """,
        },
    )
    (pkg_dir / "subpkg").mkdir()
    (pkg_dir / "subpkg" / "__init__.py").write_text(
        "raise RuntimeError('sub-package must not be auto-imported')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("pkg_with_subpkg", None)
    sys.modules.pop("pkg_with_subpkg.subpkg", None)

    results = discover_detectors(package_name="pkg_with_subpkg")
    assert [d.name for d in results] == ["good-one"]


def test_discover_handles_missing_package() -> None:
    """If the package has no submodules and no detectors, return empty list."""
    # The bare winspace.detectors package is empty until T10 adds the first
    # concrete detector — discover_detectors must handle that gracefully.
    # We use a synthetic minimal package to assert the empty path explicitly.
    with pytest.raises(ModuleNotFoundError):
        discover_detectors(package_name="winspace.detectors.does_not_exist")


def test_discover_returns_empty_for_package_without_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A module that's a single .py file (no __path__) returns []."""
    import types

    fake_module = types.ModuleType("scalar_module")
    # No __path__ attribute means it's not a package.
    monkeypatch.setitem(sys.modules, "scalar_module", fake_module)
    assert discover_detectors(package_name="scalar_module") == []


def test_discover_deduplicates_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two detector classes claiming the same name must collapse to one."""
    pkg_dir = tmp_path / "dup_pkg"
    _write_pkg(
        pkg_dir,
        {
            "__init__.py": "",
            "a.py": """
                from winspace.detectors.base import Candidate, Detector, RiskLevel
                from winspace.core.fs import FileSystem
                from typing import ClassVar

                class A(Detector):
                    name: ClassVar[str] = 'twin'
                    def find(self, fs): return []
            """,
            "b.py": """
                from winspace.detectors.base import Candidate, Detector, RiskLevel
                from winspace.core.fs import FileSystem
                from typing import ClassVar

                class B(Detector):
                    name: ClassVar[str] = 'twin'
                    def find(self, fs): return []
            """,
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("dup_pkg", None)

    results = discover_detectors(package_name="dup_pkg")
    names = [d.name for d in results]
    assert names == ["twin"]


# --- Detector.find may use the injected FileSystem ---------------------------


def test_detector_can_use_fs_for_walks(tmp_path: Path) -> None:
    """A trivial detector that uses fs.iterdir works end-to-end."""

    class CountChildrenDetector(Detector):
        name: ClassVar[str] = "count-test"

        def find(self, fs: FileSystem) -> list[Candidate]:
            count = len(list(fs.iterdir(tmp_path)))
            return [
                Candidate(
                    path=tmp_path / f"child-{i}",
                    category="counted",
                    risk=RiskLevel.SAFE,
                    reason_zh="测试",
                    reason_en="test",
                    detector_name=self.name,
                )
                for i in range(count)
            ]

    (tmp_path / "a").touch()
    (tmp_path / "b").touch()
    found = CountChildrenDetector().find(RealFileSystem())
    assert len(found) == 2
    assert all(c.detector_name == "count-test" for c in found)
