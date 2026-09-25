"""Importing Steam item definition files into a project.

Besides merging any number of files, the importer works out the structure
sisdefman manages from definitions that were written by hand:

* a series is every item tagged ``series:KEY`` that forms a run of IDs;
* an item tagged ``series:KEY`` whose description lists the names of the
  series' items is a *container* (a crate); the list becomes ``{contents}``;
* a generator whose bundle is exactly "every series item with tag T" becomes
  a generator rule, so new items are added to it automatically;
* placeholder "dummy" items after a series mark IDs it used before.

Everything detected is written to the project file, where it can be checked
and edited, and is summarised in the import report.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import steam
from .project import CONTENTS_TOKEN, Project, ProjectError

_SERIES_NAME = re.compile(r"\b((?:[A-Z][\w'&.-]*\s+)+Series)\b")


@dataclass
class Report:
    lines: List[Tuple[str, str]] = field(default_factory=list)  # (level, text)

    def info(self, text: str) -> None:
        self.lines.append(("info", text))

    def warn(self, text: str) -> None:
        self.lines.append(("warn", text))


def read_definitions(path: str) -> Tuple[Optional[int], List[dict]]:
    """Read a Steam item definition file: ``{"appid": N, "items": [...]}``
    (or a bare list of items)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ProjectError(f"{path} not found")
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}")
    appid = None
    if isinstance(data, dict):
        if "sisdefman" in data:
            raise ProjectError(f"{path} is a sisdefman project file, not a Steam item definition file")
        appid = data.get("appid")
        items = data.get("items")
    else:
        items = data
    if not isinstance(items, list):
        raise ProjectError(f"{path}: expected an \"items\" list")
    for it in items:
        if not isinstance(it, dict):
            raise ProjectError(f"{path}: every item must be an object")
        value = it.get("itemdefid")
        if isinstance(value, str) and value.strip().isdigit():
            it["itemdefid"] = int(value)
        elif not isinstance(value, int) or isinstance(value, bool):
            raise ProjectError(f"{path}: item without a numeric itemdefid: {json.dumps(it)[:80]}")
    return appid, items


def import_files(
    paths: List[str],
    project: Optional[Project] = None,
    replace: bool = False,
) -> Tuple[Project, Report]:
    report = Report()
    incoming: List[dict] = []
    origin: Dict[int, str] = {}
    appids = set()
    for path in paths:
        appid, items = read_definitions(path)
        if appid is not None:
            appids.add(appid)
        for it in items:
            i = it["itemdefid"]
            if i in origin:
                raise ProjectError(f"itemdefid {i} is defined in both {origin[i]} and {path}")
            origin[i] = path
            incoming.append(it)
        report.info(f"read {len(items)} definitions from {path}")
    if len(appids) > 1:
        raise ProjectError(f"the files are for different apps: {', '.join(map(str, sorted(appids)))}")
    appid = next(iter(appids), None)

    if project is None:
        project = Project.new(appid)
    else:
        if appid is not None and project.appid not in (None, appid):
            raise ProjectError(f"the files are for app {appid} but the project is for app {project.appid}")
        if project.appid is None:
            project.data["appid"] = appid
        existing = project.by_id()
        clashes = sorted(i for i in origin if i in existing)
        if clashes and not replace:
            shown = ", ".join(map(str, clashes[:12])) + (" ..." if len(clashes) > 12 else "")
            raise ProjectError(
                f"{len(clashes)} itemdefid(s) are already in the project ({shown}). "
                "Use --replace to overwrite them."
            )
        if clashes:
            report.warn(f"replaced {len(clashes)} existing definition(s)")
        project.items[:] = [it for it in project.items if it["itemdefid"] not in origin]
    project.items.extend(incoming)
    project.items.sort(key=lambda it: it["itemdefid"])

    tags = []
    for it in project.items:
        for key in steam.tag_values(it, "series"):
            if key not in tags:
                tags.append(key)
    for key in tags:
        if key not in project.series:
            _detect_series(project, key, report)

    _absorb_series_dummies(project, report)
    _strip_series_affixes(project, set(origin), report)
    project.normalize()
    return project, report


def _detect_series(project: Project, key: str, report: Report) -> None:
    by_id = project.by_id()
    tagged = [it for it in project.items if key in steam.tag_values(it, "series")]
    names = {it.get("name") for it in tagged if it.get("name")}

    containers = []
    for it in tagged:
        others = names - {it.get("name")}
        lines = (it.get("description") or "").split("\n")
        if sum(1 for line in lines if line.strip() in others) >= 2:
            containers.append(it)
    members = [it for it in tagged if all(it is not c for c in containers)]
    if not members:
        report.warn(f"series:{key}: no items found besides containers; not set up as a series")
        return

    member_ids = sorted(m["itemdefid"] for m in members)
    first, high = member_ids[0], member_ids[-1]
    foreign = [
        i for i in range(first, high + 1)
        if i in by_id and i not in member_ids and not project.is_dummy(by_id[i])
    ]
    if foreign:
        shown = ", ".join(map(str, foreign[:8])) + (" ..." if len(foreign) > 8 else "")
        report.warn(
            f"series:{key}: IDs {first}-{high} also contain items without the tag ({shown}); "
            "a series must be a run of consecutive IDs, so it was not set up. If a tagged item there is a "
            "crate whose description does not list the items yet, create the series with "
            f"`sisdefman series new {key} --first-id N --last-id N --container ID` instead."
        )
        return
    for other, s in project.series.items():
        if s["first_id"] <= high and first <= s["last_id"]:
            report.warn(f"series:{key}: IDs {first}-{high} overlap series {other!r}; not set up as a series")
            return

    through = high
    while project.is_dummy(by_id.get(through + 1)):
        through += 1
    blockers = [i for i in by_id if i > through and not project.is_dummy(by_id[i])]
    blockers += [s["first_id"] for s in project.series.values() if s["first_id"] > through]
    last = min(blockers) - 1 if blockers else through

    name = key
    for c in containers:
        m = _SERIES_NAME.search(c.get("description") or "")
        if m:
            name = " ".join(m.group(1).split())
            break

    config = {
        "name": name,
        "first_id": first,
        "last_id": last,
        "allocated_through": through,
        "containers": {},
        "generators": {},
    }
    project.series[key] = config
    holes = sum(1 for i in range(first, high + 1) if i not in member_ids)
    extra = f", {through - high} unused ID(s) after it" if through > high else ""
    report.info(
        f"series {key!r} ({name}): {len(members)} items at IDs {first}-{high}{extra}; "
        f"room up to ID {last}" + (f"; {holes} gap(s) inside" if holes else "")
    )
    if not blockers:
        report.warn(f"series {key!r}: nothing follows it, so it has no room to grow. "
                    f"Set the end of its ID range with `sisdefman series set {key} --last-id N`.")
    if name == key:
        report.info(f"  set its display name with `sisdefman series set {key} --name \"...\"`")
    _absorb_series_dummies(project, report, [key])

    member_set = set(member_ids)
    candidates = []
    for it in project.items:
        ids = steam.bundle_is_plain_list(it.get("bundle"))
        if it["itemdefid"] not in member_set and ids and set(ids) <= member_set:
            candidates.append((it, ids))
    # A bundle of several items that share exactly one tag is a rule. A
    # single-item bundle only counts when its tag is of the same kind as other
    # rules (e.g. the "rarity:" generator of a series with a single rare item).
    categories = set()
    for it, ids in candidates:
        rule = _find_rule(members, set(ids)) if len(ids) > 1 else None
        if rule:
            categories.add(rule.partition(":")[0])
    for it, ids in candidates:
        if len(ids) > 1:
            rule = _find_rule(members, set(ids))
        else:
            rule = _find_rule(members, set(ids), categories)
            if rule is None:
                continue
        if rule is None:
            report.warn(
                f"  generator {it['itemdefid']} ({it.get('name', '')}) lists items of {key!r} that don't "
                "share a tag; left as-is (it will not pick up new items automatically)"
            )
            continue
        config["generators"][str(it["itemdefid"])] = rule
        note = ""
        if project.generator_bundle(key, [rule]) != it["bundle"]:
            note = " (reordered to series order)"
        report.info(f"  generator {it['itemdefid']} ({it.get('name', '')}) = every item tagged {rule}{note}")

    for c in containers:
        _detect_listing(project, key, c, members, report)


def _find_rule(members: List[dict], wanted: set, categories: Optional[set] = None) -> Optional[str]:
    seen = []
    for m in members:
        if m["itemdefid"] in wanted:
            for t in steam.tag_strings(m):
                if t not in seen and (categories is None or t.partition(":")[0] in categories):
                    seen.append(t)
    for t in seen:
        if {m["itemdefid"] for m in members if steam.has_tags(m, [t])} == wanted:
            return t
    return None


def _detect_listing(project: Project, key: str, container: dict, members: List[dict], report: Report) -> None:
    cid = container["itemdefid"]
    label = f"container {cid} ({container.get('name', '')})"
    config = project.series[key]["containers"]
    desc = container.get("description") or ""
    if CONTENTS_TOKEN in desc:
        config[str(cid)] = {"exclude": []}
        report.info(f"  {label} already uses {CONTENTS_TOKEN}")
        return

    lines = desc.split("\n")
    names = {m.get("name", "") for m in members}
    # Find the paragraph (run of non-blank lines) naming the most items.
    best, start = None, None
    for i, line in enumerate(lines + [""]):
        if line.strip() and start is None:
            start = i
        elif not line.strip() and start is not None:
            hits = sum(1 for l in lines[start:i] if l.strip() in names)
            if best is None or hits > best[2]:
                best = (start, i, hits)
            start = None
    if not best or best[2] < 2:
        return
    start, end, _ = best
    block = [l.strip() for l in lines[start:end]]
    listed = set(block) & names
    unlisted = {m["itemdefid"] for m in members if m.get("name", "") not in listed}

    exclude, size = None, 0
    if unlisted:
        seen = []
        for m in members:
            if m["itemdefid"] in unlisted:
                seen += [t for t in steam.tag_strings(m) if t not in seen]
        for t in seen:
            having = {m["itemdefid"] for m in members if steam.has_tags(m, [t])}
            if having <= unlisted and len(having) > size:
                exclude, size = t, len(having)

    container["description"] = "\n".join(lines[:start] + [CONTENTS_TOKEN] + lines[end:])
    config[str(cid)] = {"exclude": [exclude] if exclude else []}
    what = f"every item except those tagged {exclude}" if exclude else "every item"
    report.info(f"  {label}: description lists {what}; the list is now generated")

    generated = project.container_listing(key, cid).split("\n")
    if generated != block:
        gone = [l for l in block if l not in generated]
        new = [l for l in generated if l not in block]
        details = [f"      - {l}" for l in gone] + [f"      + {l}" for l in new]
        if not details:
            details = ["      (same names, new order)"]
        report.warn(
            f"  {label}: its item list was out of date. The generated list differs:\n" + "\n".join(details)
        )


def _absorb_series_dummies(project: Project, report: Report, keys: Optional[List[str]] = None) -> None:
    """Dummy items inside a series' range are generated on export, so they are
    not stored; remember how far the series reaches instead."""
    for key in keys if keys is not None else list(project.series):
        s = project.series[key]
        dummies = [
            it for it in project.items
            if s["first_id"] <= it["itemdefid"] <= s["last_id"] and project.is_dummy(it)
        ]
        if not dummies:
            continue
        top = max(d["itemdefid"] for d in dummies)
        s["allocated_through"] = max(s.get("allocated_through") or 0, top)
        project.items[:] = [it for it in project.items if all(it is not d for d in dummies)]
        report.info(f"  {len(dummies)} dummy item(s) in the series' ID range will be generated on export")


def _strip_series_affixes(project: Project, imported: set, report: Report) -> None:
    """Remove series/index text a previous export added to descriptions."""
    stripped = 0
    positions = project.series_positions()
    for key in project.series:
        members = project.members(key)
        for index, m in enumerate(members, 1):
            if m["itemdefid"] not in imported:
                continue
            desc = m.get("description") or ""
            before, after = project.template_affixes(key, m, positions[m["itemdefid"]])
            if not (before or after):
                continue
            if desc == (before + after).strip():
                m["description"] = ""
                stripped += 1
            elif len(desc) >= len(before) + len(after) and desc.startswith(before) and desc.endswith(after):
                m["description"] = desc[len(before):len(desc) - len(after)]
                stripped += 1
    if stripped:
        report.info(f"removed previously generated series text from {stripped} description(s)")
