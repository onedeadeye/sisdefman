"""Importing CSV files, such as Unreal Engine DataTable exports, into lookup
tables for reference.

The first row names the columns and one column (by default the first, which
Unreal calls ``---`` or ``Name``) holds the row keys. Values in Unreal's
export formats are cleaned up:

* ``NSLOCTEXT("ns", "key", "Handgun")`` / ``LOCTEXT(...)`` / ``INVTEXT(...)``
  -> ``Handgun``
* a data table row handle ``(DataTable="...",RowName="Basic")`` -> ``Basic``
* an asset or class path ``/Game/.../BP_Pistol.BP_Pistol_C`` -> ``BP_Pistol_C``

Keys match existing rows ignoring case, so ``Pistol`` in the file
updates the row ``pistol``. Each CSV column is stored in a table column
of the same name (or the name given with ``rename``), which leaves the
columns that items use untouched unless asked. ``fills`` copies a CSV column
into another table column only where that column is empty, and reports the
rows where the two disagree.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .project import Project, ProjectError

_STR = r'"((?:[^"\\]|\\.)*)"'
_LOCTEXT = re.compile(
    r'^(?:NSLOCTEXT\(\s*' + _STR + r'\s*,\s*' + _STR + r'\s*,|LOCTEXT\(\s*' + _STR + r'\s*,|INVTEXT\()\s*'
    + _STR + r'\s*\)$', re.S)
_ROW_HANDLE = re.compile(r'^\(.*\bRowName=' + _STR + r'\s*\)$', re.S)
_QUOTED_PATH = re.compile(r"^[\w./]*'(/[^']+)'$")
_PATH = re.compile(r"^/\w+(?:/[\w\-. ]+)+$")
KEY_CASES = ("auto", "lower", "keep")


def _unescape(text: str) -> str:
    return re.sub(r'\\(.)', lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), text)


def clean_value(value: str) -> str:
    """Unreal export notation -> the plain value."""
    v = value.strip()
    m = _LOCTEXT.match(v)
    if m:
        return _unescape(m.group(m.lastindex))
    m = _ROW_HANDLE.match(v)
    if m:
        return _unescape(m.group(1))
    m = _QUOTED_PATH.match(v)
    path = m.group(1) if m else v
    if _PATH.match(path) and "." in path.rsplit("/", 1)[-1]:
        return path.rsplit(".", 1)[-1]
    return v


def column_name(header: str) -> str:
    """A CSV header as a table column name usable in templates."""
    name = re.sub(r"\W+", "_", header.strip()).strip("_") or "column"
    return "c_" + name if name[0].isdigit() else name


@dataclass
class CsvData:
    header: List[str]
    rows: List[List[str]]


def read_csv(text: str) -> CsvData:
    if text.startswith("﻿"):
        text = text[1:]
    try:
        rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    except csv.Error as e:
        raise ProjectError(f"cannot read the CSV: {e}")
    if not rows:
        raise ProjectError("the CSV file is empty")
    width = len(rows[0])
    return CsvData(rows[0], [r + [""] * (width - len(r)) for r in rows[1:]])


@dataclass
class TableImportReport:
    table: str
    created: bool = False
    added: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    columns_added: List[str] = field(default_factory=list)
    changes: List[str] = field(default_factory=list)  # existing values replaced
    filled: int = 0
    kept: List[str] = field(default_factory=list)  # fill values that disagree with the table
    skipped: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    def lines(self, verbose: bool = False) -> List[str]:
        out = [f"table {self.table}{' (new)' if self.created else ''}: {len(self.added)} row(s) added, "
               f"{len(self.updated)} matched existing rows"
               + (f", {len(self.columns_added)} column(s) added ({', '.join(self.columns_added)})"
                  if self.columns_added else "")
               + (f", {self.filled} empty value(s) filled" if self.filled else "")]
        shown = self.changes if verbose else self.changes[:15]
        if self.changes:
            out.append(f"{len(self.changes)} existing value(s) replaced:")
            out += [f"  {c}" for c in shown]
            if len(shown) < len(self.changes):
                out.append(f"  ... and {len(self.changes) - len(shown)} more (-v lists them)")
        if self.kept:
            out.append(f"{len(self.kept)} value(s) differ from the file and were kept:")
            out += [f"  {k}" for k in self.kept]
        out += [f"skipped: {s}" for s in self.skipped]
        return out


def import_csv(
    project: Project,
    table_name: str,
    data: CsvData,
    key_column: Optional[str] = None,
    only: Optional[Iterable[str]] = None,
    skip: Iterable[str] = (),
    rename: Optional[Dict[str, str]] = None,
    fills: Iterable[Tuple[str, str]] = (),
    key_case: str = "auto",
    raw: bool = False,
) -> TableImportReport:
    """Merge ``data`` into ``project.tables[table_name]`` (created if needed)."""
    if not table_name.isidentifier():
        raise ProjectError("table names may only contain letters, digits and _")
    if key_case not in KEY_CASES:
        raise ProjectError(f"key case must be one of {', '.join(KEY_CASES)}")
    header = data.header
    rename = dict(rename or {})
    only = None if only is None else list(only)
    skip = list(skip)
    fills = list(fills)
    for name in list(rename) + (only or []) + skip + [c for _, c in fills] + ([key_column] if key_column else []):
        if name not in header:
            raise ProjectError(f"the CSV has no column {name!r} (columns: {', '.join(header)})")
    key_index = header.index(key_column) if key_column else 0

    columns: List[Tuple[int, str]] = []
    for n, h in enumerate(header):
        if n == key_index or h in skip or (only is not None and h not in only):
            continue
        target = rename.get(h) or column_name(h)
        if not target.isidentifier():
            raise ProjectError(f"invalid column name {target!r}")
        if target in (t for _, t in columns):
            raise ProjectError(f"two CSV columns would both be stored as {target!r}; rename one")
        columns.append((n, target))
    fill_specs = []
    for target, source in fills:
        if not target.isidentifier():
            raise ProjectError(f"invalid column name {target!r}")
        fill_specs.append((target, header.index(source), source))

    report = TableImportReport(table_name)
    table = project.tables.get(table_name)
    if table is None:
        table = project.tables[table_name] = {"columns": [], "rows": {}}
        report.created = True
    table.setdefault("columns", [])
    rows = table.setdefault("rows", {})
    for target in [t for _, t in columns] + [t for t, _, _ in fill_specs]:
        if target not in table["columns"]:
            table["columns"].append(target)
            report.columns_added.append(target)

    existing = {k.lower(): k for k in rows}
    lower = key_case == "lower" or (key_case == "auto" and bool(rows) and all(k == k.lower() for k in rows))
    clean = (lambda v: v.strip()) if raw else clean_value
    seen = set()
    for line, values in enumerate(data.rows, 2):
        source_key = values[key_index].strip() if key_index < len(values) else ""
        if not source_key:
            report.skipped.append(f"line {line}: no key")
            continue
        key = existing.get(source_key.lower()) or (source_key.lower() if lower else source_key)
        if key in seen:
            report.skipped.append(f"line {line}: key {source_key!r} appears again (this row wins)")
        seen.add(key)
        if key in rows:
            if key not in report.updated:
                report.updated.append(key)
        else:
            rows[key] = {}
            existing[key.lower()] = key
            report.added.append(key)
        row = rows[key]
        for n, target in columns:
            value = clean(values[n])
            if value == "":
                continue
            old = row.get(target)
            if old not in (None, "") and old != value and key not in report.added:
                report.changes.append(f"{key}.{target}: {old!r} -> {value!r}")
            row[target] = value
        for target, n, source in fill_specs:
            value = clean(values[n])
            if value == "":
                continue
            old = row.get(target)
            if old in (None, ""):
                row[target] = value
                report.filled += 1
            elif old != value:
                report.kept.append(f"{key}.{target} is {old!r}; the file's {source} is {value!r}")
    return report
