"""``sisdefman gui``: a local web app for editing the project.

The server only listens on 127.0.0.1. Every API call must carry the token
embedded in the page, and the Host header must name this server, so other
web pages cannot drive it. Each change reloads the project file (so edits
made with the command line or a text editor are picked up), applies the
change and saves. In release mode a change that alters a live item is
answered with 409 and the list of affected items; the page shows the release
warning and repeats the request with ``confirm`` once the user types the
confirmation phrase.

The server can start without a project; the page then shows a chooser
(recent projects, a folder browser, creating a project) and the user can
switch projects at any time.
"""

from __future__ import annotations

import copy
import json
import mimetypes
import os
import secrets
import tempfile
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

from . import __version__, adopt, check, colors, derive, edits, importer, jsonfmt, launcher, ops, safety, steam, tableimport, ui
from .project import DEFAULT_DUMMY_ITEM, MODES, Project, ProjectError

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
UNDO_LIMIT = 100

STEAM_FIELDS = [
    "type", "name", "display_type", "description", "name_color", "background_color", "icon_url",
    "icon_url_large", "tradable", "marketable", "tags", "bundle", "exchange", "tag_generators",
    "tag_generator_name", "tag_generator_values", "price", "price_category", "promo", "drop_start_time",
    "drop_interval", "use_drop_window", "drop_window", "drop_max_per_window", "use_drop_limit",
    "drop_limit", "granted_manually", "use_bundle_price", "auto_stack", "hidden", "store_hidden",
    "store_tags", "store_images", "game_only", "purchase_limit", "item_slot", "accessory_tag",
]


class NeedsConfirmation(Exception):
    def __init__(self, action: str, impacts: List[safety.Impact], message: str = ""):
        super().__init__(action)
        self.action = action
        self.impacts = impacts
        self.message = message


def _impact_json(imp: safety.Impact) -> dict:
    return {"itemdefid": imp.itemdefid, "was": imp.was, "now": imp.now, "kind": imp.kind, "text": imp.describe()}


class App:
    def __init__(self, path: Optional[str] = None):
        self.path = os.path.abspath(path) if path else None
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(24)
        self.undo_stack: List[Tuple[str, str]] = []
        self.shutdown: Optional[Callable[[], None]] = None  # set by make_server

    # ------------------------------------------------------------ plumbing

    def load(self) -> Project:
        if self.path is None:
            raise ProjectError("no project is open")
        return Project.load(self.path)

    def open_project(self, path: str) -> dict:
        with self.lock:
            path = launcher.check_project(path)
            self.path = path
            self.undo_stack = []
            launcher.remember(path)
            return {"path": path}

    def close_project(self) -> dict:
        with self.lock:
            self.path = None
            self.undo_stack = []
            return {}

    def mutate(self, action: str, fn: Callable[[Project], object], confirm: bool = False,
               record_undo: bool = True):
        with self.lock:
            project = self.load()
            before = project.to_json()
            protect = safety.protected_before(project, project.build())
            result = fn(project)
            if project.mode == "release":
                impacts = safety.compare(protect, project, project.build())
                if impacts and not confirm:
                    raise NeedsConfirmation(action, impacts)
            project.save()
            if record_undo and project.to_json() != before:
                self.undo_stack.append((action, before))
                del self.undo_stack[:-UNDO_LIMIT]
            return result

    # --------------------------------------------------------------- state

    def state(self) -> dict:
        with self.lock:
            if self.path is None:
                return {"open": False, "version": __version__, "recent": launcher.recent_projects(),
                        "cwd": os.getcwd()}
            project = self.load()
        built, problems = project.build_with_problems()
        records = project.by_id()
        positions = project.series_positions()
        schema = project.schema()
        live = project.live_names() if project.mode == "release" else None
        items = []
        for it in built:
            i = it["itemdefid"]
            rec = records.get(i)
            pos = positions.get(i)
            items.append({
                "id": i,
                "record": rec,
                "item": it,
                "series": pos.key if pos else (project.series_for_id(i) if rec is None else None),
                "index": pos.index if pos else None,
                "dummy": rec is None,
                "overrides": derive.overridden(schema, rec) if rec else [],
                "problems": problems.get(i, []),
                "live": live.get(i) if live else None,
            })
        series = {}
        for key, s in project.series.items():
            members = [m["itemdefid"] for m in project.members(key)]
            series[key] = dict(copy.deepcopy(s), display_name=project.series_name(key),
                               named=project.has_display_name(key), members=members,
                               next_id=(members[-1] + 1) if members else s["first_id"],
                               secret_ids=project.secret_ids(key),
                               secret_rules=[";".join(r) for r in project.secret_rules(key)])
        live_info = project.live
        return {
            "open": True,
            "version": __version__,
            "path": self.path,
            "file": os.path.basename(self.path),
            "appid": project.appid,
            "mode": project.mode,
            "settings": project.settings,
            "tables": project.tables,
            "kinds": project.kinds,
            "colors": project.colors,
            "color_usage": {kw: len(u) for kw, u in colors.usages(project).items()},
            "hex_colors_left": sum(1 for _ in colors._convertible(project)),
            "new_series": edits.suggest_series(project),
            "series": series,
            "items": items,
            "issues": [{"level": x.level, "text": x.text, "itemdefid": x.itemdefid}
                       for x in check.check_project(project)],
            "live": ({"recorded_at": live_info.get("recorded_at"), "count": len(live_info.get("items") or {})}
                     if isinstance(live_info, dict) else None),
            "undo": [label for label, _ in self.undo_stack][-10:],
            "field_types": list(derive.FIELD_TYPES),
            "steam_fields": STEAM_FIELDS,
            "default_dummy": DEFAULT_DUMMY_ITEM,
            "export_path": os.path.join(os.path.dirname(self.path), "itemdefs.json"),
        }

    def preview(self, body: dict) -> dict:
        project = self.load()
        draft = body.get("kind_draft")
        if isinstance(draft, dict):
            # Preview an unsaved kind: swap it in, in memory only.
            name, old = draft.get("name"), draft.get("old_name")
            if old and old in project.kinds and old != name:
                del project.kinds[old]
            project.kinds[name] = draft.get("kind") or {}
        record = body.get("record")
        if not isinstance(record, dict):
            raise ProjectError("record must be an object")
        record = dict(record)
        record.setdefault("itemdefid", 0)
        if not isinstance(record["itemdefid"], int):
            raise ProjectError("itemdefid must be a number")
        key = body.get("series")
        info = None
        if record["itemdefid"] == 0 and key in project.series:
            # A new series item: preview it with the itemdefid and position it will get.
            ids = [m["itemdefid"] for m in project.members(key)]
            position = body.get("position")
            if position and 1 <= position <= len(ids):
                record["itemdefid"] = ids[position - 1]
            else:
                position = len(ids) + 1
                slot = (ids[-1] + 1) if ids else project.series[key]["first_id"]
                skip = project.retired_ids() if project.mode == "release" else frozenset()
                while slot in skip:
                    slot += 1
                record["itemdefid"] = slot
            info = project.series_info(key, position, len(ids) + 1)
        else:
            info = project.series_positions().get(record["itemdefid"])
        schema = project.schema()
        item, problems = derive.resolve(schema, record, info)
        kind = schema.kind_of(record)
        rules = derive.rules_of(kind)
        fields = derive.fields_of(kind)
        bare = {k: v for k, v in record.items() if k not in rules or k in fields}
        by_rule = derive.resolve(schema, bare, info)[0] if kind else {}
        if info is not None:
            try:
                item["description"] = project.render_description(info.key, item, info, record)
            except ProjectError as e:
                problems.append(str(e))
        problems.extend(project.resolve_colors(item))
        return {"item": item, "problems": problems, "rules": {f: by_rule.get(f) for f in rules},
                "overrides": derive.overridden(schema, record)}

    # -------------------------------------------------------------- actions

    def handle(self, route: str, body: dict):
        confirm = bool(body.get("confirm"))
        if route.startswith(("launcher/", "app/")):
            return self.handle_launcher(route, body)
        if route == "preview":
            return self.preview(body)
        if route == "undo":
            return self.undo(confirm)
        if route == "item/save":
            return self.mutate("Saving this item", lambda p: _save_item(p, body["record"]), confirm)
        if route == "item/create":
            return self.mutate("Adding this item", lambda p: _create_item(p, body), confirm)
        if route == "item/remove":
            return self.mutate("Removing this item", lambda p: _remove_item(p, body), confirm)
        if route == "item/move":
            return self.mutate("Moving this item", lambda p: _move_item(p, body), confirm)
        if route == "items/set":
            assignments = [(f, v) for f, v in body.get("assignments", [])]
            return self.mutate("This change", lambda p: {"notes": edits.set_fields(
                p, body.get("ids", []), assignments, body.get("unset", []))}, confirm)
        if route == "items/adopt":
            if body.get("dry_run"):
                project = self.load()
                return adopt.adopt(project, body["kind"], body.get("ids", [])).as_dict()
            return self.mutate("Converting these items", lambda p: adopt.adopt(
                p, body["kind"], body.get("ids", [])).as_dict(), confirm)
        if route == "items/detach":
            return self.mutate("Detaching these items", lambda p: {"changed": adopt.detach(p, body.get("ids", []))},
                               confirm)
        if route == "table/save":
            return self.mutate("This table change", lambda p: _save_table(p, body), confirm)
        if route == "colors/save":
            return self.mutate("This colour change", lambda p: _save_colors(p, body), confirm)
        if route == "colors/convert":
            if body.get("dry_run"):
                return colors.convert(self.load()).as_dict()
            return self.mutate("Converting colours", lambda p: colors.convert(p).as_dict(), confirm)
        if route == "table/csv-preview":
            data = tableimport.read_csv(str(body.get("csv") or ""))
            clean = (lambda v: v.strip()) if body.get("raw") else tableimport.clean_value
            return {"header": data.header, "columns": [tableimport.column_name(h) for h in data.header],
                    "rows": len(data.rows), "samples": [[clean(v) for v in r] for r in data.rows[:3]]}
        if route == "table/import":
            def run(p: Project):
                report = tableimport.import_csv(
                    p, str(body.get("name") or ""), tableimport.read_csv(str(body.get("csv") or "")),
                    key_column=body.get("key_column") or None, only=body.get("only"),
                    skip=body.get("skip") or [], rename=body.get("rename") or {},
                    fills=[tuple(f) for f in body.get("fills") or []], key_case=body.get("key_case") or "auto",
                    raw=bool(body.get("raw")))
                return dict(report.as_dict(), lines=report.lines(verbose=True))
            if body.get("dry_run"):
                return run(self.load())
            return self.mutate("Importing into a table", run, confirm)
        if route == "table/delete":
            return self.mutate("Deleting this table", lambda p: edits.delete_table(p, body["name"]), confirm)
        if route == "kind/save":
            return self.mutate("This kind change", lambda p: edits.replace_kind(
                p, body["name"], body["kind"], body.get("old_name")), confirm)
        if route == "schema/import":
            data = body.get("schema")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError as e:
                    raise ProjectError(f"the schema is not valid JSON: {e}")

            def merge(p: Project):
                return {"notes": edits.import_schema(p, data)}
            if body.get("dry_run"):
                return merge(self.load())
            return self.mutate("Importing this schema", merge, confirm)
        if route == "kind/delete":
            return self.mutate("Deleting this kind", lambda p: _delete_kind(p, body), confirm)
        if route == "series/save":
            return self.mutate("This series change", lambda p: {"notes": edits.save_series(
                p, body["key"], body.get("config", {}), bool(body.get("is_new")))}, confirm)
        if route == "series/plan":
            project = self.load()
            return edits.series_copy_plan(project, str(body.get("source") or ""), str(body.get("key") or ""),
                                          str(body.get("name") or ""), int(body.get("first_id") or 0))
        if route == "series/create":
            def create(p: Project):
                return edits.create_series(p, str(body.get("key") or ""), body.get("config") or {},
                                           body.get("copy") or None)
            if body.get("dry_run"):
                return create(self.load())
            return self.mutate("Creating this series", create, confirm)
        if route == "series/order":
            order = edits.series_by_first_id(self.load()) if body.get("by_id") else list(body.get("order") or [])
            return self.mutate("Reordering series", lambda p: {"order": edits.reorder_series(p, order)}, confirm)
        if route == "series/delete":
            return self.mutate("Deleting this series", lambda p: edits.delete_series(p, body["key"]), confirm)
        if route == "settings/save":
            return self.mutate("This settings change", lambda p: _save_settings(p, body), confirm)
        if route == "mode":
            return self.set_mode(body.get("mode"), confirm)
        if route == "mark-live":
            return self.mutate("Recording the live baseline", lambda p: {"count": p.record_live()}, True,
                               record_undo=False)
        if route == "export":
            return self.export(body, confirm)
        if route == "import":
            return self.import_files(body, confirm)
        if route == "diff":
            return self.diff()
        raise ProjectError(f"unknown action {route!r}")

    def handle_launcher(self, route: str, body: dict):
        if route == "launcher/browse":
            return launcher.browse(body.get("path") or None)
        if route == "launcher/open":
            return self.open_project(str(body.get("path") or ""))
        if route == "launcher/close":
            return self.close_project()
        if route == "launcher/forget":
            launcher.forget(str(body.get("path") or ""))
            return {"recent": launcher.recent_projects()}
        if route == "launcher/create":
            appid = body.get("appid")
            if isinstance(appid, str):
                appid = int(appid) if appid.strip().isdigit() else (None if not appid.strip() else appid)
            path, report = launcher.create_project(str(body.get("folder") or ""), str(body.get("filename") or ""),
                                                   [str(f) for f in body.get("files") or []], appid)
            self.open_project(path)
            return {"path": path, "report": report}
        if route == "app/quit":
            if self.shutdown is not None:
                threading.Timer(0.3, self.shutdown).start()
            return {"stopping": True}
        raise ProjectError(f"unknown action {route!r}")

    def undo(self, confirm: bool):
        with self.lock:
            if not self.undo_stack:
                raise ProjectError("nothing to undo")
            label, data = self.undo_stack[-1]

            def restore(p: Project):
                # Undo reverts content only: the mode and live baseline stay.
                restored = json.loads(data)
                restored["mode"] = p.data["mode"]
                restored["live"] = p.data.get("live")
                p.data = restored
                p._check_shape()

            self.mutate(f"Undoing: {label}", restore, confirm, record_undo=False)
            self.undo_stack.pop()
            return {"undone": label}

    def set_mode(self, mode: str, confirm: bool):
        if mode not in MODES:
            raise ProjectError(f"mode must be one of {', '.join(MODES)}")
        project = self.load()
        if mode == project.mode:
            return {"mode": mode}
        if mode == "release":
            errors = [i for i in check.check_project(project) if i.level == "error"]
            if errors:
                raise ProjectError("fix the errors reported by Check before switching to release mode")

            def to_release(p: Project):
                p.mode = "release"
                return {"mode": "release", "count": p.record_live()}

            return self.mutate("Switching to release mode", to_release, True, record_undo=False)
        if not confirm:
            raise NeedsConfirmation(
                "Leaving release mode", [],
                "Prerelease mode no longer asks before renumbering, removing or replacing items that players "
                "may already own. Only do this if nobody owns items yet.")

        def to_prerelease(p: Project):
            p.mode = "prerelease"
            return {"mode": "prerelease"}

        return self.mutate("Switching to prerelease mode", to_prerelease, True, record_undo=False)

    def export(self, body: dict, confirm: bool):
        project = self.load()
        path = os.path.abspath(body.get("path") or os.path.join(os.path.dirname(self.path), "itemdefs.json"))
        if path == self.path:
            raise ProjectError("the export would overwrite the project file; choose another file")
        errors = [i for i in check.check_project(project) if i.level == "error"]
        if errors:
            raise ProjectError(f"not exported: fix the {len(errors)} error(s) reported by Check first")
        built = project.build()
        live = project.live_names()
        if project.mode == "release" and live is not None and not confirm:
            impacts = safety.compare(live, project, built)
            if impacts:
                raise NeedsConfirmation("Uploading this export", impacts)
        jsonfmt.write(path, {"appid": project.appid, "items": built} if project.appid is not None
                      else {"items": built})
        count = None
        if body.get("mark_live"):
            count = self.mutate("Recording the live baseline", lambda p: p.record_live(), True, record_undo=False)
        return {"path": path, "items": len(built), "dummies": sum(1 for it in built if project.is_dummy(it)),
                "marked_live": count}

    def import_files(self, body: dict, confirm: bool):
        files = body.get("files") or []
        if not files:
            raise ProjectError("no files given")
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for n, f in enumerate(files):
                path = os.path.join(tmp, f"{n}-{os.path.basename(str(f.get('name') or 'file.json'))}")
                with open(path, "w", encoding="utf-8") as out:
                    out.write(str(f.get("content", "")))
                paths.append(path)
            report = []

            def run(p: Project):
                _, rep = importer.import_files(paths, p, replace=bool(body.get("replace")))
                report.extend(text.replace(tmp + os.sep, "") for _, text in rep.lines)

            self.mutate("Importing definitions", run, confirm)
            return {"report": report}

    def diff(self):
        project = self.load()
        live = project.live_names()
        if live is None:
            return {"recorded": False, "impacts": [], "added": []}
        built = project.build()
        names = project.owned_names(built)
        return {
            "recorded": True,
            "recorded_at": project.live.get("recorded_at"),
            "impacts": [_impact_json(i) for i in safety.compare(live, project, built)],
            "added": [{"itemdefid": i, "name": names[i]} for i in sorted(set(names) - set(live))],
        }


# ------------------------------------------------------------- mutations


def _check_record(project: Project, record: dict) -> dict:
    if not isinstance(record, dict):
        raise ProjectError("record must be an object")
    if record.get("kind") in ("", None):
        record = {k: v for k, v in record.items() if k != "kind"}
    elif record["kind"] not in project.kinds:
        raise ProjectError(f"no kind named {record['kind']!r}")
    for key in record:
        if not isinstance(key, str) or not key.strip():
            raise ProjectError("field names cannot be empty")
    return record


def _save_item(project: Project, record: dict):
    record = _check_record(project, record)
    i = record.get("itemdefid")
    for n, it in enumerate(project.items):
        if it["itemdefid"] == i:
            project.items[n] = {"itemdefid": i, **{k: v for k, v in record.items() if k != "itemdefid"}}
            return {"id": i}
    raise ProjectError(f"no item definition {i}")


def _create_item(project: Project, body: dict):
    record = _check_record(project, dict(body.get("record") or {}))
    key = body.get("series")
    if key:
        position = body.get("position")
        skip = project.retired_ids() if project.mode == "release" else frozenset()
        result = ops.insert_items(project, key, [record], position=position, skip_ids=skip)
        return {"id": result.added[0], "moved": {str(k): v for k, v in result.moved.items()}}
    i = body.get("itemdefid")
    if not isinstance(i, int) or isinstance(i, bool) or i <= 0:
        raise ProjectError("give the new definition a positive itemdefid")
    if project.item(i) is not None:
        raise ProjectError(f"itemdefid {i} is already used")
    if project.series_for_id(i) is not None:
        raise ProjectError(f"itemdefid {i} is inside series {project.series_for_id(i)!r}; add it to the series")
    record.pop("itemdefid", None)
    project.items.append({"itemdefid": i, **record})
    return {"id": i}


def _remove_item(project: Project, body: dict):
    i = body.get("id")
    if project.series_for_id(i) is not None:
        result = ops.remove_item(project, i, shift=bool(body.get("shift", True)))
        return {"moved": {str(k): v for k, v in result.moved.items()}}
    refs = ops.references_to(project, i)
    if refs:
        raise ProjectError(f"itemdefid {i} is still referenced by: {', '.join(f'{r} ({f})' for r, f in refs)}")
    before = len(project.items)
    project.items[:] = [it for it in project.items if it["itemdefid"] != i]
    if len(project.items) == before:
        raise ProjectError(f"no item definition {i}")
    return {"note": "Removed from the project. Steam keeps its current definition of this ID."}


def _move_item(project: Project, body: dict):
    result = ops.move_item(project, body.get("id"), position=body.get("position"))
    return {"moved": {str(k): v for k, v in result.moved.items()}}


def _save_table(project: Project, body: dict):
    name, old = body.get("name"), body.get("old_name")
    if not isinstance(name, str) or not name.isidentifier():
        raise ProjectError("table names may only contain letters, digits and _")
    if old and old != name:
        if name in project.tables:
            raise ProjectError(f"table {name!r} already exists")
        project.tables[name] = project.tables.pop(old)
        for kind in project.kinds.values():
            for spec in derive.fields_of(kind).values():
                if spec.get("type") == "ref" and spec.get("table") == old:
                    spec["table"] = name
    elif not old and name in project.tables:
        raise ProjectError(f"table {name!r} already exists")
    table = body.get("table") or {}
    for column in table.get("columns", []):
        if not isinstance(column, str) or not column.isidentifier():
            raise ProjectError(f"invalid column name {column!r}")
    keys = list((table.get("rows") or {}).keys())
    if any(not str(k).strip() for k in keys):
        raise ProjectError("row keys cannot be empty")
    edits.replace_table(project, name, table, body.get("renames"))
    return {"name": name}


def _save_colors(project: Project, body: dict):
    """Replace the palette (the GUI's colour editor). ``renames`` maps old
    keywords to new ones so references follow."""
    palette = body.get("colors")
    if not isinstance(palette, dict):
        raise ProjectError("colors must be an object mapping keywords to hex colours")
    for old, new in (body.get("renames") or {}).items():
        if old in project.colors and old != new:
            colors.rename(project, old, new)
    for kw in list(project.colors):
        if kw not in palette:
            colors.delete(project, kw)
    for kw, value in palette.items():
        colors.set_color(project, kw, str(value))
    project.data["colors"] = {kw: project.colors[kw] for kw in palette}


def _delete_kind(project: Project, body: dict):
    name = body.get("name")
    if body.get("detach_items"):
        adopt.detach(project, edits.kind_users(project, name))
    edits.delete_kind(project, name)


def _save_settings(project: Project, body: dict):
    if "description_template" in body:
        template = body["description_template"]
        if not isinstance(template, (str, list)) or not template:
            raise ProjectError("the description template cannot be empty")
        project.settings["description_template"] = template
    if "dummy_item" in body:
        dummy = body["dummy_item"]
        if not isinstance(dummy, dict) or not isinstance(dummy.get("name"), str) or not dummy["name"]:
            raise ProjectError("the dummy item needs a name")
        project.settings["dummy_item"] = dummy
    project.build()  # validates the template


# ---------------------------------------------------------------- server


class Handler(BaseHTTPRequestHandler):
    server_version = "sisdefman"
    app: App  # set on the subclass

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
                html = f.read().replace("__SISDEFMAN_TOKEN__", self.app.token)
            return self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            name = os.path.basename(path)
            full = os.path.join(WEB_DIR, name)
            if name and os.path.isfile(full):
                with open(full, "rb") as f:
                    data = f.read()
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                return self._send(HTTPStatus.OK, data, ctype)
        if path == "/api/state":
            return self._api(lambda: self.app.state())
        return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    def do_POST(self):
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
        except (ValueError, UnicodeDecodeError):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
        if not isinstance(body, dict):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "the body must be a JSON object"})
        return self._api(lambda: self.app.handle(path[len("/api/"):], body))

    def _api(self, fn) -> None:
        if not secrets.compare_digest(self.headers.get("X-Sisdefman-Token", ""), self.app.token):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "missing or wrong token; reload the page"})
        try:
            result = fn()
        except NeedsConfirmation as e:
            return self._json(HTTPStatus.CONFLICT, {"needs_confirmation": True, "action": e.action,
                                                    "message": e.message,
                                                    "impacts": [_impact_json(i) for i in e.impacts]})
        except (ProjectError, steam.SyntaxProblem, KeyError, TypeError, ValueError) as e:
            text = f"missing {e}" if isinstance(e, KeyError) else str(e)
            return self._json(HTTPStatus.BAD_REQUEST, {"error": text})
        except Exception as e:  # report it to the page instead of dropping the connection
            traceback.print_exc()
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"internal error: {e}"})
        return self._json(HTTPStatus.OK, {"ok": True, "result": result})


def make_server(path: Optional[str] = None, host: str = "127.0.0.1",
                port: int = 0) -> Tuple[ThreadingHTTPServer, App]:
    """A server for ``path``, or with no project open (the page then asks
    for one)."""
    app = App()
    if path:
        if not os.path.exists(path):
            raise ProjectError(f"{path} not found (create a project with `sisdefman import FILE...` first, "
                               "or run `sisdefman gui --choose` to pick or create one in the browser)")
        app.open_project(path)  # fails early on a broken file
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    app.shutdown = server.shutdown
    return server, app


def serve(path: Optional[str] = None, host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> int:
    server, _ = make_server(path, host, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if path:
        print(ui.green(f"sisdefman GUI for {path} at {url}"))
    else:
        print(ui.green(f"sisdefman GUI at {url}") + " (no project open: choose or create one in the browser)")
    print("Keep this window open while you use it; press Ctrl+C or use Quit in the page to stop.")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
        print("Stopped.")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """``sisdefman-gui [PROJECT]``: open the GUI, with the project chooser
    unless a project file is given. Handy for a desktop shortcut."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="sisdefman-gui",
        description="Open the sisdefman GUI in your browser, with the project chooser unless a project file is given.")
    parser.add_argument("project", nargs="?", help="project file to open (default: choose one in the browser)")
    parser.add_argument("--port", type=int, default=0, help="port to listen on (default: any free port)")
    parser.add_argument("--no-browser", action="store_true", help="only print the address")
    args = parser.parse_args(argv)
    try:
        return serve(args.project, port=args.port, open_browser=not args.no_browser)
    except ProjectError as e:
        print(ui.red(f"error: {e}"))
        return 1
