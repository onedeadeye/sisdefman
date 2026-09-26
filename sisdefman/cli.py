"""Command-line interface."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from typing import List, Optional

from . import __version__, adopt, check, derive, edits, importer, jsonfmt, ops, query, safety, steam, tableimport, ui
from .project import MODES, Project, ProjectError

DEFAULT_PROJECT = "sisdefman.json"
DEFAULT_EXPORT = "itemdefs.json"


def _project_path(args) -> str:
    return getattr(args, "project", None) or os.environ.get("SISDEFMAN_PROJECT") or DEFAULT_PROJECT


def _load(args) -> Project:
    return Project.load(_project_path(args))


def _unescape(text: str) -> str:
    """Let templates typed on the command line use \\n for a line break."""
    return text.replace("\\\\", "\x00").replace("\\n", "\n").replace("\x00", "\\")




def _mode_tag(project: Project) -> str:
    return ui.red("[release mode]") if project.mode == "release" else ui.dim("[prerelease mode]")


def _print_issues(issues: List[check.Issue], show_notes: bool = True) -> None:
    for issue in issues:
        if issue.level == "note" and not show_notes:
            continue
        colour = {"error": ui.red, "warning": ui.yellow, "note": ui.dim}[issue.level]
        print(colour(str(issue)))


def _series_index(project: Project, itemdefid: int):
    key = project.series_for_id(itemdefid)
    if key is None:
        return None, None
    ids = [m["itemdefid"] for m in project.members(key)]
    return key, (ids.index(itemdefid) + 1 if itemdefid in ids else None)


# ------------------------------------------------------------------ import


def cmd_import(args) -> int:
    path = _project_path(args)
    existing = Project.load(path) if os.path.exists(path) else None
    project, report = importer.import_files(args.files, existing, replace=args.replace)
    for level, text in report.lines:
        print(ui.yellow(text) if level == "warn" else text)
    project.save(path)
    verb = "Updated" if existing else "Created"
    print()
    print(ui.green(f"{verb} {path}: {len(project.items)} definitions, {len(project.series)} series."))
    issues = check.check_project(project)
    if issues:
        print()
        print(f"`sisdefman check` reports {len(issues)} issue(s):")
        _print_issues(issues)
    if not existing:
        print()
        print("Next: review the \"series\" section of the project file, then run `sisdefman export`.")
    return 0


# ------------------------------------------------------------------ export


def cmd_export(args) -> int:
    project = _load(args)
    out = args.output
    if os.path.abspath(out) == os.path.abspath(_project_path(args)):
        raise ProjectError("the export would overwrite the project file; choose another --output")
    issues = check.check_project(project)
    errors = [i for i in issues if i.level == "error"]
    _print_issues([i for i in issues if i.level != "note"])
    if errors:
        print(ui.red(f"Not exported: fix the {len(errors)} error(s) above first."))
        return 1
    built = project.build()
    if project.mode == "release":
        live = project.live_names()
        if live is None:
            print(ui.yellow("Release mode, but no live baseline is recorded: changes to live items cannot be "
                            "detected. Run `sisdefman mark-live` once this matches what is on Steam."))
        else:
            impacts = safety.compare(live, project, built)
            ok = safety.warn_and_confirm(
                project, impacts, args.accept_inventory_changes,
                action="Uploading this export",
                advice="Review it with `sisdefman diff`. After uploading, run `sisdefman mark-live`.",
            )
            if not ok:
                return 1
    doc = {"appid": project.appid, "items": built} if project.appid is not None else {"items": built}
    jsonfmt.write(out, doc)
    dummies = sum(1 for it in built if project.is_dummy(it))
    print(ui.green(f"Wrote {len(built)} item definitions to {out}") + (f" ({dummies} dummy)" if dummies else ""))
    changed = project.normalize()
    if args.mark_live:
        n = project.record_live()
        print(f"Recorded {n} item definitions as live.")
        changed = True
    if changed:
        project.save()
    return 0


# ------------------------------------------------------------------- check


def cmd_check(args) -> int:
    project = _load(args)
    issues = check.check_project(project)
    _print_issues(issues)
    counts = {lvl: sum(1 for i in issues if i.level == lvl) for lvl in check.LEVELS}
    summary = ", ".join(f"{n} {lvl}(s)" for lvl, n in counts.items())
    print((ui.red if counts["error"] else ui.green)(f"{summary}."))
    return 1 if counts["error"] else 0


# -------------------------------------------------------------- list/show


def cmd_list(args) -> int:
    project = _load(args)
    built = project.build()
    live = project.live_names() if project.mode == "release" else None
    if args.all:
        for it in built:
            key, index = _series_index(project, it["itemdefid"])
            where = f"{key} #{index}" if index else ("dummy" if project.is_dummy(it) else "")
            print(f"{it['itemdefid']:>8}  {it.get('type', ''):<17} {where:<14} {it.get('name', '')}")
        return 0
    keys = [args.series] if args.series else list(project.series)
    if args.series:
        project.get_series(args.series)
    for key in keys:
        s = project.series[key]
        members = project.members(key)
        next_id = (members[-1]["itemdefid"] + 1) if members else s["first_id"]
        secret = len(project.secret_ids(key))
        counts = f"{len(members)} items" + (f" ({len(members) - secret} + {secret} secret)" if secret else "")
        print(ui.bold(f"{key} - {project.series_name(key)}") +
              f"  (IDs {s['first_id']}-{s['last_id']}, {counts}, next free ID {next_id})")
        if not args.series:
            continue
        exported = {it["itemdefid"]: it for it in built}
        top = max([s.get("allocated_through") or 0] + [m["itemdefid"] for m in members])
        index = 0
        for i in range(s["first_id"], top + 1):
            it = exported.get(i)
            if it is None:
                continue
            if project.is_dummy(it):
                print(ui.dim(f"{'':>5}  {i:>7}  (dummy)"))
                continue
            index += 1
            mark = ""
            if live is not None and i in live:
                mark = ui.dim("  live") if live[i] == it.get("name") else ui.yellow(f"  live as {live[i]!r}")
            print(f"{index:>5}  {i:>7}  {it.get('name', '')}{mark}")
    others = [it for it in project.items if project.series_for_id(it["itemdefid"]) is None]
    if not args.series:
        print(f"{len(others)} other definitions (crates, generators, ...). `sisdefman list --all` shows everything.")
    return 0


def cmd_show(args) -> int:
    project = _load(args)
    if args.stored:
        it = project.item(args.itemdefid)
    else:
        it = next((x for x in project.build() if x["itemdefid"] == args.itemdefid), None)
    if it is None:
        raise ProjectError(f"no item definition {args.itemdefid}")
    key, index = _series_index(project, args.itemdefid)
    if index:
        print(ui.dim(f"// series {key} #{index}"))
    record = project.item(args.itemdefid)
    if record and record.get("kind") and not args.stored:
        kind = project.schema().kind_of(record)
        fields = derive.fields_of(kind)
        print(ui.dim(f"// kind {record['kind']}: " + ", ".join(
            f"{f}={record.get(f, '')!r}" for f in fields if record.get(f) not in (None, ""))))
        over = derive.overridden(project.schema(), record)
        if over:
            print(ui.dim(f"// overrides: {', '.join(over)}"))
        for problem in project.resolve(record)[1]:
            print(ui.yellow(f"// problem: {problem}"))
    print(json.dumps(it, indent=4, ensure_ascii=False))
    return 0


# ---------------------------------------------------------- series edits


def _new_items(args, project: Project) -> List[dict]:
    items: List[dict] = []
    if args.from_file:
        with open(args.from_file, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data = data["items"]
        items = data if isinstance(data, list) else [data]
        if not all(isinstance(x, dict) for x in items):
            raise ProjectError(f"{args.from_file} must contain an item object or a list of them")
    elif args.like is not None:
        base = project.item(args.like)
        if base is None:
            raise ProjectError(f"no item definition {args.like} to copy")
        base = copy.deepcopy(base)
        base.pop("itemdefid", None)
        items = [base]
    else:
        items = [{"type": "item"} if not args.kind else {}]
    if args.kind:
        if args.kind not in project.kinds:
            raise ProjectError(f"no kind named {args.kind!r} (known: {', '.join(project.kinds) or 'none'})")
        for it in items:
            it["kind"] = args.kind
    assignments = [query.parse_assignment(a) for a in args.assignments or []]
    assignments += [query.parse_assignment(a) for a in args.set or []]
    assignments += [query.parse_assignment(a.replace("=", ":=", 1)) for a in args.set_json or []]
    for field, value in assignments:
        if field in ("itemdefid", "kind"):
            raise ProjectError(f"{field} cannot be set this way" +
                               (" (use --kind)" if field == "kind" else "; it comes from the item's position"))
        for it in items:
            if value in ("", None):
                it.pop(field, None)
            else:
                it[field] = value
    return items


def _guarded_save(args, project: Project, protect, action: str, advice: Optional[str] = None) -> int:
    """Finish a change: in release mode warn about live items it changes and
    ask for confirmation, then save (or, with --dry-run, don't)."""
    impacts = safety.compare(protect, project, project.build())
    if project.mode == "prerelease" and impacts:
        print(ui.dim(f"  {len(impacts)} existing itemdefid(s) now hold a different item. That is fine before "
                     "release; in release mode this needs confirmation."))
    if getattr(args, "dry_run", False):
        if project.mode == "release" and impacts:
            print(ui.red(f"This would change {len(impacts)} item(s) players own:"))
            for imp in impacts:
                print("    " + imp.describe())
        print(ui.yellow("Dry run: nothing saved."))
        return 0
    if not safety.warn_and_confirm(project, impacts, args.accept_inventory_changes, action=action, advice=advice):
        return 1
    project.save()
    print(ui.green(f"Saved {project.path}."))
    return 0


def _run_series_op(args, project: Project, action: str, advice: Optional[str], op) -> int:
    print(_mode_tag(project))
    built_before = project.build()
    protect = safety.protected_before(project, built_before)
    names_before = {it["itemdefid"]: it.get("name", "") for it in built_before}
    result: ops.OpResult = op()

    names = {it["itemdefid"]: it.get("name", "") for it in project.build()}
    for i in result.added:
        key, index = _series_index(project, i)
        print(f"+ {names.get(i, '')!r} becomes {key} #{index}, itemdefid {i}")
    for i, _ in result.removed:
        print(f"- {names_before.get(i, '')!r} (itemdefid {i}) leaves its series")
    for note in result.notes:
        print(ui.dim(f"  note: {note}"))
    if result.moved:
        print(f"Renumbered {len(result.moved)} item(s): {', '.join(ops.shift_summary(result.moved))}")
    for i, fields in result.rewritten:
        print(ui.dim(f"  updated references in {i} ({names.get(i, '')}): {', '.join(fields)}"))
    return _guarded_save(args, project, protect, action, advice)


def cmd_add(args) -> int:
    project = _load(args)
    items = _new_items(args, project)
    built = {it["itemdefid"]: it for it in project.build()}
    names = {built[m["itemdefid"]].get("name") for m in project.members(args.series)}
    for it in items:
        name = project.resolve(dict(it, itemdefid=0))[0].get("name")
        if name in names:
            print(ui.yellow(f"warning: series {args.series!r} already has an item named {name!r}"))
    return _run_series_op(
        args, project, "Inserting this item",
        "To avoid it, add the item to the end of the series instead (leave out --position/--before/--after).",
        lambda: ops.insert_items(project, args.series, items, args.position, args.before, args.after,
                                 skip_ids=project.retired_ids() if project.mode == "release" else frozenset()),
    )


def cmd_remove(args) -> int:
    project = _load(args)
    advice = None if args.no_shift else (
        "To leave the other items' IDs alone, use --no-shift (only the removed item's ID changes).")
    return _run_series_op(args, project, "Removing this item", advice,
                          lambda: ops.remove_item(project, args.itemdefid, shift=not args.no_shift))


def cmd_move(args) -> int:
    project = _load(args)
    return _run_series_op(args, project, "Moving this item", None,
                          lambda: ops.move_item(project, args.itemdefid, args.position, args.before, args.after))


# ------------------------------------------------------------------ series


def cmd_series(args) -> int:
    project = _load(args)
    action = args.series_action or "list"
    if action == "list":
        if not project.series:
            print("No series. Create one with `sisdefman series new`.")
        for key, s in project.series.items():
            members = project.members(key)
            print(ui.bold(f"{key}") + f"  {project.series_name(key)!r}")
            print(f"    IDs {s['first_id']}-{s['last_id']}, {len(members)} items, "
                  f"used through {s.get('allocated_through')}")
            if s.get("description_template"):
                print(f"    description template: {s['description_template']!r}")
            for cid, cfg in s["containers"].items():
                exclude = ", ".join((cfg or {}).get("exclude", [])) or "nothing"
                print(f"    container {cid}: lists items, excluding {exclude}")
            for gid, rule in s["generators"].items():
                print(f"    generator {gid}: every item tagged {rule}")
            secret = project.secret_ids(key)
            rules = " or ".join(";".join(r) for r in project.secret_rules(key)) or "none"
            source = "" if "secret" in s else " (from the containers)"
            print(f"    secret rares: items tagged {rules}{source}: {len(secret)} of {len(members)}")
        return 0

    config = {"name": getattr(args, "name", None)}
    if action == "new":
        config.update(first_id=args.first_id, last_id=args.last_id)
    elif args.last_id is not None:
        config["last_id"] = args.last_id
    s = project.series.get(args.key, {"containers": {}, "generators": {}})
    if args.template is not None:
        config["description_template"] = _unescape(args.template)
    if args.secret is not None:
        config["secret"] = args.secret
    if args.exclude and not args.container:
        raise ProjectError("--exclude applies to the containers given with --container")
    if args.container or args.remove:
        containers = dict(s["containers"])
        containers.update({str(cid): {"exclude": list(args.exclude or [])} for cid in args.container or []})
        config["containers"] = {k: v for k, v in containers.items() if int(k) not in (args.remove or [])}
    if args.generator or args.remove:
        generators = dict(s["generators"])
        for spec in args.generator or []:
            gid, sep, rule = spec.partition("=")
            if not sep or not gid.strip().isdigit() or not rule.strip():
                raise ProjectError(f"--generator expects ITEMDEFID=TAGS, got {spec!r}")
            generators[str(int(gid))] = rule.strip()
        config["generators"] = {k: v for k, v in generators.items() if int(k) not in (args.remove or [])}
    for note in edits.save_series(project, args.key, config, is_new=(action == "new")):
        print(ui.yellow(note) if "{contents}" in note else note)
    project.save()
    print(ui.green(f"Saved series {args.key!r}."))
    return 0


def cmd_template(args) -> int:
    project = _load(args)
    if args.template is not None:
        project.settings["description_template"] = _unescape(args.template)
        project.build()  # validates the template
        project.save()
        print(ui.green("Saved description template."))
    print(f"Description template: {project.settings['description_template']!r}")
    for key in project.series:
        members = project.members(key)
        if members:
            print(ui.dim(f"Example ({key} #1, itemdefid {members[0]['itemdefid']}):"))
            print(project.render_description(key, project.resolve(members[0])[0],
                                             project.series_positions()[members[0]["itemdefid"]], members[0]))
            break
    return 0


# ------------------------------------------------------------ mode / live


def cmd_mode(args) -> int:
    project = _load(args)
    if args.mode is None:
        print(f"Mode: {ui.bold(project.mode)}")
        live = project.live
        if live and live.get("items") is not None:
            print(f"Live baseline: {len(live['items'])} items, recorded {live.get('recorded_at')}")
        return 0
    if args.mode == project.mode:
        print(f"Already in {args.mode} mode.")
        return 0
    if args.mode == "release":
        issues = [i for i in check.check_project(project) if i.level == "error"]
        if issues:
            _print_issues(issues)
            raise ProjectError("fix the errors above before switching to release mode")
        project.mode = "release"
        n = project.record_live()
        project.save()
        print(ui.green(f"Release mode on. Recorded {n} item definitions as live."))
        print("The record should match what is on Steam: export and upload now if you have unpublished changes.\n"
              "From now on, changes that alter an item players may own need explicit confirmation.\n"
              "After each upload, run `sisdefman mark-live`.")
        return 0
    print(ui.banner("Leaving release mode turns off inventory protection"))
    print("\nIn prerelease mode sisdefman no longer asks before renumbering, removing or replacing\n"
          "items that players may already own. Only do this if nobody owns items yet.\n")
    if not ui.confirm_phrase(args.accept_inventory_changes):
        return 1
    project.mode = "prerelease"
    project.save()
    print(ui.green("Prerelease mode on. The live baseline is kept for when you switch back."))
    return 0


def cmd_mark_live(args) -> int:
    project = _load(args)
    old = project.live_names()
    if old is not None:
        impacts = safety.compare(old, project, project.build())
        if impacts:
            print(ui.yellow(f"{len(impacts)} live itemdefid(s) now hold a different item; recording that as live:"))
            for imp in impacts:
                print("    " + imp.describe())
    n = project.record_live()
    project.save()
    print(ui.green(f"Recorded {n} item definitions as live."))
    if project.mode == "prerelease":
        print(ui.dim("(Only used in release mode.)"))
    return 0


def cmd_diff(args) -> int:
    project = _load(args)
    built = project.build()
    new = {it["itemdefid"]: it for it in built}
    if not args.against:
        live = project.live_names()
        if live is None:
            raise ProjectError("no live baseline recorded; compare with a file using --against FILE")
        impacts = safety.compare(live, project, built)
        added = sorted(set(project.owned_names(built)) - set(live))
        print(f"Compared with the live baseline recorded {project.live.get('recorded_at')}:")
        for imp in impacts:
            print(ui.red("  ! " + imp.describe()))
        for i in added:
            print(ui.green(f"  + #{i}  {new[i].get('name', '')!r}"))
        if not impacts and not added:
            print("  no changes to what players can own")
        return 0

    old = {}
    for path in args.against:
        _, items = importer.read_definitions(path)
        old.update({it["itemdefid"]: it for it in items})
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = [i for i in sorted(set(new) & set(old)) if new[i] != old[i]]
    for i in added:
        print(ui.green(f"+ {i}  {new[i].get('name', '')}"))
    for i in removed:
        print(ui.red(f"- {i}  {old[i].get('name', '')}  (not in the export; Steam keeps it as it is)"))
    for i in changed:
        fields = [k for k in sorted(set(new[i]) | set(old[i])) if new[i].get(k) != old[i].get(k)]
        print(ui.yellow(f"~ {i}  {new[i].get('name', '')}: {', '.join(fields)}"))
        if args.verbose:
            for k in fields:
                print(f"      {k}:")
                print(ui.red(f"        - {json.dumps(old[i].get(k), ensure_ascii=False)}"))
                print(ui.green(f"        + {json.dumps(new[i].get(k), ensure_ascii=False)}"))
    print(f"{len(added)} added, {len(removed)} only in the old file, {len(changed)} changed, "
          f"{len(set(new) & set(old)) - len(changed)} unchanged.")
    return 0


# ---------------------------------------------------------------- database

DEFAULT_QUERY_FIELDS = ("id", "series", "index", "kind", "name")


def _targets(args, project: Project, tokens: List[str]) -> List[int]:
    ids, rest = query.split_targets(tokens)
    if rest:
        raise ProjectError(f"expected itemdefids, got {' '.join(rest)}")
    if getattr(args, "where", None):
        ids += [v["id"] for v in query.select(project, args.where)]
    seen = set()
    ids = [i for i in ids if not (i in seen or seen.add(i))]
    if not ids:
        raise ProjectError("no items selected (give itemdefids such as 110 or 110-134, or --where CONDITION)")
    return ids


def _cell(value) -> str:
    text = query._text(value).replace("\n", " / ")
    return text if len(text) <= 60 else text[:57] + "..."


def cmd_query(args) -> int:
    project = _load(args)
    views = query.select(project, args.where or [], include_dummies=args.dummies)
    fields = [f.strip() for f in args.fields.split(",")] if args.fields else list(DEFAULT_QUERY_FIELDS)
    if args.format == "json":
        rows = [{f: v.get(f) for f in fields} for v in views] if args.fields else views
        print(json.dumps(rows, indent=4, ensure_ascii=False))
        return 0
    if args.format == "csv":
        import csv
        writer = csv.writer(sys.stdout)
        writer.writerow(fields)
        for v in views:
            writer.writerow([query._text(v.get(f)) for f in fields])
        return 0
    table = [fields] + [[_cell(v.get(f)) for f in fields] for v in views]
    widths = [max(len(row[n]) for row in table) for n in range(len(fields))]
    for n, row in enumerate(table):
        line = "  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip()
        print(ui.bold(line) if n == 0 else line)
    print(ui.dim(f"{len(views)} item(s)"))
    return 0


def cmd_set(args) -> int:
    project = _load(args)
    ids, rest = query.split_targets(args.items)
    assignments = [query.parse_assignment(a) for a in rest]
    if not assignments and not args.unset:
        raise ProjectError("nothing to change: give FIELD=VALUE, FIELD:=JSON or --unset FIELD")
    ids = _targets(args, project, [str(i) for i in ids])
    print(_mode_tag(project))
    protect = safety.protected_before(project, project.build())
    for note in edits.set_fields(project, ids, assignments, args.unset or []):
        print(ui.dim(f"  note: {note}"))
    print(f"Updated {len(ids)} item(s).")
    return _guarded_save(args, project, protect, "This change")


def cmd_adopt(args) -> int:
    project = _load(args)
    ids = _targets(args, project, args.items)
    print(_mode_tag(project))
    protect = safety.protected_before(project, project.build())
    result = adopt.adopt(project, args.kind, ids)
    print(ui.green(f"{len(result.adopted)} item(s) now use kind {args.kind!r}."))
    for i, fields in sorted(result.overrides.items()):
        print(ui.yellow(f"  {i}: kept {', '.join(fields)} as override(s); the rule gives something else"))
    for i, reason in sorted(result.skipped.items()):
        print(ui.yellow(f"  {i}: not converted: {reason}"))
    if result.learned:
        print(f"Filled in {len(result.learned)} table value(s)" + (":" if args.verbose else " (-v lists them)."))
        if args.verbose:
            for line in result.learned:
                print(ui.dim(f"  {line}"))
    if result.side_effects:
        print(ui.yellow(f"The new table values also change item(s) {', '.join(map(str, result.side_effects))}."))
    if not result.adopted:
        return 1
    return _guarded_save(args, project, protect, "Adopting these items")


def cmd_detach(args) -> int:
    project = _load(args)
    ids = _targets(args, project, args.items)
    protect = safety.protected_before(project, project.build())
    changed = adopt.detach(project, ids)
    print(f"{len(changed)} item(s) are now plain definitions.")
    return _guarded_save(args, project, protect, "Detaching these items")


def cmd_schema(args) -> int:
    project = _load(args)
    action = args.schema_action or "show"
    if action == "export":
        text = jsonfmt.dumps(edits.export_schema(project))
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            print(ui.green(f"Wrote {args.output}."))
        else:
            sys.stdout.write(text)
        return 0
    if action == "import":
        with open(args.file, encoding="utf-8-sig") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ProjectError(f"{args.file}: invalid JSON at line {e.lineno}: {e.msg}")
        protect = safety.protected_before(project, project.build())
        for note in edits.import_schema(project, data):
            print(note)
        return _guarded_save(args, project, protect, "This schema change")
    usage = {}
    for rec in project.items:
        if rec.get("kind"):
            usage[rec["kind"]] = usage.get(rec["kind"], 0) + 1
    if not project.tables and not project.kinds:
        print("No tables or kinds yet. See `sisdefman schema import --help`, or use `sisdefman gui`.")
    for name, table in project.tables.items():
        print(ui.bold(f"table {name}") + f"  columns: {', '.join(table.get('columns', [])) or '-'}; "
              f"{len(table.get('rows', {}))} row(s)")
    for name, kind in project.kinds.items():
        print(ui.bold(f"kind {name}") + f"  ({usage.get(name, 0)} item(s))")
        for field, spec in derive.fields_of(kind).items():
            extra = f" -> table {spec.get('table')}" if spec.get("type") == "ref" else ""
            opt = ", optional" if spec.get("optional") else ""
            print(f"    field {field}: {spec.get('type', 'text')}{extra}{opt}")
        for field, rule in derive.rules_of(kind).items():
            print(f"    {field} = {json.dumps(rule, ensure_ascii=False)}")
    return 0


def cmd_table(args) -> int:
    project = _load(args)
    action = args.table_action or "list"
    if action == "list":
        for name, table in project.tables.items():
            print(f"{name}: {len(table.get('rows', {}))} row(s); columns {', '.join(table.get('columns', []))}")
        if not project.tables:
            print("No tables.")
        return 0
    if action == "show":
        table = edits._table(project, args.name)
        used = edits.rows_in_use(project, args.name)
        columns = [c.strip() for c in args.columns.split(",")] if args.columns else table["columns"]
        rows = [["key"] + columns + ["items"]]
        for key, row in table["rows"].items():
            rows.append([key] + [_cell(row.get(c, "")) for c in columns] + [str(len(used.get(key, [])))])
        widths = [max(len(r[n]) for r in rows) for n in range(len(rows[0]))]
        for n, r in enumerate(rows):
            line = "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
            print(ui.bold(line) if n == 0 else line)
        return 0
    protect = safety.protected_before(project, project.build())
    if action == "new":
        edits.create_table(project, args.name, args.columns)
        print(f"Created table {args.name!r}.")
    elif action == "set":
        values = dict(query.parse_assignment(a) for a in args.values)
        for note in edits.set_row(project, args.name, args.key, values):
            print(ui.dim(f"  note: {note}"))
        print(f"Saved row {args.key!r}.")
    elif action == "delete":
        edits.delete_row(project, args.name, args.key, force=args.force)
        print(f"Deleted row {args.key!r}.")
    elif action == "rename":
        n = edits.rename_row(project, args.name, args.old, args.new)
        print(f"Renamed {args.old!r} to {args.new!r}; updated {n} item(s).")
    elif action == "import":
        with open(args.file, encoding="utf-8-sig", newline="") as f:
            data = tableimport.read_csv(f.read())
        split = lambda text: [c.strip() for c in text.split(",") if c.strip()] if text else None  # noqa: E731
        rename, fills = {}, []
        for spec in args.map or []:
            source, sep, target = spec.partition("=")
            if not sep:
                raise ProjectError(f"--map expects CSVCOLUMN=COLUMN, got {spec!r}")
            rename[source] = target
        for spec in args.fill or []:
            target, sep, source = spec.partition("=")
            if not sep:
                raise ProjectError(f"--fill expects COLUMN=CSVCOLUMN, got {spec!r}")
            fills.append((target, source))
        report = tableimport.import_csv(project, args.name, data, key_column=args.key, only=split(args.only),
                                        skip=split(args.skip) or [], rename=rename, fills=fills,
                                        key_case=args.key_case, raw=args.raw)
        for line in report.lines(args.verbose):
            print(ui.yellow(line) if "differ from the file" in line else line)
    return _guarded_save(args, project, protect, "This table change")


def gui_project(args) -> Optional[str]:
    """The project the GUI opens with: the one given with -p or
    $SISDEFMAN_PROJECT, else sisdefman.json in this folder if there is one,
    else none (the page asks for one)."""
    if args.choose:
        return None
    explicit = getattr(args, "project", None) or os.environ.get("SISDEFMAN_PROJECT")
    if explicit:
        return explicit
    return DEFAULT_PROJECT if os.path.exists(DEFAULT_PROJECT) else None


def cmd_gui(args) -> int:
    from . import gui
    return gui.serve(gui_project(args), host=args.host, port=args.port, open_browser=not args.no_browser)


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--project", default=argparse.SUPPRESS,
                        help=f"project file (default: $SISDEFMAN_PROJECT or {DEFAULT_PROJECT})")

    parser = argparse.ArgumentParser(
        prog="sisdefman",
        description="Manage Steam Inventory Service item definitions in a single project file.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"sisdefman {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def command(name: str, help_text: str, **kw) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, description=help_text, parents=[common], **kw)

    def guarded(p: argparse.ArgumentParser, dry_run: bool = True) -> None:
        p.add_argument(ui.ACCEPT_FLAG, dest="accept_inventory_changes", action="store_true",
                       help="in release mode, go ahead without the typed confirmation")
        if dry_run:
            p.add_argument("-n", "--dry-run", action="store_true", help="show what would happen; save nothing")

    def where(p: argparse.ArgumentParser, required: bool = False) -> None:
        g = p.add_mutually_exclusive_group(required=required)
        g.add_argument("--position", type=int, metavar="N", help="1-based position in the series")
        g.add_argument("--before", type=int, metavar="ID", help="put it before this itemdefid")
        g.add_argument("--after", type=int, metavar="ID", help="put it after this itemdefid")

    p = command("import", "Create the project from Steam item definition files, or add files to it.")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--replace", action="store_true", help="overwrite definitions already in the project")
    p.set_defaults(func=cmd_import)

    p = command("export", "Write every item definition to one Steam-ready JSON file.")
    p.add_argument("-o", "--output", default=DEFAULT_EXPORT, help=f"output file (default: {DEFAULT_EXPORT})")
    p.add_argument("--mark-live", action="store_true",
                   help="also record the export as live (use when you are about to upload it)")
    guarded(p, dry_run=False)
    p.set_defaults(func=cmd_export)

    p = command("check", "Validate the project.")
    p.set_defaults(func=cmd_check)

    p = command("list", "List series, or the items of one series.")
    p.add_argument("series", nargs="?")
    p.add_argument("--all", action="store_true", help="list every definition")
    p.set_defaults(func=cmd_list)

    p = command("show", "Print one definition as it will be exported.")
    p.add_argument("itemdefid", type=int)
    p.add_argument("--stored", action="store_true", help="show it as stored in the project instead")
    p.set_defaults(func=cmd_show)

    p = command("add", "Add an item to a series (at the end unless a position is given).")
    p.add_argument("series")
    where(p)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--from", dest="from_file", metavar="FILE",
                     help="JSON file with the item (or a list of items) to add")
    src.add_argument("--like", type=int, metavar="ID", help="start from a copy of this definition")
    p.add_argument("assignments", nargs="*", metavar="FIELD=VALUE",
                   help="fields of the new item; FIELD:=JSON for true, numbers, etc.")
    p.add_argument("--kind", help="make it an item of this kind (its other fields are derived)")
    p.add_argument("--set", action="append", metavar="FIELD=VALUE", help=argparse.SUPPRESS)
    p.add_argument("--set-json", action="append", metavar="FIELD=JSON", help=argparse.SUPPRESS)
    guarded(p)
    p.set_defaults(func=cmd_add, extra_dest="assignments")

    p = command("remove", "Remove an item from its series.")
    p.add_argument("itemdefid", type=int)
    p.add_argument("--no-shift", action="store_true",
                   help="leave a gap (exported as a dummy item) instead of moving later items down")
    guarded(p)
    p.set_defaults(func=cmd_remove)

    p = command("move", "Move an item to another position in its series.")
    p.add_argument("itemdefid", type=int)
    where(p, required=True)
    guarded(p)
    p.set_defaults(func=cmd_move)

    p = command("series", "Show or configure series.")
    p.set_defaults(func=cmd_series, series_action=None)
    ssub = p.add_subparsers(dest="series_action", metavar="ACTION")
    for name in ("new", "set"):
        help_text = "Create a series." if name == "new" else "Change a series."
        sp = ssub.add_parser(name, help=help_text, description=help_text, parents=[common])
        sp.add_argument("key", help="series key, as used in the series: tag")
        sp.add_argument("--name", help="display name used in descriptions")
        if name == "new":
            sp.add_argument("--first-id", type=int, required=True)
            sp.add_argument("--last-id", type=int, required=True, help="last itemdefid the series may use")
        else:
            sp.add_argument("--last-id", type=int, help="last itemdefid the series may use")
        sp.add_argument("--template", help="description template for this series (\"\" = use the global one)")
        sp.add_argument("--secret", metavar="TAGS",
                        help="tags that mark the series' secret rares, e.g. rarity:epic "
                             "(\"\" = the tags its containers leave out)")
        sp.add_argument("--container", type=int, action="append", metavar="ID",
                        help="definition whose description lists the series' items at {contents}")
        sp.add_argument("--exclude", action="append", metavar="TAG",
                        help="with --container: leave items with this tag out of the list")
        sp.add_argument("--generator", action="append", metavar="ID=TAGS",
                        help="generator whose bundle is every series item with these tags")
        sp.add_argument("--remove", type=int, action="append", metavar="ID",
                        help="stop managing this container or generator")
        sp.set_defaults(func=cmd_series)
    ssub.add_parser("list", help="Show all series.", parents=[common]).set_defaults(func=cmd_series)

    p = command("template", "Show or set the description template used for series items.")
    p.add_argument("template", nargs="?", help="new template; \\n is a line break")
    p.set_defaults(func=cmd_template)

    p = command("mode", "Show or switch between prerelease and release mode.")
    p.add_argument("mode", nargs="?", choices=MODES)
    p.add_argument(ui.ACCEPT_FLAG, dest="accept_inventory_changes", action="store_true",
                   help="skip the typed confirmation when leaving release mode")
    p.set_defaults(func=cmd_mode)

    p = command("mark-live", "Record the current definitions as what is live on Steam (after uploading).")
    p.set_defaults(func=cmd_mark_live)

    p = command("query", "List item definitions that match conditions, like a database query.")
    p.add_argument("-w", "--where", action="append", metavar="COND",
                   help="e.g. rarity=epic, name~rifle, id>=200, flavor, !flavor (repeatable; all must match)")
    p.add_argument("-f", "--fields", help=f"comma-separated fields to show (default: {','.join(DEFAULT_QUERY_FIELDS)})")
    p.add_argument("--format", choices=("table", "json", "csv"), default="table")
    p.add_argument("--dummies", action="store_true", help="include generated dummy items")
    p.set_defaults(func=cmd_query)

    p = command("set", "Change fields of items: kind fields, overrides of derived fields or Steam fields.")
    p.add_argument("items", nargs="*", metavar="ID|FIELD=VALUE",
                   help="itemdefids (110, 110-134) followed by FIELD=VALUE or FIELD:=JSON")
    p.add_argument("-w", "--where", action="append", metavar="COND", help="select items by condition")
    p.add_argument("--unset", action="append", metavar="FIELD",
                   help="remove a stored value (for a derived field: go back to the rule)")
    guarded(p)
    p.set_defaults(func=cmd_set, extra_dest="items")

    p = command("adopt", "Convert items to a kind, working out its fields from their current definitions.")
    p.add_argument("kind")
    p.add_argument("items", nargs="*", metavar="ID", help="itemdefids (110, 110-134)")
    p.add_argument("-w", "--where", action="append", metavar="COND", help="select items by condition")
    p.add_argument("-v", "--verbose", action="store_true", help="list the table values filled in")
    guarded(p)
    p.set_defaults(func=cmd_adopt, extra_dest="items")

    p = command("detach", "Turn kind items back into plain definitions with their current fields.")
    p.add_argument("items", nargs="*", metavar="ID")
    p.add_argument("-w", "--where", action="append", metavar="COND")
    guarded(p)
    p.set_defaults(func=cmd_detach, extra_dest="items")

    p = command("schema", "Show, export or import the lookup tables and item kinds.")
    p.set_defaults(func=cmd_schema, schema_action=None)
    ssub = p.add_subparsers(dest="schema_action", metavar="ACTION")
    sp = ssub.add_parser("show", help="summarise tables and kinds", parents=[common])
    sp.set_defaults(func=cmd_schema)
    sp = ssub.add_parser("export", help="write tables and kinds as JSON", parents=[common])
    sp.add_argument("-o", "--output", help="file to write (default: print)")
    sp.set_defaults(func=cmd_schema)
    sp = ssub.add_parser("import", help="merge tables and kinds from a JSON file", parents=[common])
    sp.add_argument("file")
    guarded(sp)
    sp.set_defaults(func=cmd_schema)

    p = command("table", "Show or edit lookup tables.")
    p.set_defaults(func=cmd_table, table_action=None)
    tsub = p.add_subparsers(dest="table_action", metavar="ACTION")
    tsub.add_parser("list", help="list tables", parents=[common]).set_defaults(func=cmd_table)
    sp = tsub.add_parser("show", help="show a table's rows", parents=[common])
    sp.add_argument("name")
    sp.add_argument("-c", "--columns", help="comma-separated columns to show (default: all)")
    sp.set_defaults(func=cmd_table)
    sp = tsub.add_parser("new", help="create a table", parents=[common])
    sp.add_argument("name")
    sp.add_argument("columns", nargs="*")
    guarded(sp)
    sp.set_defaults(func=cmd_table)
    sp = tsub.add_parser("set", help="add or change a row: table set weapon rifle name=Rifle", parents=[common])
    sp.add_argument("name")
    sp.add_argument("key")
    sp.add_argument("values", nargs="*", metavar="COLUMN=VALUE")
    guarded(sp)
    sp.set_defaults(func=cmd_table, extra_dest="values")
    sp = tsub.add_parser("delete", help="delete a row", parents=[common])
    sp.add_argument("name")
    sp.add_argument("key")
    sp.add_argument("--force", action="store_true", help="even if items use it")
    guarded(sp)
    sp.set_defaults(func=cmd_table)
    sp = tsub.add_parser("import", parents=[common],
                         help="add a CSV file (e.g. an Unreal DataTable export) to a table, for reference",
                         description="Add the rows of a CSV file to a table. Unreal Engine notation (NSLOCTEXT, "
                                     "row handles, asset paths) is cleaned up and keys match existing rows "
                                     "ignoring case. Each CSV column becomes a table column of the same name.")
    sp.add_argument("name", help="table to add to (created if needed)")
    sp.add_argument("file", help="CSV file; the first row names the columns")
    sp.add_argument("--key", metavar="COLUMN", help="column holding the row keys (default: the first)")
    sp.add_argument("--only", metavar="COLS", help="comma-separated CSV columns to import")
    sp.add_argument("--skip", metavar="COLS", help="comma-separated CSV columns to leave out")
    sp.add_argument("--map", action="append", metavar="CSVCOL=COLUMN", help="store a CSV column under another name")
    sp.add_argument("--fill", action="append", metavar="COLUMN=CSVCOL",
                    help="also copy a CSV column into COLUMN where it is empty, and report where they differ")
    sp.add_argument("--key-case", choices=tableimport.KEY_CASES, default="auto",
                    help="case of new keys: auto (lower case if the table's keys are), lower or keep")
    sp.add_argument("--raw", action="store_true", help="keep values exactly as in the file")
    sp.add_argument("-v", "--verbose", action="store_true", help="list every replaced value")
    guarded(sp)
    sp.set_defaults(func=cmd_table)
    sp = tsub.add_parser("rename", help="rename a row key and every reference to it", parents=[common])
    sp.add_argument("name")
    sp.add_argument("old")
    sp.add_argument("new")
    guarded(sp)
    sp.set_defaults(func=cmd_table)

    p = command("gui", "Open the graphical editor in your browser. Without a project in this folder "
                       "(or with --choose) it starts with a project chooser.")
    p.add_argument("--choose", action="store_true",
                   help="start with the project chooser even if this folder has a project")
    p.add_argument("--port", type=int, default=0, help="port to listen on (default: any free port)")
    p.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    p.add_argument("--no-browser", action="store_true", help="only print the address")
    p.set_defaults(func=cmd_gui)

    p = command("diff", "Compare the export with the live baseline or with Steam item definition files.")
    p.add_argument("--against", action="append", metavar="FILE", help="compare with this file (repeatable)")
    p.add_argument("-v", "--verbose", action="store_true", help="show changed values")
    p.set_defaults(func=cmd_diff)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        # argparse cannot take positionals after options (add crate1 --kind skin weapon=x);
        # commands that accept FIELD=VALUE lists collect the leftovers.
        dest = getattr(args, "extra_dest", None)
        if dest and not any(e.startswith("-") for e in extra):
            getattr(args, dest).extend(extra)
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (ProjectError, steam.SyntaxProblem) as e:
        print(ui.red(f"error: {e}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except BrokenPipeError:  # output piped into e.g. `head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
