"""Selecting item definitions: the ``--where`` conditions and ID lists used
by ``query``, ``set``, ``adopt`` and friends.

A condition is ``FIELD=VALUE``, ``FIELD!=VALUE``, ``FIELD~TEXT`` (contains,
ignoring case), ``FIELD!~TEXT``, ``FIELD<N`` / ``>`` / ``<=`` / ``>=``, or a
bare ``FIELD`` (is set) / ``!FIELD`` (is not set).

Fields are those of the exported definition (``name``, ``type``, ``tags``...),
the item's kind fields (``weapon``, ``flavor``...), the columns of the table
row a ref field points to (``weapon.Range``), one tag
(``tags.rarity``) and ``id``, ``kind``, ``series``, ``index`` and ``dummy``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from . import derive, steam
from .project import Project, ProjectError

_CONDITION = re.compile(
    r"^(?P<neg>!)?(?P<field>[A-Za-z_][\w.]*)\s*(?:(?P<op>!=|<=|>=|!~|=|~|<|>)(?P<value>.*))?$", re.S)
_RANGE = re.compile(r"^(\d+)-(\d+)$")


@dataclass
class Condition:
    field: str
    op: Optional[str]
    value: str
    negate: bool = False

    def matches(self, view: dict) -> bool:
        raw = view.get(self.field)
        values = raw if isinstance(raw, list) else [raw]
        texts = [_text(v) for v in values]
        op, want = self.op, self.value
        if op is None:
            result = any(t != "" and v is not False for t, v in zip(texts, values))
        elif op == "=":
            result = want in texts
        elif op == "!=":
            result = want not in texts
        elif op == "~":
            result = any(want.lower() in t.lower() for t in texts)
        elif op == "!~":
            result = not any(want.lower() in t.lower() for t in texts)
        else:
            result = any(_compare(t, op, want) for t in texts)
        return result != self.negate


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _compare(a: str, op: str, b: str) -> bool:
    try:
        x, y = float(a), float(b)
    except ValueError:
        x, y = a, b
    return {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


def parse_condition(text: str) -> Condition:
    m = _CONDITION.match(text.strip())
    if not m:
        raise ProjectError(f"cannot read condition {text!r} (e.g. rarity=epic, name~rifle, id>=200, flavor)")
    if m.group("neg") and m.group("op"):
        raise ProjectError(f"{text!r}: use != or !~ instead of a leading !")
    return Condition(m.group("field"), m.group("op"), (m.group("value") or "").strip(), bool(m.group("neg")))


def item_views(project: Project, include_dummies: bool = False) -> List[dict]:
    """One flat dictionary per definition, for filtering and display."""
    records = project.by_id()
    positions = project.series_positions()
    schema = project.schema()
    out = []
    for it in project.build():
        i = it["itemdefid"]
        rec = records.get(i)
        if rec is None and not include_dummies:
            continue
        pos = positions.get(i)
        view = {
            "id": i,
            "kind": (rec or {}).get("kind", ""),
            "series": pos.key if pos else (project.series_for_id(i) or "" if rec is None else ""),
            "index": pos.index if pos else "",
            "dummy": rec is None,
        }
        for k, v in it.items():
            view.setdefault(k, v)
        for k, v in (rec or {}).items():
            view.setdefault(k, v)
        for name, spec in derive.fields_of(schema.kind_of(rec or {})).items():
            if spec.get("type") == "ref" and (rec or {}).get(name) not in (None, ""):
                table = schema.tables.get(spec.get("table")) or {}
                row = (table.get("rows") or {}).get(str(rec[name])) or {}
                for column in table.get("columns") or []:
                    view.setdefault(f"{name}.{column}", row.get(column, ""))
        for cat, val in steam.parse_tags(it.get("tags")):
            key = f"tags.{cat}"
            if key in view:
                view[key] = (view[key] if isinstance(view[key], list) else [view[key]]) + [val]
            else:
                view[key] = val
        out.append(view)
    return out


def select(project: Project, conditions: Iterable[str], include_dummies: bool = False) -> List[dict]:
    parsed = [parse_condition(c) for c in conditions]
    return [v for v in item_views(project, include_dummies) if all(c.matches(v) for c in parsed)]


def split_targets(tokens: Iterable[str]) -> Tuple[List[int], List[str]]:
    """Separate itemdefids (``110``, ``110-134``) from other arguments."""
    ids, rest = [], []
    for tok in tokens:
        m = _RANGE.match(tok)
        if tok.isdigit():
            ids.append(int(tok))
        elif m:
            a, b = int(m.group(1)), int(m.group(2))
            ids.extend(range(min(a, b), max(a, b) + 1))
        else:
            rest.append(tok)
    return ids, rest


def parse_assignment(text: str) -> Tuple[str, object]:
    """``FIELD=TEXT`` or ``FIELD:=JSON``."""
    field, sep, value = text.partition("=")
    if not sep or not field:
        raise ProjectError(f"expected FIELD=VALUE or FIELD:=JSON, got {text!r}")
    if field.endswith(":"):
        field = field[:-1]
        try:
            return field, json.loads(value)
        except json.JSONDecodeError as e:
            raise ProjectError(f"{field}: invalid JSON value: {e.msg}")
    return field, value
