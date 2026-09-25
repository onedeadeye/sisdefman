"""Converting existing definitions to a kind, and back.

``adopt`` works out a kind's fields for items whose Steam fields were written
by hand. Each of the kind's templates is matched against the item's stored
value: ``"{weapon.name} | {finish}"`` against ``"Pistol | Red"``
gives ``finish = "Red"`` and, once ``weapon`` is known from another
template (such as the tags), teaches the ``weapon`` table that
``pistol`` is called ``Pistol``. Matching repeats over all items until
nothing new is learned, so values that are ambiguous in one item are
resolved by others.

Adopting never changes the exported definitions: a stored value that the
rule does not reproduce is kept as an override, and an item whose export
would change anyway is left as it was.
"""

from __future__ import annotations

import copy
import itertools
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import derive
from .derive import Context, Schema, Unknown
from .project import Project, ProjectError

_FORMATTER = derive._FORMATTER


@dataclass
class AdoptResult:
    kind: str
    adopted: List[int] = field(default_factory=list)
    overrides: Dict[int, List[str]] = field(default_factory=dict)  # fields kept as overrides
    skipped: Dict[int, str] = field(default_factory=dict)  # itemdefid -> reason
    learned: List[str] = field(default_factory=list)  # table values filled in
    side_effects: List[int] = field(default_factory=list)  # other items whose export changed

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "adopted": self.adopted,
            "overrides": {str(k): v for k, v in self.overrides.items()},
            "skipped": {str(k): v for k, v in self.skipped.items()},
            "learned": self.learned,
            "side_effects": self.side_effects,
        }


# ------------------------------------------------------------ inversion


def _known(ctx: Context, path: str, spec: str) -> Optional[str]:
    try:
        obj, _ = _FORMATTER.get_field(path, (), ctx)
        return _FORMATTER.format_field(obj, spec)
    except Unknown:
        return None
    except Exception:
        return None


def _invert_one(template: str, value: str, ctx: Context) -> Optional[Dict[str, str]]:
    """Match ``value`` against ``template``. Returns the values of unknown
    placeholders that the match pins down, or None if it does not match."""
    try:
        segments = list(_FORMATTER.parse(template))
    except ValueError:
        return None
    lazy, greedy = [], []
    groups: Dict[str, str] = {}
    unlearnable = set()
    for literal, path, spec, conversion in segments:
        lazy.append(re.escape(literal))
        greedy.append(re.escape(literal))
        if path is None:
            continue
        if conversion or not path or path[0].isdigit():
            return None
        known = _known(ctx, path, spec)
        if known is not None:
            lazy.append(re.escape(known))
            greedy.append(re.escape(known))
        elif path in groups:
            lazy.append(f"(?P={groups[path]})")
            greedy.append(f"(?P={groups[path]})")
        else:
            name = groups[path] = f"g{len(groups)}"
            lazy.append(f"(?P<{name}>.*?)")
            greedy.append(f"(?P<{name}>.*)")
            if spec:
                unlearnable.add(path)
    m1 = re.fullmatch("".join(lazy), value, re.S)
    m2 = re.fullmatch("".join(greedy), value, re.S)
    if not m1 or not m2:
        return None
    return {p: m1.group(g) for p, g in groups.items() if m1.group(g) == m2.group(g) and p not in unlearnable}


def invert(rule, value, ctx: Context) -> Optional[Dict[str, str]]:
    if isinstance(rule, str):
        return _invert_one(rule, value, ctx)
    if not isinstance(rule, list) or not all(isinstance(p, str) for p in rule):
        return None
    # A paragraph made only of placeholders renders empty when they are
    # empty, and is then dropped.
    optional = []
    for n, p in enumerate(rule):
        try:
            if all(not lit.strip() for lit, _, _, _ in _FORMATTER.parse(p)):
                optional.append(n)
        except ValueError:
            return None
    for size in range(len(optional) + 1):
        for omit in itertools.combinations(optional, size):
            kept = [p for n, p in enumerate(rule) if n not in omit]
            found = _invert_one("\n\n".join(kept), value, ctx) if kept else ({} if value == "" else None)
            if found is None:
                continue
            for n in omit:
                for path in derive.template_paths(rule[n]):
                    found.setdefault(path, "")
            return found
    return None


# ---------------------------------------------------------------- adopt


def adopt(project: Project, kind_name: str, ids: List[int]) -> AdoptResult:
    """Convert the given items to ``kind_name`` in place (tables may gain
    rows and values). The caller saves the project."""
    kind = project.kinds.get(kind_name)
    if kind is None:
        raise ProjectError(f"no kind named {kind_name!r} (known: {', '.join(project.kinds) or 'none'})")
    problems = derive.check_definitions(project.schema())
    if problems:
        raise ProjectError("fix the schema first: " + "; ".join(problems))
    result = AdoptResult(kind_name)
    fields = derive.fields_of(kind)
    rules = derive.rules_of(kind)
    records = project.by_id()
    positions = project.series_positions()
    before = {it["itemdefid"]: it for it in project.build()}

    active = []
    for i in ids:
        rec = records.get(i)
        if rec is None:
            result.skipped[i] = "no such item"
        elif rec.get("kind"):
            result.skipped[i] = f"already has kind {rec['kind']!r}"
        else:
            active.append(i)

    tables = copy.deepcopy(project.tables)
    schema = Schema(tables, project.kinds)
    values = {i: {f: records[i][f] for f in fields if f in records[i]} for i in active}

    def learn(i: int, path: str, value: str) -> bool:
        try:
            root, attrs = derive.split_path(path)
        except ValueError:
            return False
        spec = fields.get(root)
        if spec is None:
            return False
        if not attrs:
            if root in values[i]:
                return False
            if spec.get("type") == "number":
                try:
                    value = int(value) if re.fullmatch(r"-?\d+", value) else float(value)
                except ValueError:
                    return False
            elif spec.get("type") == "bool":
                if value not in ("True", "False", "true", "false"):
                    return False
                value = value.lower() == "true"
            values[i][root] = value
            if spec.get("type") == "ref" and value != "":
                rows = tables.setdefault(spec["table"], {"columns": [], "rows": {}}).setdefault("rows", {})
                if str(value) not in rows:
                    rows[str(value)] = {}
                    result.learned.append(f"table {spec['table']}: new row {value!r}")
            return True
        if spec.get("type") != "ref" or len(attrs) != 1 or attrs[0].startswith("["):
            return False
        table = tables.get(spec.get("table"), {})
        column = attrs[0]
        if column not in table.get("columns", []):
            return False
        rows = table.setdefault("rows", {})
        key = values[i].get(root)
        if key not in (None, ""):
            row = rows.setdefault(str(key), {})
            if row.get(column) in (None, ""):
                row[column] = value
                result.learned.append(f"table {spec['table']}: {key}.{column} = {value!r}")
                return True
            return False
        matches = [k for k, r in rows.items() if r.get(column) == value]
        if len(matches) == 1:
            values[i][root] = matches[0]
            return True
        return False

    changed = True
    while changed:
        changed = False
        for i in active:
            for name, rule in rules.items():
                stored = records[i].get(name)
                if not isinstance(stored, str) or not isinstance(rule, (str, list)):
                    continue
                ctx = Context(schema, {"itemdefid": i, **values[i]}, positions.get(i), kind, strict=True)
                for path, value in (invert(rule, stored, ctx) or {}).items():
                    changed |= learn(i, path, value)

    new_records: Dict[int, dict] = {}
    for i in active:
        missing = [f for f, spec in fields.items()
                   if not spec.get("optional") and not spec.get("default") and values[i].get(f) in (None, "")]
        if missing:
            result.skipped[i] = f"could not work out {', '.join(missing)}"
            continue
        rec = records[i]
        new = {"itemdefid": i, "kind": kind_name}
        new.update((f, values[i][f]) for f in fields if values[i].get(f) not in (None, ""))
        trial, _ = derive.resolve(schema, new, positions.get(i))
        for name in rules:
            if name in rec and name not in fields and rec[name] != trial.get(name):
                new[name] = copy.deepcopy(rec[name])
                result.overrides.setdefault(i, []).append(name)
        for key, value in rec.items():
            if key not in new and key not in rules and key not in fields:
                new[key] = copy.deepcopy(value)
        new_records[i] = new

    # Apply, then undo any item whose export would change.
    original_items = copy.deepcopy(project.items)
    original_tables = project.data["tables"]
    project.data["tables"] = tables
    while True:
        project.items[:] = [new_records.get(it["itemdefid"], copy.deepcopy(it)) for it in original_items]
        after = {it["itemdefid"]: it for it in project.build()}
        broken = [i for i in new_records if after.get(i) != before.get(i)]
        if not broken:
            break
        for i in broken:
            diff = sorted(k for k in set(after[i]) | set(before[i]) if after[i].get(k) != before[i].get(k))
            result.skipped[i] = f"its export would change ({', '.join(diff)})"
            result.overrides.pop(i, None)
            del new_records[i]
    result.adopted = sorted(new_records)
    result.side_effects = sorted(i for i in before if i not in new_records and after.get(i) != before[i])
    if not result.adopted:
        project.data["tables"] = original_tables
        result.learned = []
    return result


def detach(project: Project, ids: List[int]) -> List[int]:
    """Turn kind items back into plain definitions holding their current
    Steam fields. Returns the itemdefids changed."""
    records = project.by_id()
    changed = []
    for i in ids:
        rec = records.get(i)
        if rec is None:
            raise ProjectError(f"no item definition {i}")
        if not rec.get("kind"):
            continue
        resolved, _ = project.resolve(rec)
        rec.clear()
        rec.update(resolved)
        changed.append(i)
    return changed
