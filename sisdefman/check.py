"""Consistency checks for a project."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import colors, derive, steam
from .project import LISTING_TOKENS, NO_DISPLAY_NAME, Project, ProjectError

LEVELS = ("error", "warning", "note")

# Text players read: name, description, display_type and their localized
# versions (name_german, ...).
_VISIBLE = re.compile(r"^(?:name|description|display_type)(?:_[a-z]+)?$")
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][\w.]*(?:\[[^\]]*\])?(?:![rsa])?(?::[^{}]*)?\}")
_IDENTIFIER = re.compile(r"^[a-z0-9_]+$")


def is_visible_field(name: str) -> bool:
    return bool(_VISIBLE.match(name)) and not colors.is_color_field(name)


@dataclass
class Issue:
    level: str
    text: str
    itemdefid: Optional[int] = None

    def __str__(self) -> str:
        where = f"[{self.itemdefid}] " if self.itemdefid is not None else ""
        return f"{self.level}: {where}{self.text}"


def check_project(project: Project) -> List[Issue]:
    issues: List[Issue] = []

    def add(level: str, text: str, itemdefid: Optional[int] = None) -> None:
        issues.append(Issue(level, text, itemdefid))

    for problem in derive.check_definitions(project.schema()) + colors.check_palette(project.colors):
        add("error", problem)
    try:
        built, problems = project.build_with_problems()
    except (ProjectError, steam.SyntaxProblem) as e:
        add("error", str(e))
        return issues
    exported = {it["itemdefid"]: it for it in built}
    managed = project.managed_generator_ids()
    referenced = set()
    for it in built:
        try:
            referenced.update(r for _, r in steam.references(it))
        except steam.SyntaxProblem:
            pass
    containers = {cid: key for key in project.series for cid in project.container_ids(key)}

    # ------------------------------------------------------------ each item
    unnamed: Dict[str, List[int]] = {}
    for i, found in problems.items():
        for problem in found:
            m = re.match(r"series (['\"])(.+?)\1 " + NO_DISPLAY_NAME, problem)
            if m:
                unnamed.setdefault(m.group(2), []).append(i)
                continue
            add("error" if problem.startswith(("unknown kind", "unknown colour")) else "warning", problem, i)
    for key, ids in unnamed.items():
        add("error", f"series {key!r} {NO_DISPLAY_NAME}, so the text of {_ranges(ids)} would show its key "
                     f"{key!r}. Set one on the Series setup page or with `sisdefman series set {key} --name NAME`.")
    for key in project.series:
        if key not in unnamed and not project.has_display_name(key):
            add("note", f"series {key!r} {NO_DISPLAY_NAME} (none of its text uses it yet)")
    records = project.by_id()
    for it in (exported[i] for i in records):
        i = it["itemdefid"]
        if i <= 0:
            add("error", "itemdefid must be positive", i)
        kind = it.get("type")
        if kind not in steam.ITEM_TYPES:
            add("error", f"unknown type {kind!r} (expected one of {', '.join(steam.ITEM_TYPES)})", i)
        if not it.get("name"):
            add("warning", "has no name", i)
        for color in ("name_color", "background_color"):
            if color in it and not steam.is_hex_color(it[color]):
                add("warning", f"{color} {it[color]!r} is not a six-digit hex colour", i)
        try:
            refs = steam.references(it)
        except steam.SyntaxProblem as e:
            add("error", str(e), i)
            refs = []
        missing = sorted({ref for _, ref in refs if ref not in exported})
        if missing:
            add("warning",
                f"refers to itemdefid {', '.join(map(str, missing))}, which "
                f"{'is' if len(missing) == 1 else 'are'} not defined in this project", i)
        dummies = sorted({ref for _, ref in refs if ref in exported and project.is_dummy(exported[ref])})
        if dummies:
            add("warning", f"refers to dummy item(s) {', '.join(map(str, dummies))}", i)
        if kind == "generator" and not (it.get("bundle") or "").strip() and i not in managed:
            if (it.get("exchange") or "").strip():
                add("warning", "has an exchange recipe but an empty bundle: players who exchange for it get "
                               "nothing", i)
            elif i in referenced:  # otherwise reported as unreachable below
                add("note", "generator has an empty bundle, so it grants nothing", i)

        in_series = project.series_for_id(i)
        for key in steam.tag_values(it, "series"):
            if key not in project.series:
                continue
            if in_series != key and containers.get(i) != key:
                add("warning", f"is tagged series:{key} but is outside that series' ID range", i)

    _unreachable(exported, managed, referenced, add)
    _outliers(project, built, add)

    # --------------------------------------------------------------- series
    keys = list(project.series)
    for n, a in enumerate(keys):
        for b in keys[n + 1:]:
            sa, sb = project.series[a], project.series[b]
            if sa["first_id"] <= sb["last_id"] and sb["first_id"] <= sa["last_id"]:
                add("error", f"series {a!r} ({sa['first_id']}-{sa['last_id']}) overlaps series "
                             f"{b!r} ({sb['first_id']}-{sb['last_id']})")

    owners = {}
    for key, s in project.series.items():
        for ref in list(s["containers"]) + list(s["generators"]):
            owners.setdefault(int(ref), []).append(key)
    for ref, keys_ in sorted(owners.items()):
        if len(keys_) > 1:
            add("error", f"is a container or generator of more than one series ({', '.join(keys_)})", ref)

    by_id = project.by_id()
    for key, s in project.series.items():
        members = project.members(key)
        through = s.get("allocated_through")
        if through is not None and through > s["last_id"]:
            add("error", f"series {key!r}: allocated_through {through} is past last_id {s['last_id']}")
        if members:
            ids = [m["itemdefid"] for m in members]
            gaps = [i for i in range(ids[0], ids[-1] + 1) if i not in set(ids)]
            if ids[0] != s["first_id"]:
                gaps = list(range(s["first_id"], ids[0])) + gaps
            if gaps:
                add("note", f"series {key!r}: unused ID(s) {_ranges(gaps)} between its items are exported "
                            "as dummy items")
        for m in (exported[r["itemdefid"]] for r in members):
            if key not in steam.tag_values(m, "series"):
                add("warning", f"is in series {key!r} but not tagged series:{key}", m["itemdefid"])
            elif len(steam.tag_values(m, "series")) > 1:
                add("warning", "has more than one series: tag", m["itemdefid"])
            if project.is_dummy(m):
                add("note", f"a dummy item is stored inside series {key!r}; it counts as a series item",
                    m["itemdefid"])

        for cid_text, cfg in s["containers"].items():
            cid = int(cid_text)
            c = by_id.get(cid)
            if c is None:
                add("error", f"series {key!r}: container {cid} does not exist")
            elif project.series_for_id(cid) is not None:
                add("error", f"series {key!r}: container {cid} is inside a series' ID range")
            elif not any(t in (c.get("description") or "") for t in LISTING_TOKENS):
                add("warning", f"container of series {key!r} has no {{contents}} in its description, "
                               "so its item list is not generated", cid)
            try:
                rules = [steam.parse_tag_rule(r) for r in (cfg or {}).get("exclude", [])]
            except steam.SyntaxProblem as e:
                add("error", f"series {key!r}: container {cid}: {e}")
                rules = []
            if any(not r for r in rules):
                add("warning", f"series {key!r}: container {cid} has an empty exclude rule")

        used = max([s.get("allocated_through") or s["first_id"]] + [m["itemdefid"] for m in members])
        block = max(100, 10 ** len(str(used - s["first_id"] + 1)))
        if s["last_id"] - s["first_id"] + 1 > 10 * block:
            add("warning", f"series {key!r} reserves IDs {s['first_id']}-{s['last_id']} for {len(members)} "
                           "item(s); every definition added in that range becomes one of its items. Shrink it "
                           f"with `sisdefman series set {key} --last-id "
                           f"{(used // block + 1) * block - 1}` (or on the Series setup page).")

        if "secret" in s and members and not project.secret_ids(key):
            add("warning", f"series {key!r}: no item matches its secret rares rule {s['secret']!r}")

        for gid_text, rule in s["generators"].items():
            gid = int(gid_text)
            try:
                tags = steam.parse_tag_rule(rule)
            except steam.SyntaxProblem as e:
                add("error", f"series {key!r}: generator {gid}: {e}")
                continue
            g = by_id.get(gid)
            if g is None:
                add("error", f"series {key!r}: generator {gid} does not exist")
            elif project.series_for_id(gid) is not None:
                add("error", f"series {key!r}: generator {gid} is inside a series' ID range")
            elif not tags:
                add("error", f"series {key!r}: generator {gid} has an empty rule")
            elif not project.generator_bundle(key, tags):
                add("warning", f"no item of series {key!r} is tagged {';'.join(tags)}, so this generator "
                               "grants nothing", gid)

    for it in built:
        left = [t for t in LISTING_TOKENS if t in (it.get("description") or "")]
        if left:
            add("warning", f"description still contains {left[0]}; only containers configured in a "
                           "series get it filled in", it["itemdefid"])

    _visible_text(project, built, add)

    if project.mode == "release" and project.live_names() is None:
        add("warning", "release mode, but no live baseline is recorded. Run `sisdefman mark-live` so "
                       "changes to live items can be detected.")

    order = {lvl: n for n, lvl in enumerate(LEVELS)}
    issues.sort(key=lambda x: (order[x.level], x.itemdefid if x.itemdefid is not None else -1))
    return issues


def _unreachable(exported: Dict[int, dict], managed: set, referenced: set, add) -> None:
    """Tag generators no generator uses, and generators nothing can grant."""
    for i, it in exported.items():
        kind = it.get("type")
        if i in referenced or i in managed:
            continue
        if kind == "tag_generator":
            add("note", "tag generator is not used by any generator's tag_generators", i)
        elif kind in ("generator", "bundle") and not any((it.get(f) or "").strip() for f in ("exchange", "promo")):
            add("note", "nothing refers to this definition and it has no exchange recipe or promo, so players "
                        "can only get it if your game server grants it", i)


def _outliers(project: Project, built: List[dict], add) -> None:
    """Items whose colour, tradable or marketable differs from the others
    with the same tag, where that tag decides it for nearly every item (e.g.
    one rarity:rare item in the common colour)."""
    items = [it for it in built if it.get("type") == "item" and not project.is_dummy(it)]
    groups: Dict[str, Dict[str, List[dict]]] = {}
    for it in items:
        for t in steam.tag_strings(it):
            category, _, value = t.partition(":")
            groups.setdefault(category, {}).setdefault(value, []).append(it)
    fields = sorted({f for it in items for f in it if colors.is_color_field(f)}) + ["tradable", "marketable"]

    def norm(v):
        return v.lower() if isinstance(v, str) else json.dumps(v)

    for field in fields:
        candidates = []
        for category, by_value in groups.items():
            big = [(v, its) for v, its in by_value.items() if len(its) >= 3]
            if len(big) < 2:
                continue
            stats = [(v, Counter(norm(it.get(field)) for it in its).most_common(1)[0], its) for v, its in big]
            purity = sum(n for _, (_, n), _ in stats) / sum(len(its) for _, _, its in stats)
            candidates.append((purity, category, stats))
        if any(purity == 1 for purity, _, _ in candidates):
            continue  # some tag decides it for every item: nothing stands out
        for purity, category, stats in candidates:
            if purity < 0.9:
                continue
            for value, (top, n), its in stats:
                if n == len(its) or n * 2 <= len(its):
                    continue
                shown = next(it.get(field) for it in its if norm(it.get(field)) == top)
                for it in its:
                    if norm(it.get(field)) != top:
                        add("warning", f"{field} is {json.dumps(it.get(field))}, but {n} of the {len(its)} items "
                                       f"tagged {category}:{value} have {json.dumps(shown)}", it["itemdefid"])


def _paths(rule) -> List[str]:
    try:
        return derive.template_paths(rule)
    except ValueError:
        return []  # reported elsewhere


def _visible_text(project: Project, built: List[dict], add) -> None:
    """Internal identifiers that would reach players: series keys and table
    row keys used in templates for visible text, and placeholders or colour
    keywords left in the exported text."""
    templates = [("the description template", project.settings.get("description_template"))]
    templates += [(f"series {key!r}: its description template", s.get("description_template"))
                  for key, s in project.series.items() if s.get("description_template")]
    for label, template in templates:
        if "series" in _paths(template):
            add("warning", f"{label} uses {{series}}, the series' key; use {{series_name}} for its display name")
    for kname, kind in project.kinds.items():
        if not isinstance(kind, dict):
            continue
        fields = derive.fields_of(kind)
        for field, rule in derive.rules_of(kind).items():
            if not is_visible_field(field):
                continue
            for path in _paths(rule):
                try:
                    head, rest = derive.split_path(path)
                except ValueError:
                    continue
                if rest:
                    continue
                spec = fields.get(head)
                if head == "series":
                    add("warning", f"kind {kname!r}: the rule for {field} uses {{series}}, the series' key; use "
                                   "{series.name} for its display name")
                elif isinstance(spec, dict) and spec.get("type") == "ref":
                    _bare_key(project, head, spec.get("table"), f"kind {kname!r}: the rule for {field}", add)
                elif spec is None and head in project.tables and head not in derive.rules_of(kind):
                    _bare_key(project, head, head, f"kind {kname!r}: the rule for {field}", add)
    for rec in project.items:
        kind = project.schema().kind_of(rec)
        kind_fields = derive.fields_of(kind)
        for field, value in rec.items():
            if field in kind_fields or not is_visible_field(field):
                continue
            for m in re.finditer(r"\{([A-Za-z_]\w*)(?:![rsa])?(?::[^{}]*)?\}", value if isinstance(value, str) else ""):
                head = m.group(1)
                if head in project.tables and not kind_fields.get(head):
                    _bare_key(project, head, head, f"{field}", add, rec["itemdefid"])
                elif head == "series" and project.series_for_id(rec["itemdefid"]):
                    add("warning", f"{field} uses {{series}}, the series' key; use {{series.name}} for its display "
                                   "name", rec["itemdefid"])
    for it in built:
        for field, value in it.items():
            if not isinstance(value, str) or not is_visible_field(field):
                continue
            for token in dict.fromkeys(_PLACEHOLDER.findall(value)):
                head = re.match(r"\{([A-Za-z_]\w*)", token).group(1)
                # Listing tokens and table references that failed are reported above.
                if token not in LISTING_TOKENS and head not in project.tables:
                    add("warning", f"{field} contains {token}, which is not filled in here, so players would "
                                   "see it as it is", it["itemdefid"])
            for kw in dict.fromkeys(re.findall(r"(?<![\w@])@([A-Za-z_][\w-]*)", value)):
                if kw in project.colors:
                    add("warning", f"{field} shows the colour keyword @{kw} as text; keywords only work in "
                                   "colour fields", it["itemdefid"])


def _bare_key(project: Project, head: str, table_name: str, where: str, add, itemdefid=None) -> None:
    table = project.tables.get(table_name) or {}
    keys = [k for k in (table.get("rows") or {}) if _IDENTIFIER.match(k.lower())]
    columns = table.get("columns") or []
    if keys and columns:
        add("warning", f"{where} uses {{{head}}}, which is a row key of table {table_name!r} (such as {keys[0]!r}); "
                       f"use a column such as {{{head}.{columns[0]}}} for text players read", itemdefid)


def _ranges(ids: List[int]) -> str:
    out, start, prev = [], None, None
    for i in sorted(ids):
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = i
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)
