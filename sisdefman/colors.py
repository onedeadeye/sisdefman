"""The colour palette: standard colours defined once by keyword.

``project.colors`` maps keywords to six-digit hex colours
(``{"common": "d2d2d2", "background": "292929"}``). A colour field
(``name_color``, ``background_color`` or any field ending in ``_color``)
can hold ``@keyword`` instead of a hex value, on an item, in a lookup-table
cell, in a kind's rule (``"@background"``, or even ``"@{rarity}"``) or in
the dummy item; it is replaced by the palette's hex value on export.

``convert`` moves the hex colours already in a project into the palette
and replaces them with references, without changing the export.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from . import steam

KEYWORD = re.compile(r"^[A-Za-z_][\w-]*$")
REFERENCE = re.compile(r"^@([A-Za-z_][\w-]*)$")
NAMED = {"ffffff": "white", "000000": "black"}


def is_color_field(name: str) -> bool:
    return name in ("name_color", "background_color") or name.endswith("_color")


def is_color_column(name: str) -> bool:
    lower = name.lower()
    return "color" in lower or "colour" in lower


def keyword_of(value) -> Optional[str]:
    m = REFERENCE.match(value) if isinstance(value, str) else None
    return m.group(1) if m else None


def resolve_item(palette: Dict[str, str], item: dict) -> List[str]:
    """Replace ``@keyword`` colour values in ``item``; returns problems."""
    problems = []
    for name, value in item.items():
        kw = keyword_of(value) if is_color_field(name) else None
        if kw is None:
            continue
        if kw in palette:
            item[name] = palette[kw]
        else:
            problems.append(f"unknown colour @{kw} in {name}")
    return problems


def check_palette(palette) -> List[str]:
    if not isinstance(palette, dict):
        return ["colors must be an object mapping keywords to hex colours"]
    out = []
    for kw, value in palette.items():
        if not KEYWORD.match(str(kw)):
            out.append(f"colour keyword {kw!r} may only contain letters, digits, _ and -")
        if not steam.is_hex_color(value):
            out.append(f"colour {kw!r}: {value!r} is not a six-digit hex colour")
    return out


# --------------------------------------------------------------- places


def _places(project) -> Iterator[Tuple[str, dict, str]]:
    """Every stored value that may be a colour: (description, container, key)."""
    for rec in project.items:
        for k in list(rec):
            if is_color_field(k):
                yield f"item {rec['itemdefid']} {k}", rec, k
    for tname, t in project.tables.items():
        for key, row in (t.get("rows") or {}).items():
            for col in list(row):
                yield f"table {tname} {key}.{col}", row, col
    for kname, kind in project.kinds.items():
        derive = kind.get("derive") or {}
        for k in list(derive):
            yield f"kind {kname} rule {k}", derive, k
    dummy = project.settings.get("dummy_item") or {}
    for k in list(dummy):
        if is_color_field(k):
            yield f"dummy item {k}", dummy, k


def usages(project) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {kw: [] for kw in project.colors}
    for where, container, key in _places(project):
        kw = keyword_of(container.get(key))
        if kw is not None:
            out.setdefault(kw, []).append(where)
    return out


def rename(project, old: str, new: str) -> int:
    if old not in project.colors:
        raise_error(f"there is no colour {old!r}")
    if new == old:
        return 0
    if not KEYWORD.match(new):
        raise_error("colour keywords may only contain letters, digits, _ and -")
    if new in project.colors:
        raise_error(f"there is already a colour {new!r}")
    project.data["colors"] = {(new if k == old else k): v for k, v in project.colors.items()}
    count = 0
    for _, container, key in _places(project):
        if container.get(key) == f"@{old}":
            container[key] = f"@{new}"
            count += 1
    return count


def set_color(project, kw: str, value: str) -> None:
    if not KEYWORD.match(kw):
        raise_error("colour keywords may only contain letters, digits, _ and -")
    value = value.strip().lstrip("#")
    if not steam.is_hex_color(value):
        raise_error(f"{value!r} is not a six-digit hex colour")
    project.colors[kw] = value


def delete(project, kw: str, force: bool = False) -> None:
    if kw not in project.colors:
        raise_error(f"there is no colour {kw!r}")
    used = usages(project).get(kw) or []
    if used and not force:
        raise_error(f"colour {kw!r} is used by {len(used)} value(s), e.g. {used[0]}")
    del project.colors[kw]


def raise_error(text: str):
    from .project import ProjectError

    raise ProjectError(text)


# -------------------------------------------------------------- convert


@dataclass
class ConvertReport:
    created: Dict[str, str] = field(default_factory=dict)  # keyword -> hex
    reused: Dict[str, str] = field(default_factory=dict)  # keyword -> hex already in the palette
    replaced: int = 0
    by_keyword: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__, lines=self.lines())

    def lines(self) -> List[str]:
        if not self.replaced:
            return ["No hex colours to convert."]
        out = [f"Replaced {self.replaced} colour value(s) with palette keywords:"]
        for kw, n in sorted(self.by_keyword.items(), key=lambda x: -x[1]):
            hexv = self.created.get(kw) or self.reused.get(kw)
            state = "new" if kw in self.created else "existing"
            out.append(f"  @{kw} = {hexv} ({state}, {n} value(s))")
        return out


def _convertible(project) -> Iterator[Tuple[str, dict, str, str]]:
    """Hex colour values that can become references: (kind of place, container, key, hex)."""
    for where, container, key in _places(project):
        value = container.get(key)
        if not steam.is_hex_color(value):
            continue
        kind = where.split(" ", 1)[0]
        if kind == "table":
            if not is_color_column(key):
                continue
        elif not is_color_field(key):
            continue
        yield where, container, key, value


def _unique(name: str, taken: set) -> str:
    name = re.sub(r"[^\w-]+", "_", name).strip("_") or "color"
    if not re.match(r"^[A-Za-z_]", name):
        name = "c_" + name
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}_{n}", n + 1
    return candidate


def convert(project) -> ConvertReport:
    """Put every hex colour of the project into the palette and replace it
    with a reference. The export does not change."""
    report = ConvertReport()
    found = list(_convertible(project))
    if not found:
        return report
    before = _comparable(project.build())

    existing = {v.lower(): kw for kw, v in project.colors.items()}
    uses: Dict[str, List[str]] = {}
    first_spelling: Dict[str, str] = {}
    table_keys: Dict[str, str] = {}
    for where, container, key, value in found:
        low = value.lower()
        uses.setdefault(low, []).append(where)
        first_spelling.setdefault(low, value)
        if where.startswith("table "):
            table_keys.setdefault(low, where.split(" ")[2].split(".")[0])

    # Tags that single out the items using a colour in one field (e.g. the
    # name_color of every rarity:common item).
    field_users: Dict[str, Dict[str, List[dict]]] = {}
    for it in project.build():
        if project.is_dummy(it):
            continue
        for k, v in it.items():
            if is_color_field(k) and steam.is_hex_color(v):
                field_users.setdefault(k, {}).setdefault(v.lower(), []).append(it)
    scored: Dict[str, List[Tuple[int, int, str]]] = {}
    for by_hex in field_users.values():
        counts = {h: Counter(t for it in its for t in steam.tag_strings(it)) for h, its in by_hex.items()}
        for h, c in counts.items():
            order = {t: n for it in reversed(by_hex[h]) for n, t in enumerate(steam.tag_strings(it))}
            for tag, n in c.items():
                if n * 2 < len(by_hex[h]):
                    continue
                elsewhere = sum(o.get(tag, 0) for other, o in counts.items() if other != h)
                # Ties go to the tag written first (e.g. rarity before visuals).
                scored.setdefault(h, []).append((elsewhere - n, order.get(tag, 99), tag))
    tag_scores = {h: [(-a, tag) for a, _, tag in sorted(v)] for h, v in scored.items()}

    # Names: a lookup-table row key, "background", a tag that singles out the
    # items using the colour (a tag of a single item only when its category
    # names other colours too, e.g. the one rare), "white"/"black", or the hex.
    names: Dict[str, str] = {}
    categories = set()
    pending = []
    for low in uses:
        if low in existing:
            continue
        if low in table_keys:
            names[low] = table_keys[low]
        elif all(u.endswith("background_color") for u in uses[low]):
            names[low] = "background"
        else:
            tag = next((t for score, t in tag_scores.get(low, []) if score > 1), None)
            if tag:
                category, _, value = tag.partition(":")
                names[low] = value or tag
                categories.add(category)
            else:
                pending.append(low)
    for low in pending:
        tag = next((t for score, t in tag_scores.get(low, [])
                    if score == 1 and t.partition(":")[0] in categories), None)
        names[low] = (tag.partition(":")[2] or tag) if tag else NAMED.get(low, f"color_{low}")

    taken = set(project.colors)
    keywords: Dict[str, str] = {}
    for low in uses:
        if low in existing:
            keywords[low] = existing[low]
            report.reused[existing[low]] = project.colors[existing[low]]
            continue
        kw = _unique(names[low], taken)
        taken.add(kw)
        keywords[low] = kw
        project.colors[kw] = first_spelling[low]
        report.created[kw] = first_spelling[low]

    for _, container, key, value in found:
        kw = keywords[value.lower()]
        container[key] = f"@{kw}"
        report.replaced += 1
        report.by_keyword[kw] = report.by_keyword.get(kw, 0) + 1

    if _comparable(project.build()) != before:  # should not happen
        raise_error("converting the colours would change the export; nothing was changed")
    return report


def _comparable(built: List[dict]) -> List[dict]:
    out = []
    for it in built:
        out.append({k: (v.lower() if is_color_field(k) and isinstance(v, str) else v) for k, v in it.items()})
    return out
