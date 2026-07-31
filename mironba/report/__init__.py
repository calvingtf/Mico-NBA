"""The product surface: timeline, report agent, chat, and a static page.

No modelling lives here. Everything in this package reads a completed run and
renders it; nothing decides anything, and nothing changes a result.
"""

from __future__ import annotations

import sys


def use_utf8_stdout() -> None:
    """Make CLI output survive redirection on Windows.

    Player names contain characters outside cp1252 - Nikola Jokic and Jonas
    Valanciunas both do - and Python picks the console codepage when stdout is
    a pipe. Printing a timeline to a file therefore crashed with a
    UnicodeEncodeError partway through, which is a poor way for a reporting
    tool to fail. Called by every entry point in this package.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover - non-tty streams
            pass
