"""Consistency checks for a project."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import derive, steam
from .project import CONTENTS_TOKEN, Project, ProjectError

LEVELS = ("error", "warning", "note")


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

    for problem in derive.check_definitions(project.schema()):
        add("error", problem)
    try:
        built, problems = project.build_with_problems()
    except (ProjectError, steam.SyntaxProblem) as e:
        add("error", str(e))
        return issues
    exported = {it["itemdefid"]: it for it in built}
    managed = project.managed_generator_ids()
    containers = {cid: key for key in project.series for cid in project.container_ids(key)}

    # ------------------------------------------------------------ each item
    for i, found in problems.items():
        for problem in found:
            add("error" if problem.startswith("unknown kind") else "warning", problem, i)
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
            add("note", "generator has an empty bundle, so it grants nothing", i)

        in_series = project.series_for_id(i)
        for key in steam.tag_values(it, "series"):
            if key not in project.series:
                continue
            if in_series != key and containers.get(i) != key:
                add("warning", f"is tagged series:{key} but is outside that series' ID range", i)

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
            elif CONTENTS_TOKEN not in (c.get("description") or ""):
                add("warning", f"container of series {key!r} has no {CONTENTS_TOKEN} in its description, "
                               "so its item list is not generated", cid)
            try:
                rules = [steam.parse_tag_rule(r) for r in (cfg or {}).get("exclude", [])]
            except steam.SyntaxProblem as e:
                add("error", f"series {key!r}: container {cid}: {e}")
                rules = []
            if any(not r for r in rules):
                add("warning", f"series {key!r}: container {cid} has an empty exclude rule")

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
        if CONTENTS_TOKEN in (it.get("description") or ""):
            add("warning", f"description still contains {CONTENTS_TOKEN}; only containers configured in a "
                           "series get it filled in", it["itemdefid"])

    if project.mode == "release" and project.live_names() is None:
        add("warning", "release mode, but no live baseline is recorded. Run `sisdefman mark-live` so "
                       "changes to live items can be detected.")

    order = {lvl: n for n, lvl in enumerate(LEVELS)}
    issues.sort(key=lambda x: (order[x.level], x.itemdefid if x.itemdefid is not None else -1))
    return issues


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
