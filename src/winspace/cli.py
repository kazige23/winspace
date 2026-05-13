"""Top-level CLI for winspace.

Subcommands (scan / move / undo / list / doctor) are added in later
tasks. This module only wires up the command group and ``--version`` so
that Task 1's acceptance checks pass on a fresh install.
"""

from __future__ import annotations

import click

from winspace.version import __version__


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="winspace", message="%(prog)s %(version)s")
def main() -> None:
    """winspace — clean up C: drive by relocating large directories.

    Run ``winspace scan`` to discover candidates, then ``winspace move``
    to relocate them while keeping the original paths working through
    NTFS junctions.
    """


if __name__ == "__main__":
    main()
