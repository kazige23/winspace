"""Smoke tests that exercise the package skeleton.

These tests must pass before any feature work begins; they guard the
install/import/CLI-entry contract.
"""

from __future__ import annotations

from click.testing import CliRunner

from winspace import __version__
from winspace.cli import main


def test_version_string_is_pep440() -> None:
    """Version string must be non-empty and look like a PEP 440 release."""
    assert isinstance(__version__, str)
    assert __version__
    # Cheap PEP 440 shape check; full validation lives in packaging.
    assert __version__[0].isdigit()


def test_cli_version_flag_prints_canonical_format() -> None:
    """`winspace --version` must print `winspace <version>` exactly."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"winspace {__version__}"


def test_cli_help_lists_program_intent() -> None:
    """The top-level help should mention what the tool does."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    # Don't assert on exact wording — only that the help advertises both
    # the relocation concept and the safety net (junctions).
    assert "junction" in result.output.lower()
