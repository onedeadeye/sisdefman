"""Edits shared by the command line and the GUI: item fields, lookup tables
and the schema (tables + kinds)."""

from __future__ import annotations

import copy
from typing import Dict, Iterable, List, Optional, Tuple

from . import derive, steam
from .project import CONTENTS_TOKEN, Project, ProjectError

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


def rows_in_use(project: Project, table: str) -> Dict[str, List[int]]:
    """Row key -> itemdefids referring to it."""
    fields = ref_fields(project, table)
    out: Dict[str, List[int]] = {}
    for rec in project.items:
        for field in fields.get(rec.get("kind"), []):
            if rec.get(field) not in (None, ""):
                out.setdefault(str(rec[field]), []).append(rec["itemdefid"])
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
    table["rows"] = {(new if k == old else k): v for k, v in table["rows"].items()}
    fields = ref_fields(project, name)
    count = 0
    for rec in project.items:
        for field in fields.get(rec.get("kind"), []):
            if rec.get(field) == old:
                rec[field] = new
                count += 1
    return count


def replace_table(project: Project, name: str, table: dict, renames: Optional[Dict[str, str]] = None) -> None:
    """Replace a whole table (the GUI's table editor). ``renames`` maps old
    row keys to new ones so references follow."""
    if not isinstance(table.get("columns"), list) or not isinstance(table.get("rows"), dict):
        raise ProjectError("a table needs a columns list and a rows object")
    renames = {o: n for o, n in (renames or {}).items() if o != n}
    fields = ref_fields(project, name)
    for rec in project.items:
        for field in fields.get(rec.get("kind"), []):
            if rec.get(field) in renames:
                rec[field] = renames[rec[field]]
    project.tables[name] = {"columns": list(table["columns"]),
                            "rows": {str(k): dict(v) for k, v in table["rows"].items()}}


def delete_table(project: Project, name: str) -> None:
    _table(project, name)
    users = ref_fields(project, name)
    if users:
        raise ProjectError(f"table {name!r} is used by kind(s) {', '.join(users)}")
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
        s = {"name": key, "first_id": config["first_id"], "last_id": config["last_id"],
             "allocated_through": None, "containers": {}, "generators": {}}
    else:
        s = copy.deepcopy(project.get_series(key))
        if "first_id" in config and config["first_id"] != s["first_id"]:
            raise ProjectError("the first ID of an existing series cannot change")
    if config.get("name") not in (None, ""):
        s["name"] = str(config["name"])
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
        if CONTENTS_TOKEN not in (by_id[int(cid)].get("description") or ""):
            notes.append(f"Put {CONTENTS_TOKEN} in the description of {cid} where the item list should go.")
    project.series[key] = s
    return notes


def delete_series(project: Project, key: str) -> None:
    s = project.get_series(key)
    if project.members(key) or s.get("allocated_through"):
        raise ProjectError(f"series {key!r} has items or used IDs; only an unused series can be deleted")
    del project.series[key]
