"""Item kinds, lookup tables and derived fields.

A *kind* describes a family of similar items (for example weapon skins):

* ``fields`` are the values entered for each item (``weapon``, ``finish``,
  ``flavor``...). They are stored in the item's record but not exported.
* ``derive`` gives a rule for each Steam field the kind produces. A rule is a
  template string such as ``"{weapon.name} | {finish}"``, a list of template
  strings (paragraphs: empty ones are dropped and the rest joined with a blank
  line), or any other JSON value, which is used as-is.

A *table* maps keys to rows of values. A field of type ``ref`` holds a key of
a table, so ``{weapon}`` is the key (``pistol``) and ``{weapon.name}``
is that row's ``name`` column (``Pistol``).

Templates use Python's format syntax: ``{field}``, ``{series.index:03d}``,
``{{`` for a literal brace. Besides the kind's fields they can use
``{itemdefid}``, ``{series}`` (key), ``{series.name}``, ``{series.index}``,
``{series.count}`` (items in the series), ``{series.count_no_secret}`` and
``{series.count_secret}`` (without / only the secret rares), any other
derived field of the kind and any other value stored on the item.

A value stored on an item for a derived field overrides the rule.
"""

from __future__ import annotations

import copy
import re
import string
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import steam

FIELD_TYPES = ("text", "multiline", "number", "bool", "ref")
RESERVED_NAMES = ("itemdefid", "kind", "series", "series_name", "index", "count", "count_no_secret",
                  "count_secret")
SERIES_NAMES = ("series_name", "index", "count", "count_no_secret", "count_secret")

_FORMATTER = string.Formatter()
_MISSING = object()
_PATH = re.compile(r"^([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*|\[[^\]]*\])*)$")


class SeriesInfo(NamedTuple):
    key: str
    name: str
    index: int
    count: int  # every item of the series
    count_no_secret: Optional[int] = None  # without the secret rares (None: same as count)
    count_secret: int = 0


class Unknown(Exception):
    """Strict rendering needed a value that is not known."""


# ---------------------------------------------------------------- values


class RefValue:
    """A key of a lookup table; its columns are attributes."""

    __slots__ = ("key", "table", "_ctx")

    def __init__(self, key, table: str, ctx: "Context"):
        self.key = key
        self.table = table
        self._ctx = ctx

    def __str__(self) -> str:
        return str(self.key)

    def __format__(self, spec: str) -> str:
        return format(str(self.key), spec)

    def __getattr__(self, column: str):
        if column.startswith("_"):
            raise AttributeError(column)
        return self._ctx.column(self.table, self.key, column)


class SeriesValue:
    """``{series}`` is the key; ``.name``, ``.index``, ``.count``,
    ``.count_no_secret`` and ``.count_secret`` are attributes."""

    __slots__ = ("key", "name", "index", "count", "count_no_secret", "count_secret")

    def __init__(self, info: Optional[SeriesInfo]):
        self.key = info.key if info else ""
        self.name = info.name if info else ""
        self.index = info.index if info else ""
        self.count = info.count if info else ""
        if info is None:
            self.count_no_secret = self.count_secret = ""
        else:
            self.count_no_secret = info.count if info.count_no_secret is None else info.count_no_secret
            self.count_secret = info.count_secret

    def __str__(self) -> str:
        return self.key

    def __format__(self, spec: str) -> str:
        return format(self.key, spec)


class TagsValue:
    """``{tags}`` is the tag string; ``{tags[rarity]}`` is one tag's value."""

    __slots__ = ("raw",)

    def __init__(self, raw):
        self.raw = raw if isinstance(raw, str) else ""

    def __str__(self) -> str:
        return self.raw

    def __format__(self, spec: str) -> str:
        return format(self.raw, spec)

    def __getitem__(self, category: str) -> str:
        values = [v for c, v in steam.parse_tags(self.raw) if c == category]
        return values[0] if values else ""


# ---------------------------------------------------------------- schema


class Schema:
    def __init__(self, tables: Dict[str, dict], kinds: Dict[str, dict]):
        self.tables = tables
        self.kinds = kinds

    def kind_of(self, record: dict) -> Optional[dict]:
        name = record.get("kind")
        return self.kinds.get(name) if isinstance(name, str) else None

    def rows(self, table: str) -> Dict[str, dict]:
        return (self.tables.get(table) or {}).get("rows") or {}


def fields_of(kind: Optional[dict]) -> Dict[str, dict]:
    return (kind or {}).get("fields") or {}


def rules_of(kind: Optional[dict]) -> Dict[str, object]:
    return (kind or {}).get("derive") or {}


def split_path(path: str) -> Tuple[str, List[str]]:
    """``"weapon.name"`` -> ``("weapon", ["name"])``. Index parts keep their brackets."""
    m = _PATH.match(path)
    if not m:
        raise ValueError(f"invalid field reference {{{path}}}")
    rest = re.findall(r"\.([A-Za-z_]\w*)|(\[[^\]]*\])", m.group(2))
    return m.group(1), [a or b for a, b in rest]


def template_paths(rule) -> List[str]:
    """Every ``{field}`` reference in a rule, in order."""
    out: List[str] = []
    for text in (rule if isinstance(rule, list) else [rule]):
        if not isinstance(text, str):
            continue
        for _, field, _, _ in _FORMATTER.parse(text):
            if field is not None and field not in out:
                out.append(field)
    return out


# --------------------------------------------------------------- context


class Context:
    """The values a template can use. Passed to ``str.format_map``.

    ``values`` are the item's stored values (or, while adopting, the values
    worked out so far). In strict mode anything missing raises Unknown
    instead of rendering as blank.
    """

    def __init__(self, schema: Schema, values: dict, series: Optional[SeriesInfo] = None,
                 kind: Optional[dict] = None, strict: bool = False):
        self.schema = schema
        self.values = values
        self.series = series
        self.kind = kind if kind is not None else schema.kind_of(values)
        self.strict = strict
        self.problems: List[str] = []
        self._cache: Dict[str, object] = {}
        self._resolving: List[str] = []

    def problem(self, text: str) -> None:
        if self.strict:
            raise Unknown(text)
        if text not in self.problems:
            self.problems.append(text)

    def __getitem__(self, name: str):
        return self.value(name)

    def value(self, name: str):
        if name == "itemdefid":
            return self.values.get("itemdefid", "")
        if name == "series":
            return SeriesValue(self.series)
        if name in SERIES_NAMES:
            s = SeriesValue(self.series)
            return s.name if name == "series_name" else getattr(s, name)
        fields = fields_of(self.kind)
        rules = rules_of(self.kind)
        stored = self.values.get(name, _MISSING)
        if name in fields:
            default = fields[name].get("default")
            if stored not in (_MISSING, None, ""):
                raw = stored
            elif isinstance(default, str) and default:
                raw = self.render(default, f"default of {name}")
            elif stored == "":
                raw = ""
            elif self.strict:
                raise Unknown(name)
            else:
                raw = ""
        elif stored is not _MISSING:
            raw = stored
        elif name in rules:
            raw = self.derived(name)
        else:
            self.problem(f"unknown field {{{name}}}")
            return ""
        return self.wrap(name, raw)

    def wrap(self, name: str, raw):
        spec = fields_of(self.kind).get(name) or {}
        if spec.get("type") == "ref" and raw not in (None, ""):
            return RefValue(raw, spec.get("table", ""), self)
        if name == "tags":
            return TagsValue(raw)
        return raw

    def column(self, table: str, key, column: str):
        t = self.schema.tables.get(table)
        if t is None:
            self.problem(f"there is no table {table!r}")
            return ""
        if column not in (t.get("columns") or []):
            self.problem(f"table {table!r} has no column {column!r}")
            return ""
        row = (t.get("rows") or {}).get(str(key))
        if row is None:
            self.problem(f"{key!r} is not a row of table {table!r}")
            return ""
        value = row.get(column)
        if value in (None, ""):
            self.problem(f"table {table!r}: row {key!r} has no {column}")
            return ""
        return value

    def derived(self, name: str):
        if name in self._cache:
            return self._cache[name]
        if name in self._resolving:
            self.problem(f"{name} depends on itself ({' -> '.join(self._resolving + [name])})")
            return ""
        self._resolving.append(name)
        try:
            value = self.render_rule(rules_of(self.kind)[name], name)
        finally:
            self._resolving.pop()
        self._cache[name] = value
        return value

    def render_rule(self, rule, label: str):
        if isinstance(rule, str):
            return self.render(rule, label)
        if isinstance(rule, list):
            parts = [self.render(p, label).strip() if isinstance(p, str) else str(p) for p in rule]
            return "\n\n".join(p for p in parts if p)
        return copy.deepcopy(rule)

    def render(self, template: str, label: str) -> str:
        try:
            return template.format_map(self)
        except Unknown:
            raise
        except (KeyError, ValueError, IndexError, AttributeError, TypeError) as e:
            self.problem(f"{label}: {_explain(e)}")
            return ""


def _explain(e: Exception) -> str:
    if isinstance(e, IndexError):
        return "use named fields such as {name}; {} and {0} are not supported"
    return str(e).strip("'\"") or type(e).__name__


# --------------------------------------------------------------- resolve


def resolve(schema: Schema, record: dict, series: Optional[SeriesInfo] = None) -> Tuple[dict, List[str]]:
    """The Steam definition for a record (without series text) and the
    problems found while building it."""
    kind = schema.kind_of(record)
    if kind is None:
        out = copy.deepcopy(record)
        problems = []
        if "kind" in out:
            problems.append(f"unknown kind {out['kind']!r}")
            out.pop("kind")
        return out, problems

    ctx = Context(schema, record, series, kind)
    fields = fields_of(kind)
    out = {"itemdefid": record["itemdefid"]}
    for field in rules_of(kind):
        out[field] = copy.deepcopy(record[field]) if field in record else ctx.derived(field)
    for key, value in record.items():
        if key not in out and key != "kind" and key not in fields:
            out[key] = copy.deepcopy(value)

    for name, spec in fields.items():
        value = record.get(name)
        if value in (None, ""):
            if not spec.get("optional") and not spec.get("default"):
                ctx.problem(f"{name} is empty")
            continue
        kind_type = spec.get("type", "text")
        if kind_type == "ref" and str(value) not in schema.rows(spec.get("table", "")):
            ctx.problem(f"{name}: {value!r} is not a row of table {spec.get('table')!r}")
        elif kind_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            ctx.problem(f"{name} should be a number")
        elif kind_type == "bool" and not isinstance(value, bool):
            ctx.problem(f"{name} should be true or false")
    return out, ctx.problems


def overridden(schema: Schema, record: dict) -> List[str]:
    """Derived fields whose rule is overridden by a stored value."""
    kind = schema.kind_of(record)
    fields = fields_of(kind)
    return [f for f in rules_of(kind) if f in record and f not in fields]


# ----------------------------------------------------------- definitions


def check_definitions(schema: Schema) -> List[str]:
    """Problems with the tables and kinds themselves."""
    out = []
    for name, t in schema.tables.items():
        if not isinstance(t, dict) or not isinstance(t.get("columns", []), list) \
                or not isinstance(t.get("rows", {}), dict):
            out.append(f"table {name!r} needs a \"columns\" list and a \"rows\" object")
            continue
        for key, row in (t.get("rows") or {}).items():
            if not isinstance(row, dict):
                out.append(f"table {name!r}: row {key!r} must be an object")
            else:
                extra = [c for c in row if c not in t.get("columns", [])]
                if extra:
                    out.append(f"table {name!r}: row {key!r} has undeclared column(s) {', '.join(extra)}")
    for name, kind in schema.kinds.items():
        if not isinstance(kind, dict) or not isinstance(kind.get("fields", {}), dict) \
                or not isinstance(kind.get("derive", {}), dict):
            out.append(f"kind {name!r} needs \"fields\" and \"derive\" objects")
            continue
        for field, spec in fields_of(kind).items():
            if field in RESERVED_NAMES:
                out.append(f"kind {name!r}: {field!r} is a reserved name")
            if not isinstance(spec, dict):
                out.append(f"kind {name!r}: field {field!r} must be an object")
                continue
            if spec.get("type", "text") not in FIELD_TYPES:
                out.append(f"kind {name!r}: field {field!r} has unknown type {spec.get('type')!r} "
                           f"(use {', '.join(FIELD_TYPES)})")
            if spec.get("type") == "ref" and spec.get("table") not in schema.tables:
                out.append(f"kind {name!r}: field {field!r} refers to missing table {spec.get('table')!r}")
        for field, rule in rules_of(kind).items():
            for text in (rule if isinstance(rule, list) else [rule]):
                if not isinstance(text, str):
                    continue
                try:
                    for _, ref, _, _ in _FORMATTER.parse(text):
                        if ref is not None:
                            split_path(ref)
                except ValueError as e:
                    out.append(f"kind {name!r}: rule for {field!r}: {e}")
    return out
