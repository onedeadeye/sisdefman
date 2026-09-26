"""Edits shared by the command line and the GUI: item fields, lookup tables
and the schema (tables + kinds)."""

from __future__ import annotations

import copy
import re
from typing import Dict, Iterable, List, Optional, Tuple

from . import derive, steam
from .project import CONTENTS_TOKEN, LISTING_TOKENS, Project, ProjectError

RESERVED_FIELDS = ("itemdefid", "kind")


def set_fields(project: Project, ids: Iterable[int], assignments: List[Tuple[str, object]],
               unset: Iterable[str] = ()) -> List[str]:
    """Store values on items (a kind field, an override of a derived field or
    a plain Steam field) and remove others. Returns notes for the user."""
    schema = project.schema()
    records = project.by_id()
    notes: List[str] = []
    unset = list(unset)
    for field, _ in assignments:
        if field == "itemdefid":
            raise ProjectError("itemdefid cannot be set; use `move` to change an item's position")
        if field == "kind":
            raise ProjectError("use `adopt` to give items a kind and `detach` to remove it")
    for i in ids:
        rec = records.get(i)
        if rec is None:
            raise ProjectError(f"no item definition {i}"
                               + (" (it is an unused series slot)" if project.series_for_id(i) else ""))
        kind = schema.kind_of(rec)
        fields = derive.fields_of(kind)
        rules = derive.rules_of(kind)
        for field, value in assignments:
            spec = fields.get(field)
            if spec and spec.get("type") == "ref" and value not in ("", None) \
                    and str(value) not in schema.rows(spec.get("table", "")):
                raise ProjectError(f"{value!r} is not a row of table {spec.get('table')!r}; "
                                   f"add it with `sisdefman table set {spec.get('table')} {value} ...`")
            if spec and value in ("", None):
                rec.pop(field, None)
                continue
            rec[field] = value
            if kind is not None and field in rules and not spec:
                notes.append(f"{i}: {field} now overrides the {rec['kind']} rule "
                             f"(`sisdefman set {i} --unset {field}` goes back to the rule)")
        for field in unset:
            if field in RESERVED_FIELDS:
                raise ProjectError(f"{field} cannot be removed")
            rec.pop(field, None)
    return notes


# ------------------------------------------------------------------ tables


def _table(project: Project, name: str) -> dict:
    table = project.tables.get(name)
    if table is None:
        raise ProjectError(f"no table named {name!r} (known: {', '.join(project.tables) or 'none'})")
    table.setdefault("columns", [])
    table.setdefault("rows", {})
    return table


def ref_fields(project: Project, table: str) -> Dict[str, List[str]]:
    """kind -> the kind's fields that hold keys of ``table``."""
    out: Dict[str, List[str]] = {}
    for kind_name, kind in project.kinds.items():
        for field, spec in derive.fields_of(kind).items():
            if spec.get("type") == "ref" and spec.get("table") == table:
                out.setdefault(kind_name, []).append(field)
    return out


def uses_table_directly(project: Project, rec: dict, table: str) -> bool:
    """Whether an item reads ``table`` through a reference such as
    {weapon.DisplayName} (in its stored values or its kind's rules) rather
    than through a ref field."""
    kind = project.schema().kind_of(rec)
    if (derive.fields_of(kind).get(table) or {}).get("type") == "ref":
        return False
    texts = [v for k, v in rec.items() if k not in derive.STRUCTURAL_FIELDS]
    for rule in derive.rules_of(kind).values():
        texts.extend(rule if isinstance(rule, list) else [rule])
    return any(table in derive.references_in(t) for t in texts)


def direct_row(project: Project, rec: dict, table: str) -> Optional[str]:
    """The value that picks an item's row of ``table`` for such references:
    its field named after the table, or its tag."""
    value = rec.get(table)
    if isinstance(value, (str, int)) and value != "":
        return str(value)
    resolved = project.resolve(rec)[0] if project.schema().kind_of(rec) else rec
    values = steam.tag_values(resolved, table)
    return values[0] if values else None


def rows_in_use(project: Project, table: str) -> Dict[str, List[int]]:
    """Row key -> itemdefids referring to it (through a ref field, or a
    reference such as {weapon.DisplayName} with the item's field or tag)."""
    fields = ref_fields(project, table)
    rows = (project.tables.get(table) or {}).get("rows") or {}
    out: Dict[str, List[int]] = {}
    for rec in project.items:
        for field in fields.get(rec.get("kind"), []):
            if rec.get(field) not in (None, ""):
                key = derive.row_key(rows, rec[field]) or str(rec[field])
                out.setdefault(key, []).append(rec["itemdefid"])
        if uses_table_directly(project, rec, table):
            value = direct_row(project, rec, table)
            key = derive.row_key(rows, value) if value is not None else None
            if key is not None:
                out.setdefault(key, []).append(rec["itemdefid"])
    return out


def _tag_users(project: Project, table: str, old: str, new: str) -> List[int]:
    """Items that find row ``old`` by their tag and would lose it as ``new``."""
    out = []
    for rec in project.items:
        if isinstance(rec.get(table), (str, int)) and rec.get(table) != "":
            continue  # a field, which is renamed along
        if uses_table_directly(project, rec, table):
            value = direct_row(project, rec, table)
            if value is not None and value.lower() == old.lower() and value.lower() != new.lower():
                out.append(rec["itemdefid"])
    return out


def create_table(project: Project, name: str, columns: List[str]) -> None:
    if name in project.tables:
        raise ProjectError(f"table {name!r} already exists")
    if not name.isidentifier():
        raise ProjectError("table names may only contain letters, digits and _")
    project.tables[name] = {"columns": list(columns), "rows": {}}


def set_row(project: Project, name: str, key: str, values: Dict[str, object]) -> List[str]:
    table = _table(project, name)
    notes = []
    for column in values:
        if not column.isidentifier():
            raise ProjectError(f"invalid column name {column!r}")
        if column not in table["columns"]:
            table["columns"].append(column)
            notes.append(f"added column {column!r} to table {name!r}")
    row = table["rows"].setdefault(str(key), {})
    for column, value in values.items():
        if value in ("", None):
            row.pop(column, None)
        else:
            row[column] = value
    return notes


def delete_row(project: Project, name: str, key: str, force: bool = False) -> None:
    table = _table(project, name)
    if str(key) not in table["rows"]:
        raise ProjectError(f"table {name!r} has no row {key!r}")
    users = rows_in_use(project, name).get(str(key), [])
    if users and not force:
        raise ProjectError(f"row {key!r} is used by item(s) {', '.join(map(str, users[:10]))}")
    del table["rows"][str(key)]


def rename_row(project: Project, name: str, old: str, new: str) -> int:
    """Rename a row key and every reference to it. Returns the number of
    items updated."""
    table = _table(project, name)
    if old not in table["rows"]:
        raise ProjectError(f"table {name!r} has no row {old!r}")
    if new in table["rows"]:
        raise ProjectError(f"table {name!r} already has a row {new!r}")
    tagged = _tag_users(project, name, old, new)
    if tagged:
        raise ProjectError(f"item(s) {', '.join(map(str, tagged[:10]))} find row {old!r} by their {name}: tag, "
                           f"which renaming the row would not change; rename it only in a way that still matches "
                           f"(ignoring case), or change their tags first")
    table["rows"] = {(new if k == old else k): v for k, v in table["rows"].items()}
    fields = ref_fields(project, name)
    count = 0
    for rec in project.items:
        for field in fields.get(rec.get("kind"), []):
            if rec.get(field) == old:
                rec[field] = new
                count += 1
        if field_names_row(project, rec, name, old):
            rec[name] = new
            count += 1
    return count


def field_names_row(project: Project, rec: dict, table: str, key: str) -> bool:
    """Whether an item without a ref field for ``table`` names row ``key`` in
    its own field called ``table``."""
    return (not project.schema().kind_of(rec) or table not in derive.fields_of(project.schema().kind_of(rec))) \
        and isinstance(rec.get(table), str) and rec[table] == key and uses_table_directly(project, rec, table)


def replace_table(project: Project, name: str, table: dict, renames: Optional[Dict[str, str]] = None) -> None:
    """Replace a whole table (the GUI's table editor). ``renames`` maps old
    row keys to new ones so references follow."""
    if not isinstance(table.get("columns"), list) or not isinstance(table.get("rows"), dict):
        raise ProjectError("a table needs a columns list and a rows object")
    renames = {o: n for o, n in (renames or {}).items() if o != n}
    for old, new in renames.items():
        tagged = _tag_users(project, name, old, new)
        if tagged:
            raise ProjectError(f"item(s) {', '.join(map(str, tagged[:10]))} find row {old!r} by their {name}: tag, "
                               "which renaming the row would not change; keep a key that still matches (ignoring "
                               "case), or change their tags first")
    fields = ref_fields(project, name)
    for rec in project.items:
        for field in fields.get(rec.get("kind"), []):
            if rec.get(field) in renames:
                rec[field] = renames[rec[field]]
        for old, new in renames.items():
            if field_names_row(project, rec, name, old):
                rec[name] = new
                break
    project.tables[name] = {"columns": list(table["columns"]),
                            "rows": {str(k): dict(v) for k, v in table["rows"].items()}}


def delete_table(project: Project, name: str) -> None:
    _table(project, name)
    users = ref_fields(project, name)
    if users:
        raise ProjectError(f"table {name!r} is used by kind(s) {', '.join(users)}")
    direct = [rec["itemdefid"] for rec in project.items if uses_table_directly(project, rec, name)]
    if direct:
        raise ProjectError(f"table {name!r} is used by item(s) {', '.join(map(str, direct[:10]))}")
    del project.tables[name]


# ------------------------------------------------------------------ kinds


def kind_users(project: Project, name: str) -> List[int]:
    return [rec["itemdefid"] for rec in project.items if rec.get("kind") == name]


def replace_kind(project: Project, name: str, kind: dict, old_name: Optional[str] = None) -> None:
    if not name.isidentifier():
        raise ProjectError("kind names may only contain letters, digits and _")
    if not isinstance(kind.get("fields", {}), dict) or not isinstance(kind.get("derive", {}), dict):
        raise ProjectError("a kind needs \"fields\" and \"derive\" objects")
    if old_name and old_name != name:
        if name in project.kinds:
            raise ProjectError(f"kind {name!r} already exists")
        project.kinds[name] = project.kinds.pop(old_name)
        for rec in project.items:
            if rec.get("kind") == old_name:
                rec["kind"] = name
    project.kinds[name] = {"fields": copy.deepcopy(kind.get("fields", {})),
                           "derive": copy.deepcopy(kind.get("derive", {}))}
    problems = derive.check_definitions(project.schema())
    if problems:
        raise ProjectError("; ".join(problems))


def delete_kind(project: Project, name: str) -> None:
    if name not in project.kinds:
        raise ProjectError(f"no kind named {name!r}")
    users = kind_users(project, name)
    if users:
        raise ProjectError(f"kind {name!r} is used by {len(users)} item(s); detach them first")
    del project.kinds[name]


# ------------------------------------------------------------------ schema


def export_schema(project: Project) -> dict:
    return {"tables": copy.deepcopy(project.tables), "kinds": copy.deepcopy(project.kinds)}


def import_schema(project: Project, data: dict) -> List[str]:
    """Merge a schema: tables of the same name get the new columns and rows
    (rows with the same key are replaced); kinds of the same name are
    replaced."""
    if not isinstance(data, dict) or not ({"tables", "kinds"} & set(data)):
        raise ProjectError("a schema file is an object with \"tables\" and/or \"kinds\"")
    notes = []
    for name, table in (data.get("tables") or {}).items():
        if not isinstance(table, dict):
            raise ProjectError(f"table {name!r} must be an object")
        target = project.tables.setdefault(name, {"columns": [], "rows": {}})
        for column in table.get("columns", []):
            if column not in target.setdefault("columns", []):
                target["columns"].append(column)
        target.setdefault("rows", {}).update(copy.deepcopy(table.get("rows") or {}))
        notes.append(f"table {name}: {len(target['columns'])} column(s), {len(target['rows'])} row(s)")
    for name, kind in (data.get("kinds") or {}).items():
        project.kinds[name] = copy.deepcopy(kind)
        notes.append(f"kind {name}: {len(derive.fields_of(kind))} field(s), "
                     f"{len(derive.rules_of(kind))} derived field(s)")
    problems = derive.check_definitions(project.schema())
    if problems:
        raise ProjectError("; ".join(problems))
    return notes


# ------------------------------------------------------------------ series


def save_series(project: Project, key: str, config: dict, is_new: bool) -> List[str]:
    """Create or change a series' settings. ``config`` holds any of name,
    first_id (new series only), last_id, description_template (None or "" to
    use the global one), secret (tag rule(s) marking secret rares; None or ""
    to use the containers' exclude tags), containers and generators. Returns
    notes."""
    notes: List[str] = []
    if is_new:
        if key in project.series:
            raise ProjectError(f"series {key!r} already exists")
        if not key or any(c in key for c in ";: "):
            raise ProjectError("a series key cannot be empty or contain ';', ':' or spaces")
        for field in ("first_id", "last_id"):
            if not isinstance(config.get(field), int) or isinstance(config.get(field), bool):
                raise ProjectError(f"{field} must be a whole number")
        if not str(config.get("name") or "").strip():
            raise ProjectError("give the series a display name; descriptions show it (e.g. \"Third Series #4\")")
        s = {"name": "", "first_id": config["first_id"], "last_id": config["last_id"],
             "allocated_through": None, "containers": {}, "generators": {}}
    else:
        s = copy.deepcopy(project.get_series(key))
        if "first_id" in config and config["first_id"] != s["first_id"]:
            raise ProjectError("the first ID of an existing series cannot change")
    if str(config.get("name") or "").strip():
        s["name"] = str(config["name"]).strip()
    if "last_id" in config and not is_new:
        s["last_id"] = config["last_id"]
    if s["last_id"] < s["first_id"]:
        raise ProjectError("the last ID must not be before the first ID")
    if "description_template" in config:
        template = config["description_template"]
        if template in (None, ""):
            s.pop("description_template", None)
        else:
            s["description_template"] = template
    if "secret" in config:
        secret = config["secret"]
        if secret in (None, "") or secret == []:
            s.pop("secret", None)
        else:
            rules = secret if isinstance(secret, list) else [secret]
            for rule in rules:
                if not isinstance(rule, str) or not steam.parse_tag_rule(rule):
                    raise ProjectError(f"secret rares need a tag rule such as rarity:epic (got {rule!r})")
            s["secret"] = secret
    if "containers" in config:
        s["containers"] = {str(int(k)): {"exclude": [t for t in (v or {}).get("exclude", []) if t]}
                           for k, v in (config["containers"] or {}).items()}
    if "generators" in config:
        s["generators"] = {str(int(k)): str(v).strip() for k, v in (config["generators"] or {}).items()}
        for gid, rule in s["generators"].items():
            if not steam.parse_tag_rule(rule):
                raise ProjectError(f"generator {gid} needs a tag rule such as rarity:common")

    for other, o in project.series.items():
        if other != key and o["first_id"] <= s["last_id"] and s["first_id"] <= o["last_id"]:
            raise ProjectError(f"IDs {s['first_id']}-{s['last_id']} overlap series {other!r}")
    in_range = [it for it in project.items if s["first_id"] <= it["itemdefid"] <= s["last_id"]]
    if is_new:
        strays = [it["itemdefid"] for it in in_range
                  if key not in steam.tag_values(project.resolve(it)[0], "series")]
        if strays:
            raise ProjectError(
                f"IDs {', '.join(map(str, strays[:8]))} in that range belong to definitions not tagged "
                f"series:{key}. Every definition in a series' range becomes one of its items, so choose "
                "a range without them.")
        if in_range:
            notes.append(f"{len(in_range)} existing definition(s) in that range are now items of the series.")
    else:
        old = project.series[key]
        needed = max([old.get("allocated_through") or 0] +
                     [it["itemdefid"] for it in in_range if it["itemdefid"] <= old["last_id"]])
        if s["last_id"] < needed:
            raise ProjectError(f"series {key!r} already uses IDs up to {needed}")
        blockers = [it["itemdefid"] for it in in_range if it["itemdefid"] > old["last_id"]]
        if blockers:
            raise ProjectError(f"IDs {', '.join(map(str, blockers[:8]))} are in use by other definitions")

    by_id = project.by_id()
    for ref in [int(k) for k in s["containers"]] + [int(k) for k in s["generators"]]:
        if ref not in by_id:
            raise ProjectError(f"itemdefid {ref} does not exist")
        if s["first_id"] <= ref <= s["last_id"] or (project.series_for_id(ref) not in (None, key)):
            raise ProjectError(f"itemdefid {ref} is inside a series' ID range, so it cannot be a container "
                               "or generator")
    mine = set(s["containers"]) | set(s["generators"])
    for other, o in project.series.items():
        taken = (set(o["containers"]) | set(o["generators"])) & mine if other != key else set()
        if taken:
            raise ProjectError(f"itemdefid {', '.join(sorted(taken))} already belongs to series {other!r}")
    for cid in s["containers"]:
        if not any(t in (by_id[int(cid)].get("description") or "") for t in LISTING_TOKENS):
            notes.append(f"Put {CONTENTS_TOKEN} in the description of {cid} where the item list should go.")
    if is_new:
        project.insert_series(key, s)
    else:
        project.series[key] = s
    return notes


def reorder_series(project: Project, keys: List[str]) -> List[str]:
    """Put the series in this order (as the GUI and ``list`` show them);
    series not listed follow in their current order. Only the order changes."""
    keys = [str(k) for k in keys]
    for k in keys:
        project.get_series(k)
    if len(set(keys)) != len(keys):
        raise ProjectError("a series is listed more than once")
    order = keys + [k for k in project.series if k not in keys]
    entries = [(k, project.series[k]) for k in order]
    project.series.clear()
    project.series.update(entries)
    return order


def series_by_first_id(project: Project) -> List[str]:
    return sorted(project.series, key=lambda k: project.series[k]["first_id"])


def delete_series(project: Project, key: str) -> None:
    s = project.get_series(key)
    if project.members(key) or s.get("allocated_through"):
        raise ProjectError(f"series {key!r} has items or used IDs; only an unused series can be deleted")
    del project.series[key]


# ------------------------------------------------------- new series (copy)

_NO_TEXT_REPLACE = ("itemdefid", "kind", "bundle", "exchange", "tag_generators", "tags")


def suggest_series(project: Project) -> dict:
    """Defaults for a new series, following the last one of the largest
    family (crate1..crate5 -> crate6, rather than promo1 -> promo2): the
    next key and the next ID block of the same size."""
    if not project.series:
        return {"key": "series1", "first_id": None, "last_id": None, "source": None}
    families: Dict[str, List[str]] = {}
    for k in project.series:
        m = re.match(r"^(.*?)(\d+)$", k)
        families.setdefault(m.group(1) if m else k, []).append(k)
    family = max(families.values(), key=lambda ks: (len(ks), max(project.series[k]["first_id"] for k in ks)))
    by_start = sorted(family, key=lambda k: project.series[k]["first_id"])
    source = by_start[-1]
    s = project.series[source]
    m = re.match(r"^(.*?)(\d+)$", source)
    prefix, number = (m.group(1), int(m.group(2))) if m else (source, 1)
    key = f"{prefix}{number + 1}"
    while key in project.series:
        number += 1
        key = f"{prefix}{number + 1}"
    used = (s.get("allocated_through") or s["first_id"]) - s["first_id"] + 1
    block = max(100, 10 ** len(str(used)))
    span = s["last_id"] - s["first_id"]
    if span + 1 > 10 * block:  # an oversized range is not worth repeating
        span = block - 2
    if len(by_start) > 1:
        step = s["first_id"] - project.series[by_start[-2]]["first_id"]
    else:
        step = max(block, 10 ** len(str(span + 1)))
    first, last = s["first_id"] + step, s["first_id"] + step + span
    used_ids = project.by_id()
    while any(first <= i <= last for i in used_ids) or any(
            o["first_id"] <= last and first <= o["last_id"] for o in project.series.values()):
        first, last = first + step, last + step
    return {"key": key, "first_id": first, "last_id": last, "source": source}


def _block(s: dict) -> Tuple[int, int]:
    """The ID block around a series: 210-296 -> 200-299 (supporting
    definitions such as generators usually live next to the items)."""
    size = 10 ** len(str(s["last_id"] - s["first_id"] + 1))
    return (s["first_id"] // size) * size, -(-(s["last_id"] + 1) // size) * size - 1


def series_copy_plan(project: Project, source: str, key: str, name: str, first_id: int) -> dict:
    """What copying the setup of ``source`` for a new series would copy: the
    source's containers and generators and the other definitions in its ID
    block, each with its new ID, plus suggested text replacements."""
    s = project.get_series(source)
    offset = first_id - s["first_id"]
    lo, hi = _block(s)
    records = project.by_id()
    others = {int(k) for o_key, o in project.series.items() if o_key != source
              for k in list(o["containers"]) + list(o["generators"])}
    containers = [int(k) for k in s["containers"]]
    ids = sorted({i for i in records if lo <= i <= hi and project.series_for_id(i) is None} |
                 set(containers) | {int(k) for k in s["generators"]})
    ids = [i for i in ids if i not in others]
    referenced_elsewhere = set()
    for rec in project.items:
        if lo <= rec["itemdefid"] <= hi or rec["itemdefid"] in containers:
            continue
        try:
            referenced_elsewhere.update(r for _, r in steam.references(rec))
        except steam.SyntaxProblem:
            pass
    connected = _connected(project, source, ids)
    taken = set(records)
    candidates = []
    for i in ids:
        if i in containers:
            new = i + 1
            while new in taken or project.series_for_id(new) is not None:
                new += 1
            taken.add(new)
        else:
            new = i + offset
        shared = i in referenced_elsewhere and i not in containers
        rec = records[i]
        note = ""
        if shared:
            note = "also used outside this series (shared); not copied unless ticked"
        elif i not in connected:
            note = "not connected to this series' crate, generators or items; not copied unless ticked"
        candidates.append({
            "id": i, "new_id": new, "name": rec.get("name", ""), "type": rec.get("type", ""),
            "role": "container" if i in containers else ("generator" if str(i) in s["generators"] else ""),
            "copy": not note,
            "note": note,
        })
    return {"candidates": candidates, "replacements": _suggest_replacements(project, source, key,
                                                                            s.get("name") or "", name, ids)}


def _connected(project: Project, source: str, ids: List[int]) -> set:
    """The definitions among ``ids`` that belong to the setup of series
    ``source``: its containers, generators and items, and whatever refers to
    them, is referred to by them or names series:source in its exchange
    recipe or tags, step by step."""
    s = project.get_series(source)
    records = project.by_id()
    connected = {int(k) for k in list(s["containers"]) + list(s["generators"])}
    connected |= {m["itemdefid"] for m in project.members(source)}
    refs = {}
    for i in set(ids) | connected:
        try:
            refs[i] = {r for _, r in steam.references(records[i])} if i in records else set()
        except steam.SyntaxProblem:
            refs[i] = set()
    tag = re.compile(r"(^|[;,])series:" + re.escape(source) + r"(?=[;,*]|$)")
    changed = True
    while changed:
        changed = False
        for i in ids:
            if i in connected:
                continue
            rec = records[i]
            if (refs[i] & connected or any(i in refs.get(j, ()) for j in connected)
                    or any(tag.search(rec.get(f) or "") for f in ("exchange", "tags"))):
                connected.add(i)
                changed = True
    return connected


def _suggest_replacements(project: Project, source: str, key: str, old_name: str, new_name: str,
                          ids: List[int]) -> List[List[str]]:
    records = project.by_id()
    texts = [v for i in ids for k, v in records[i].items() if isinstance(v, str) and k not in _NO_TEXT_REPLACE]
    pairs = []
    m_old, m_new = re.match(r"^(.*?)(\d+)$", source), re.match(r"^(.*?)(\d+)$", key)
    if m_old and m_new and m_old.group(1) == m_new.group(1):
        p, a, b = m_old.group(1), m_old.group(2), m_new.group(2)
        for fmt in ("{p}{n}", "{p}_{n}", "{p} {n}", "{P} {n}", "{P}{n}", "{U}{n}", "{U}_{n}"):
            old = fmt.format(p=p, P=p[:1].upper() + p[1:], U=p.upper(), n=a)
            new = fmt.format(p=p, P=p[:1].upper() + p[1:], U=p.upper(), n=b)
            if [old, new] not in pairs and any(old in t for t in texts):
                pairs.append([old, new])
    elif any(source in t for t in texts):
        pairs.append([source, key])
    if new_name and old_name and new_name != old_name:
        pairs.append([old_name, new_name])
        old_words, new_words = old_name.split(), new_name.split()
        common = 0
        while common < min(len(old_words), len(new_words)) - 1 and old_words[-1 - common] == new_words[-1 - common]:
            common += 1
        if common:
            old_head, new_head = " ".join(old_words[:-common]), " ".join(new_words[:-common])
            if old_head and old_head != new_head and any(old_head in t for t in texts):
                pairs.append([old_head, new_head])
    return pairs


def create_series(project: Project, key: str, config: dict, copy_setup: Optional[dict] = None) -> dict:
    """Create a series, optionally copying definitions from another one.

    ``copy_setup``: ``{"source": key, "ids": [...], "new_ids": {old: new},
    "replacements": [[find, replace], ...]}``. Copied definitions get their
    new IDs; references between them follow, ``series:<source>`` becomes
    ``series:<key>`` in tags and exchange recipes, and the replacements are
    applied to their other text. The copies of the source's containers and
    generators become the new series' containers and generators.
    """
    created = []
    config = dict(config)
    if copy_setup:
        source = copy_setup.get("source")
        s = project.get_series(source)
        first = config.get("first_id")
        if not isinstance(first, int) or isinstance(first, bool):
            raise ProjectError("first_id must be a whole number")
        offset = first - s["first_id"]
        overrides = {int(k): int(v) for k, v in (copy_setup.get("new_ids") or {}).items()}
        ids = [int(i) for i in copy_setup.get("ids") or []]
        mapping = {i: overrides.get(i, i + offset) for i in ids}
        records = project.by_id()
        new_range = (first, config.get("last_id", first))
        if len(set(mapping.values())) != len(mapping):
            raise ProjectError("two copied definitions would get the same new ID")
        for old, new in mapping.items():
            if old not in records:
                raise ProjectError(f"there is no definition {old} to copy")
            if project.series_for_id(old) is not None:
                raise ProjectError(f"{old} is an item of a series; only supporting definitions can be copied")
            if new <= 0 or new in records:
                raise ProjectError(f"{old} would be copied to {new}, which is already used")
            if project.series_for_id(new) is not None or new_range[0] <= new <= new_range[1]:
                raise ProjectError(f"{old} would be copied to {new}, which is inside a series' ID range")
        replacements = sorted(([str(a), str(b)] for a, b in copy_setup.get("replacements") or [] if a),
                              key=lambda r: -len(r[0]))
        tag = re.compile(r"(^|;)series:" + re.escape(source) + r"(?=;|$)")
        material = re.compile(r"(^|[;,])series:" + re.escape(source) + r"(?=\*)")
        for old in ids:
            rec = copy.deepcopy(records[old])
            rec["itemdefid"] = mapping[old]
            steam.remap_references(rec, mapping)
            if isinstance(rec.get("tags"), str):
                rec["tags"] = tag.sub(lambda m: f"{m.group(1)}series:{key}", rec["tags"])
            if isinstance(rec.get("exchange"), str):
                rec["exchange"] = material.sub(lambda m: f"{m.group(1)}series:{key}", rec["exchange"])
            for field, value in list(rec.items()):
                if isinstance(value, str) and field not in _NO_TEXT_REPLACE:
                    for find, replace in replacements:
                        value = value.replace(find, replace)
                    rec[field] = value
            project.items.append(rec)
            created.append({"old": old, "new": mapping[old], "name": rec.get("name", "")})
        config.setdefault("containers", {str(mapping[int(c)]): copy.deepcopy(cfg)
                                         for c, cfg in s["containers"].items() if int(c) in mapping})
        config.setdefault("generators", {str(mapping[int(g)]): rule
                                         for g, rule in s["generators"].items() if int(g) in mapping})
        if "description_template" in s:
            config.setdefault("description_template", s["description_template"])
        if "secret" in s:
            config.setdefault("secret", copy.deepcopy(s["secret"]))
    notes = save_series(project, key, config, is_new=True)
    project.items.sort(key=lambda it: it["itemdefid"])
    return {"key": key, "created": created, "notes": notes}
