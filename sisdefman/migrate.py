"""Upgrading older project files.

Before format version 5 a *description template* (``settings`` and
optionally per series) added a series line to the description of every
series item on export: ``"{description}\\n\\n{series_name} #{index}"``. The
line is now part of the items themselves, written with the references any
template can use (``{series.name} #{series.index}``):

* a kind whose items are in series gets the line in its ``description``
  rule, as a paragraph of its own (left out for items in no series);
* an item without a kind, or with its own description, gets it in its
  stored description.

The exported definitions stay the same.
"""

from __future__ import annotations

import re
import os
import shutil
from typing import Dict, List, Optional

from . import derive

# Old series-line names -> references usable in every template.
_NAMES = {"series_name": "series.name", "index": "series.index", "count": "series.count",
          "count_no_secret": "series.count_no_secret", "count_secret": "series.count_secret"}
_FIELD = re.compile(r"\{\{|\}\}|\{([A-Za-z_]\w*)([^{}]*)\}")


def convert(template: str, stored: bool = False) -> str:
    """``{series_name} #{index:03d}`` -> ``{series.name} #{series.index:03d}``.
    In stored text, where ``{{`` is not an escape, braces are unescaped."""
    def sub(m):
        if m.group(0) in ("{{", "}}"):
            return m.group(0)[0] if stored else m.group(0)
        name, rest = m.group(1), m.group(2)
        if name in _NAMES and (not rest or rest[0] in ":!"):
            return "{" + _NAMES[name] + rest + "}"
        return m.group(0)

    return _FIELD.sub(sub, template)


def _fold_text(template: str, base) -> str:
    """A stored description with the series line of ``template`` around it."""
    t = convert(template, stored=True)
    base = base if isinstance(base, str) else ""
    head, found, tail = t.partition("{description}")
    text = head + base + tail if found else t
    return text if base.strip() else text.strip()


def _fold_rule(template: str, rule, paragraphs: bool, optional_fields=()):
    """A kind's description rule with the series line of ``template`` added.
    ``paragraphs``: some items of the kind are in no series, so the line must
    be a paragraph of its own (left out when its series values are empty)."""
    t = convert(template)
    if "{description}" not in t:
        return [t] if paragraphs else t
    head, _, tail = t.partition("{description}")
    if isinstance(rule, list) or paragraphs:
        paras = list(rule) if isinstance(rule, list) else ([rule] if isinstance(rule, str) and rule else [])
        if (not head or head.endswith("\n\n")) and (not tail or tail.startswith("\n\n")):
            before = head[:-2].split("\n\n") if head else []
            after = tail[2:].split("\n\n") if tail else []
            return before + paras + after
        # Text around the description on the same line: attach it to the first
        # and last paragraphs that are always there (not an optional {flavor}).
        steady = [n for n, p in enumerate(paras) if isinstance(p, str) and not any(
            f.split(".")[0].split("[")[0] in optional_fields for f in derive.template_paths(p))]
        if not steady:
            return head + "\n\n".join(p for p in paras if isinstance(p, str)) + tail
        paras[steady[0]] = head + paras[steady[0]]
        paras[steady[-1]] = paras[steady[-1]] + tail
        return paras
    base = rule if isinstance(rule, str) else ""
    return head + base + tail if base else (head + tail).strip()


def save_upgrade(project) -> Optional[str]:
    """Write an upgraded project file, keeping the old one next to it
    (sisdefman.json -> sisdefman.v4.json). Returns the backup's path."""
    if project.upgraded_from is None or not project.path or not os.path.isfile(project.path):
        return None
    base, ext = os.path.splitext(project.path)
    backup = f"{base}.v{project.upgraded_from}{ext or '.json'}"
    if not os.path.exists(backup):
        shutil.copyfile(project.path, backup)
    project.save()
    project.upgraded_from = None
    return backup


def fold_series_lines(project, global_template: Optional[str], per_series: Dict[str, str]) -> List[str]:
    notes: List[str] = []
    schema = project.schema()

    def template_of(key: str) -> Optional[str]:
        t = per_series.get(key) or global_template
        return t if isinstance(t, str) and t.strip() not in ("", "{description}") else None

    series_of = {m["itemdefid"]: key for key in project.series for m in project.members(key)}
    by_kind: Dict[str, Dict[str, List[int]]] = {}
    stored = 0
    for rec in project.items:
        key = series_of.get(rec["itemdefid"])
        template = template_of(key) if key else None
        if template is None:
            continue
        if schema.kind_of(rec) is not None and "description" not in rec:
            by_kind.setdefault(rec["kind"], {}).setdefault(template, []).append(rec["itemdefid"])
        else:
            rec["description"] = _fold_text(template, rec.get("description"))
            stored += 1

    positions = project.series_positions()
    for kind_name, groups in by_kind.items():
        kind = project.kinds[kind_name]
        rules = kind.setdefault("derive", {})
        old_rule = rules.get("description")
        main = max(groups, key=lambda t: len(groups[t]))
        users = [r for r in project.items if r.get("kind") == kind_name and "description" not in r]
        others = [r for r in users if r["itemdefid"] not in groups[main]]
        # What the other items show now (their description without a series line).
        before = {r["itemdefid"]: derive.resolve(project.schema(), r, positions.get(r["itemdefid"]))[0]
                  .get("description", "") for r in others}
        optional = [f for f, spec in derive.fields_of(kind).items() if isinstance(spec, dict) and spec.get("optional")]
        rules["description"] = _fold_rule(main, old_rule, paragraphs=any(
            r["itemdefid"] not in series_of for r in others), optional_fields=optional)
        notes.append(f"kind {kind_name!r}: its description rule now ends with the series line")
        kept = []
        for rec in others:
            i = rec["itemdefid"]
            template = template_of(series_of[i]) if i in series_of else None
            if template:  # in a series with a different line
                rec["description"] = _fold_text(template, before[i])
                kept.append(i)
            elif derive.resolve(project.schema(), rec, positions.get(i))[0].get("description", "") != before[i]:
                rec["description"] = before[i]
                kept.append(i)
        if kept:
            notes.append(f"kind {kind_name!r}: item(s) {', '.join(map(str, kept[:10]))} keep their current "
                         "description as their own, as the new rule would show them differently; check them")
    if stored:
        notes.append(f"{stored} stored description(s) now contain their series line")
    if by_kind or stored:
        notes.insert(0, "The series line setting is gone: series lines are now written with {series.name}, "
                        "{series.index} and similar references, in kind rules and item fields.")
    return notes
