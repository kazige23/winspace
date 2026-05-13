"""winspace graphical interface (PySide6).

The GUI reuses the same core engine the CLI does — detectors, mover,
manifest — so behaviour stays consistent across both surfaces. Each
long-running operation (scan / move / delete) runs on a background
:class:`QThread` so the window never freezes.
"""
