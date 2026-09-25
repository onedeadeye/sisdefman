"""Operations that change which items are in a series and in what order.

An item's itemdefid is ``first_id`` of its series plus its slot. Inserting,
moving or removing items therefore renumbers the items after them. Every
reference to a renumbered item (in ``bundle``, ``exchange`` and
``tag_generators``) is rewritten to follow it, so drop tables keep pointing
at the same items. Players' inventories cannot be rewritten the same way,
which is why the CLI warns about these operations in release mode.

Renumbering only shifts the run of consecutive items after the change: an
unused slot in the series absorbs the shift.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import derive, steam
from .project import Project, ProjectError


@dataclass
class OpResult:
    moved: Dict[int, int] = field(default_factory=dict)  # old itemdefid -> new itemdefid
    added: List[int] = field(default_factory=list)  # itemdefids of new items
    removed: List[Tuple[int, str]] = field(default_factory=list)  # (old itemdefid, name)
    rewritten: List[Tuple[int, List[str]]] = field(default_factory=list)  # (itemdefid, fields)
    notes: List[str] = field(default_factory=list)


class _Layout:
    """The members of one series keyed by itemdefid, edited in place."""

    def __init__(self, project: Project, key: str):
        self.project = project
        self.key = key
        s = project.get_series(key)
        self.first_id = s["first_id"]
        self.last_id = s["last_id"]
        self.slots: Dict[int, dict] = {m["itemdefid"]: m for m in project.members(key)}

    def ids(self) -> List[int]:
        return sorted(self.slots)

    def position_of(self, itemdefid: int) -> int:
        ids = self.ids()
        if itemdefid not in self.slots:
            raise ProjectError(f"itemdefid {itemdefid} is not an item of series {self.key!r}")
        return ids.index(itemdefid) + 1

    def insert(self, obj: dict, position: Optional[int], skip: frozenset = frozenset()) -> None:
        ids = self.ids()
        if position is None:
            slot = (ids[-1] + 1) if ids else self.first_id
            while slot in skip:
                slot += 1
            self.slots[slot] = obj
        elif position == len(ids) + 1:
            self.slots[(ids[-1] + 1) if ids else self.first_id] = obj
        else:
            if not 1 <= position <= len(ids) + 1:
                raise ProjectError(f"position must be between 1 and {len(ids) + 1} for series {self.key!r}")
            carry, slot = obj, ids[position - 1]
            while slot in self.slots:
                self.slots[slot], carry = carry, self.slots[slot]
                slot += 1
            self.slots[slot] = carry
        top = max(self.slots)
        if top > self.last_id:
            raise ProjectError(
                f"series {self.key!r} is full: it would need itemdefid {top}, but its range ends at "
                f"{self.last_id}. Raise it with `sisdefman series set {self.key} --last-id N` if the IDs "
                "after it are free."
            )

    def remove(self, itemdefid: int, shift: bool) -> dict:
        obj = self.slots.pop(itemdefid)
        if shift:
            slot = itemdefid
            while slot + 1 in self.slots:
                self.slots[slot] = self.slots.pop(slot + 1)
                slot += 1
        return obj

    def apply(self, result: OpResult) -> None:
        """Write the new itemdefids back and rewrite references."""
        old_ids = {id(it): it["itemdefid"] for it in self.project.items}
        placed = {id(obj) for obj in self.slots.values()}
        # Drop removed items, then renumber and add new ones.
        self.project.items[:] = [
            it for it in self.project.items
            if id(it) in placed or not (self.first_id <= it["itemdefid"] <= self.last_id)
        ]
        known = {id(it) for it in self.project.items}
        for slot, obj in self.slots.items():
            if id(obj) in old_ids and old_ids[id(obj)] != slot:
                result.moved[old_ids[id(obj)]] = slot
            obj["itemdefid"] = slot
            if id(obj) not in known:
                self.project.items.append(obj)
        if result.moved:
            for it in self.project.items:
                fields = steam.remap_references(it, result.moved)
                if fields and it["itemdefid"] not in self.project.managed_generator_ids():
                    result.rewritten.append((it["itemdefid"], fields))
        self.project.normalize()


def _prepare_new_item(project: Project, key: str, item: dict, result: OpResult) -> dict:
    item = copy.deepcopy(item)
    if "itemdefid" in item:
        result.notes.append(f"ignored itemdefid {item.pop('itemdefid')} on {item.get('name', 'new item')!r}; "
                            "series items are numbered by position")
    kind = project.schema().kind_of(item)
    if kind is None:
        item.setdefault("type", "item")
    elif "tags" in derive.rules_of(kind) and "tags" not in item:
        # The kind's rule builds the tags (normally including series:{series}).
        return {"itemdefid": 0, **item}
    old_series = steam.tag_values(item, "series")
    if steam.set_single_tag(item, "series", key):
        was = f" (was series:{old_series[0]})" if old_series else ""
        result.notes.append(f"tagged {item.get('name', 'new item')!r} with series:{key}{was}")
    # itemdefid first, as in Steam's own files.
    return {"itemdefid": 0, **item}


def references_to(project: Project, itemdefid: int) -> List[Tuple[int, str]]:
    """Items referring to ``itemdefid``, ignoring generator bundles that
    sisdefman builds from series rules."""
    managed = project.managed_generator_ids()
    out = []
    for it in project.items:
        for fld, ref in steam.references(it):
            if ref == itemdefid and not (fld == "bundle" and it["itemdefid"] in managed):
                out.append((it["itemdefid"], fld))
    return out


def resolve_position(
    layout_ids: List[int], position: Optional[int], before: Optional[int], after: Optional[int], key: str
) -> Optional[int]:
    given = [x is not None for x in (position, before, after)]
    if sum(given) > 1:
        raise ProjectError("use only one of --position, --before and --after")
    if before is not None or after is not None:
        target = before if before is not None else after
        if target not in layout_ids:
            raise ProjectError(f"itemdefid {target} is not an item of series {key!r}")
        pos = layout_ids.index(target) + 1
        return pos if before is not None else pos + 1
    return position


def insert_items(
    project: Project,
    key: str,
    new_items: List[dict],
    position: Optional[int] = None,
    before: Optional[int] = None,
    after: Optional[int] = None,
    skip_ids: frozenset = frozenset(),
) -> OpResult:
    """Insert ``new_items`` (in order) into a series. Without a position they
    are appended, which never renumbers existing items; appending also passes
    over ``skip_ids`` (retired IDs players may still hold)."""
    if not new_items:
        raise ProjectError("no items to add")
    result = OpResult()
    layout = _Layout(project, key)
    pos = resolve_position(layout.ids(), position, before, after, key)
    prepared = [_prepare_new_item(project, key, it, result) for it in new_items]
    for n, obj in enumerate(prepared):
        layout.insert(obj, None if pos is None else pos + n, skip_ids)
    layout.apply(result)
    result.added = [obj["itemdefid"] for obj in prepared]
    return result


def remove_item(project: Project, itemdefid: int, shift: bool = True) -> OpResult:
    """Remove a series item. With ``shift`` the items after it move down to
    close the gap; without it the slot is left empty and exported as a dummy."""
    key = project.series_for_id(itemdefid)
    if key is None or project.item(itemdefid) is None:
        raise ProjectError(
            f"itemdefid {itemdefid} is not an item in a series. Other definitions can be edited in the "
            "project file directly (deleting one there does not delete it on Steam)."
        )
    refs = references_to(project, itemdefid)
    if refs:
        where = ", ".join(f"{i} ({fld})" for i, fld in refs)
        raise ProjectError(f"itemdefid {itemdefid} is still referenced by: {where}. Remove those references first.")
    result = OpResult()
    layout = _Layout(project, key)
    obj = layout.remove(itemdefid, shift)
    result.removed.append((itemdefid, obj.get("name", "")))
    layout.apply(result)
    return result


def move_item(
    project: Project,
    itemdefid: int,
    position: Optional[int] = None,
    before: Optional[int] = None,
    after: Optional[int] = None,
) -> OpResult:
    """Move a series item to another position in the same series."""
    key = project.series_for_id(itemdefid)
    if key is None or project.item(itemdefid) is None:
        raise ProjectError(f"itemdefid {itemdefid} is not an item in a series")
    if itemdefid in (before, after):
        raise ProjectError("cannot move an item relative to itself")
    if position is None and before is None and after is None:
        raise ProjectError("say where to move it with --position, --before or --after")
    layout = _Layout(project, key)
    obj = layout.remove(itemdefid, shift=True)
    # --before/--after name items by their current IDs; find where they sit now.
    lookup = {id(o): slot for slot, o in layout.slots.items()}
    current = {m["itemdefid"]: lookup.get(id(m)) for m in project.members(key)}
    before = current.get(before, before) if before is not None else None
    after = current.get(after, after) if after is not None else None
    pos = resolve_position(layout.ids(), position, before, after, key)
    if pos is not None and not 1 <= pos <= len(layout.ids()) + 1:
        raise ProjectError(f"position must be between 1 and {len(layout.ids()) + 1}")
    layout.insert(obj, pos)
    result = OpResult()
    layout.apply(result)
    return result


def shift_summary(moved: Dict[int, int]) -> List[str]:
    """Group renumberings into runs: ``"117-140 -> 118-141"``."""
    runs: List[List[int]] = []
    for old in sorted(moved):
        new = moved[old]
        if runs and old == runs[-1][1] + 1 and new - old == runs[-1][2]:
            runs[-1][1] = old
        else:
            runs.append([old, old, new - old])
    out = []
    for start, end, delta in runs:
        if start == end:
            out.append(f"{start} -> {start + delta}")
        else:
            out.append(f"{start}-{end} -> {start + delta}-{end + delta}")
    return out
