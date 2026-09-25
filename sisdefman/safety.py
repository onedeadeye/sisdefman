"""Release-mode protection.

Players' Steam inventories store itemdefids. If the definition behind an
itemdefid becomes a different item (because items were renumbered, removed or
replaced), every player who owned it silently ends up with something else.
This module works out which live definitions a change would affect and
shows the release-mode warning.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, TextIO

from . import ui
from .project import Project


@dataclass
class Impact:
    itemdefid: int
    was: str
    now: Optional[str]  # None when the definition would be missing
    kind: str  # "reassigned", "dummied", "reused" (a retired ID gets a new item) or "missing"

    def describe(self) -> str:
        if self.kind == "dummied":
            return f"#{self.itemdefid}  {self.was!r} -> a dummy placeholder item"
        if self.kind == "reused":
            return f"#{self.itemdefid}  retired item (now {self.was!r}) -> {self.now!r}"
        if self.kind == "missing":
            return f"#{self.itemdefid}  {self.was!r} -> not in the export (Steam keeps the old definition)"
        return f"#{self.itemdefid}  {self.was!r} -> {self.now!r}"


def compare(old: Dict[int, str], project: Project, built: List[dict]) -> List[Impact]:
    """How the definitions in ``built`` differ from ``old`` (itemdefid -> name)
    for the IDs listed in ``old``."""
    new = {it["itemdefid"]: it for it in built}
    out = []
    for itemdefid, was in sorted(old.items()):
        it = new.get(itemdefid)
        if it is None:
            out.append(Impact(itemdefid, was, None, "missing"))
        elif project.is_dummy(it):
            if was != it.get("name"):
                out.append(Impact(itemdefid, was, it.get("name", ""), "dummied"))
        elif it.get("name", "") != was or it.get("type") != "item":
            retired = was == project.make_dummy(itemdefid).get("name")
            out.append(Impact(itemdefid, was, it.get("name", ""), "reused" if retired else "reassigned"))
    return out


def protected_before(project: Project, built_before: List[dict]) -> Dict[int, str]:
    """The definitions an operation must not silently change: everything
    recorded as live (or, without a live record, everything that exists).

    A live ID that is currently a dummy stays protected under the dummy's
    name: players may still hold the retired item, so putting a new item in
    that slot would change what they own."""
    before = {it["itemdefid"]: it for it in built_before}
    live = project.live_names()
    if live is None:
        return project.owned_names(built_before)
    out = {}
    for i in live:
        it = before.get(i)
        if it is not None:
            out[i] = it.get("name", "")
    return out


def warn_and_confirm(
    project: Project,
    impacts: List[Impact],
    accepted: bool,
    *,
    action: str,
    advice: Optional[str] = None,
    out: Optional[TextIO] = None,
    stdin: Optional[TextIO] = None,
    read=None,
) -> bool:
    """In release mode, show the heavy warning for ``impacts`` and require
    confirmation. Returns True if the change may go ahead."""
    if project.mode != "release" or not impacts:
        return True
    out = out or sys.stdout
    real = [i for i in impacts if i.kind != "missing"]
    print(file=out)
    print(ui.banner("Release mode: this changes items players already own"), file=out)
    print(file=out)
    print(
        f"{action} changes what {len(impacts)} live item definition(s) are.\n"
        "Steam inventories store itemdefids, so once this is uploaded every player\n"
        "who owns one of these IDs will have a different item:",
        file=out,
    )
    print(file=out)
    for imp in impacts:
        print("    " + ui.bold(imp.describe()), file=out)
    print(file=out)
    if real:
        print(ui.red("This cannot be undone for players once it is uploaded to Steam."), file=out)
    if any(i.kind == "reassigned" for i in impacts):
        print("If an entry above is only a rename of the same item, that one is harmless.", file=out)
    if project.live_names() is None:
        print(
            ui.yellow("No live baseline is recorded, so every existing item is treated as live. "
                      "Run `sisdefman mark-live` after uploading to record one."),
            file=out,
        )
    if advice:
        print(advice, file=out)
    print(file=out)
    return ui.confirm_phrase(accepted, stdin=stdin, out=out, read=read)
