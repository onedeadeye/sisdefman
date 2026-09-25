"""Terminal output helpers: colour, warning banners and confirmation prompts."""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional, TextIO

CONFIRM_PHRASE = "CHANGE PLAYER INVENTORIES"
ACCEPT_FLAG = "--accept-inventory-changes"

_enabled: Optional[bool] = None


def _color_enabled(stream: TextIO) -> bool:
    global _enabled
    if _enabled is None:
        _enabled = (
            hasattr(stream, "isatty")
            and stream.isatty()
            and "NO_COLOR" not in os.environ
            and os.environ.get("TERM") != "dumb"
        )
        if _enabled and os.name == "nt":
            os.system("")  # enables ANSI escape handling in the Windows console
    return _enabled


def _wrap(code: str, text: str, stream: TextIO = sys.stdout) -> str:
    return f"\033[{code}m{text}\033[0m" if _color_enabled(stream) else text


def bold(text: str) -> str:
    return _wrap("1", text)


def dim(text: str) -> str:
    return _wrap("2", text)


def red(text: str) -> str:
    return _wrap("1;31", text)


def yellow(text: str) -> str:
    return _wrap("33", text)


def green(text: str) -> str:
    return _wrap("32", text)


def banner(title: str, width: int = 76) -> str:
    bar = "!" * width
    inner = width - 6
    lines = [bar]
    for chunk in _chunks(title.upper(), inner):
        lines.append(f"!! {chunk.center(inner)} !!")
    lines.append(bar)
    return red("\n".join(lines))


def _chunks(text: str, width: int):
    words, line = text.split(), ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            yield line
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        yield line


def confirm_phrase(
    accepted: bool,
    stdin: Optional[TextIO] = None,
    out: Optional[TextIO] = None,
    read: Optional[Callable[[str], str]] = None,
) -> bool:
    """Ask the user to type CONFIRM_PHRASE. ``accepted`` (the command-line
    flag) skips the prompt. Without a terminal the answer is "no"."""
    stdin = stdin or sys.stdin
    out = out or sys.stdout
    read = read or input
    if accepted:
        print(yellow(f"{ACCEPT_FLAG} given: proceeding."), file=out)
        return True
    if not (hasattr(stdin, "isatty") and stdin.isatty()):
        print(red(f"Nothing was changed: this needs confirmation. Re-run with {ACCEPT_FLAG} to proceed anyway."),
              file=out)
        return False
    try:
        answer = read(f"Type {bold(CONFIRM_PHRASE)} to continue, or anything else to cancel: ")
    except EOFError:
        answer = ""
    if answer.strip() == CONFIRM_PHRASE:
        return True
    print("Cancelled. Nothing was changed.", file=out)
    return False
