"""The sisdefman project file.

A project is one JSON file that holds every item definition for an app plus
the metadata sisdefman needs to manage them:

* ``items``   - one record per itemdefid. A record without a ``kind`` holds
  the Steam definition as-is; a record with a kind holds that kind's fields
  and any overrides, and the rest is derived (see ``derive``).
* ``tables``  - lookup tables used by kinds (e.g. weapon -> display name).
* ``kinds``   - families of items whose fields are derived from templates.
* ``series``  - ordered groups of items that occupy a contiguous ID range.
  An item's position within its series decides both its itemdefid and the
  "#index" written into its description.
* ``mode``    - ``prerelease`` or ``release``. Release mode guards against
  changes that would alter items players already own.
* ``live``    - in release mode, the name of every item definition that is
  live on Steam, used to detect such changes.

``Project.build()`` turns the project into the list of definitions that is
uploaded to Steam.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
from typing import Dict, List, Optional, Tuple

from . import derive, jsonfmt, steam
from .derive import SeriesInfo

FORMAT_VERSION = 2
KEY_ORDER = ("sisdefman", "appid", "mode", "settings", "tables", "kinds", "series", "items", "live")
MODES = ("prerelease", "release")

# Placeholder in a container's description that is replaced by the list of
# the series' item names.
CONTENTS_TOKEN = "{contents}"

DEFAULT_DESCRIPTION_TEMPLATE = "{description}\n\n{series_name} #{index}"

# Matches the placeholder definitions already used for retired IDs.
DEFAULT_DUMMY_ITEM = {
    "type": "item",
    "name": "Dummy Item #{itemdefid}",
    "description": "This is a dummy item.",
    "name_color": "d2d2d2",
    "background_color": "292929",
    "icon_url": "",
    "icon_url_large": "",
    "tradable": False,
    "marketable": False,
}

TEMPLATE_FIELDS = ("description", "series", "series_name", "index", "count", "itemdefid", "name", "tags")


class ProjectError(Exception):
    """The project file is malformed or an operation cannot be performed."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


class Project:
    def __init__(self, data: dict, path: Optional[str] = None):
        self.data = data
        self.path = path
        self._check_shape()

    # ------------------------------------------------------------ load/save

    @classmethod
    def new(cls, appid: Optional[int], path: Optional[str] = None) -> "Project":
        data = {
            "sisdefman": FORMAT_VERSION,
            "appid": appid,
            "mode": "prerelease",
            "settings": {
                "description_template": DEFAULT_DESCRIPTION_TEMPLATE,
                "dummy_item": copy.deepcopy(DEFAULT_DUMMY_ITEM),
            },
            "tables": {},
            "kinds": {},
            "series": {},
            "items": [],
            "live": None,
        }
        return cls(data, path)

    @classmethod
    def load(cls, path: str) -> "Project":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise ProjectError(f"{path} not found (create a project with `sisdefman import FILE...`)")
        except json.JSONDecodeError as e:
            raise ProjectError(f"{path}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}")
        if not isinstance(data, dict) or "sisdefman" not in data:
            raise ProjectError(
                f"{path} is not a sisdefman project file. "
                "To bring in Steam item definition files use `sisdefman import`."
            )
        return cls(data, path)

    def to_json(self) -> str:
        ordered = {k: self.data[k] for k in KEY_ORDER if k in self.data}
        ordered.update((k, v) for k, v in self.data.items() if k not in ordered)
        return jsonfmt.dumps(ordered)

    def save(self, path: Optional[str] = None) -> None:
        path = path or self.path
        if not path:
            raise ProjectError("no project path given")
        self.normalize()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.to_json())
        os.replace(tmp, path)
        self.path = path

    def _check_shape(self) -> None:
        d = self.data
        if d.get("sisdefman") == 1:
            d["sisdefman"] = FORMAT_VERSION  # version 2 only adds tables and kinds
        if d.get("sisdefman") != FORMAT_VERSION:
            raise ProjectError(f"unsupported project format version {d.get('sisdefman')!r}")
        if d.get("mode") not in MODES:
            raise ProjectError(f"mode must be one of {', '.join(MODES)} (got {d.get('mode')!r})")
        d.setdefault("settings", {})
        d["settings"].setdefault("description_template", DEFAULT_DESCRIPTION_TEMPLATE)
        d["settings"].setdefault("dummy_item", copy.deepcopy(DEFAULT_DUMMY_ITEM))
        d.setdefault("series", {})
        d.setdefault("tables", {})
        d.setdefault("kinds", {})
        d.setdefault("items", [])
        d.setdefault("live", None)
        if not isinstance(d["items"], list):
            raise ProjectError("items must be a list")
        seen = set()
        for it in d["items"]:
            if not isinstance(it, dict) or not isinstance(it.get("itemdefid"), int) or isinstance(it.get("itemdefid"), bool):
                raise ProjectError(f"every item needs an integer itemdefid (found {json.dumps(it)[:80]})")
            if it["itemdefid"] in seen:
                raise ProjectError(f"itemdefid {it['itemdefid']} is defined more than once")
            seen.add(it["itemdefid"])
            if "kind" in it and not isinstance(it["kind"], str):
                raise ProjectError(f"itemdefid {it['itemdefid']}: kind must be a string")
        for field in ("tables", "kinds"):
            if not isinstance(d[field], dict):
                raise ProjectError(f"{field} must be an object keyed by name")
        if not isinstance(d["series"], dict):
            raise ProjectError("series must be an object keyed by series name")
        for key, s in d["series"].items():
            if not isinstance(s, dict):
                raise ProjectError(f"series {key!r} must be an object")
            for field in ("first_id", "last_id"):
                if not isinstance(s.get(field), int):
                    raise ProjectError(f"series {key!r} needs an integer {field}")
            if s["last_id"] < s["first_id"]:
                raise ProjectError(f"series {key!r}: last_id is before first_id")
            s.setdefault("allocated_through", None)
            s.setdefault("containers", {})
            s.setdefault("generators", {})
            for field in ("containers", "generators"):
                if not isinstance(s[field], dict):
                    raise ProjectError(f"series {key!r}: {field} must be an object keyed by itemdefid")
                for k in s[field]:
                    if not str(k).isdigit():
                        raise ProjectError(f"series {key!r}: {field} key {k!r} is not an itemdefid")

    # ------------------------------------------------------------ accessors

    @property
    def appid(self):
        return self.data.get("appid")

    @property
    def mode(self) -> str:
        return self.data["mode"]

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in MODES:
            raise ProjectError(f"mode must be one of {', '.join(MODES)}")
        self.data["mode"] = value

    @property
    def settings(self) -> dict:
        return self.data["settings"]

    @property
    def series(self) -> Dict[str, dict]:
        return self.data["series"]

    @property
    def items(self) -> List[dict]:
        return self.data["items"]

    @property
    def live(self) -> Optional[dict]:
        return self.data.get("live")

    @property
    def tables(self) -> Dict[str, dict]:
        return self.data["tables"]

    @property
    def kinds(self) -> Dict[str, dict]:
        return self.data["kinds"]

    def schema(self) -> derive.Schema:
        return derive.Schema(self.tables, self.kinds)

    def by_id(self) -> Dict[int, dict]:
        return {it["itemdefid"]: it for it in self.items}

    def item(self, itemdefid: int) -> Optional[dict]:
        for it in self.items:
            if it["itemdefid"] == itemdefid:
                return it
        return None

    def get_series(self, key: str) -> dict:
        if key not in self.series:
            known = ", ".join(self.series) or "none"
            raise ProjectError(f"no series named {key!r} (known: {known})")
        return self.series[key]

    def series_for_id(self, itemdefid: int) -> Optional[str]:
        for key, s in self.series.items():
            if s["first_id"] <= itemdefid <= s["last_id"]:
                return key
        return None

    def members(self, key: str) -> List[dict]:
        """Items of a series in order. Every item inside the series' ID range
        is a member."""
        s = self.get_series(key)
        return sorted(
            (it for it in self.items if s["first_id"] <= it["itemdefid"] <= s["last_id"]),
            key=lambda it: it["itemdefid"],
        )

    def container_ids(self, key: str) -> List[int]:
        return [int(k) for k in self.get_series(key)["containers"]]

    def generator_rules(self, key: str) -> List[Tuple[int, List[str]]]:
        return [(int(k), steam.parse_tag_rule(v)) for k, v in self.get_series(key)["generators"].items()]

    def managed_generator_ids(self) -> set:
        return {gid for key in self.series for gid, _ in self.generator_rules(key)}

    # --------------------------------------------------------------- dummies

    def make_dummy(self, itemdefid: int) -> dict:
        out = {"itemdefid": itemdefid}
        for k, v in self.settings["dummy_item"].items():
            if k == "itemdefid":
                continue
            out[k] = v.replace("{itemdefid}", str(itemdefid)) if isinstance(v, str) else copy.deepcopy(v)
        return out

    def is_dummy(self, item: Optional[dict]) -> bool:
        if not item:
            return False
        name = self.settings["dummy_item"].get("name", "")
        return bool(name) and item.get("name") == name.replace("{itemdefid}", str(item["itemdefid"]))

    # ------------------------------------------------------ managed content

    def description_template(self, key: str) -> str:
        return self.get_series(key).get("description_template") or self.settings["description_template"]

    def series_name(self, key: str) -> str:
        return self.get_series(key).get("name") or key

    def series_positions(self) -> Dict[int, SeriesInfo]:
        out = {}
        for key in self.series:
            members = self.members(key)
            for index, m in enumerate(members, 1):
                out[m["itemdefid"]] = SeriesInfo(key, self.series_name(key), index, len(members))
        return out

    def resolve_all(self) -> Tuple[Dict[int, dict], Dict[int, List[str]]]:
        """Every record turned into its Steam definition (before series text,
        generator rules and container lists), plus problems per itemdefid."""
        schema = self.schema()
        positions = self.series_positions()
        resolved, problems = {}, {}
        for rec in self.items:
            item, probs = derive.resolve(schema, rec, positions.get(rec["itemdefid"]))
            resolved[rec["itemdefid"]] = item
            if probs:
                problems[rec["itemdefid"]] = probs
        return resolved, problems

    def resolve(self, record: dict) -> Tuple[dict, List[str]]:
        """One record (which need not be saved) as a Steam definition without
        series text."""
        return derive.resolve(self.schema(), record, self.series_positions().get(record["itemdefid"]))

    def render_description(self, key: str, item: dict, index: int, count: int,
                           record: Optional[dict] = None) -> str:
        """The series description template applied to ``item`` (a resolved
        definition). ``record`` gives access to its kind's fields."""
        template = self.description_template(key)
        base = item.get("description") or ""
        values = dict(record or {})
        values.update(item)
        for name in ("description", "name", "tags"):
            values.setdefault(name, "")
        info = SeriesInfo(key, self.series_name(key), index, count)
        ctx = derive.Context(self.schema(), values, info)
        text = ctx.render_rule(template, "description template")
        for problem in ctx.problems:
            if problem.startswith(("unknown field", "description template")):
                raise ProjectError(
                    f"description template {template!r}: {problem}; "
                    f"available: {', '.join(TEMPLATE_FIELDS)}, and the item's own fields"
                )
        return text.strip() if not base.strip() else text

    def template_affixes(self, key: str, item: dict, index: int, count: int) -> Tuple[str, str]:
        """The text the template adds before and after the base description."""
        sentinel = "\x00SISDEFMAN\x00"
        text = self.render_description(key, dict(item, description=sentinel), index, count)
        if sentinel not in text:
            return "", ""
        before, _, after = text.partition(sentinel)
        return before, after

    def _resolved_members(self, key: str, resolved: Optional[Dict[int, dict]]) -> List[dict]:
        if resolved is None:
            resolved, _ = self.resolve_all()
        return [resolved[m["itemdefid"]] for m in self.members(key)]

    def generator_bundle(self, key: str, rule: List[str], resolved: Optional[Dict[int, dict]] = None) -> str:
        return ";".join(str(m["itemdefid"]) for m in self._resolved_members(key, resolved)
                        if steam.has_tags(m, rule))

    def container_listing(self, key: str, container_id: int, resolved: Optional[Dict[int, dict]] = None) -> str:
        cfg = {int(k): v for k, v in self.get_series(key)["containers"].items()}.get(container_id) or {}
        excludes = [steam.parse_tag_rule(r) for r in cfg.get("exclude", [])]
        names = [
            m.get("name", "")
            for m in self._resolved_members(key, resolved)
            if not any(steam.has_tags(m, rule) for rule in excludes)
        ]
        return "\n".join(names)

    # -------------------------------------------------------------- normalize

    def normalize(self) -> bool:
        """Sort items, track the highest ID each series has used and refresh
        generator bundles that are built from series rules. Returns True if
        anything changed."""
        before = self.to_json()
        self.items.sort(key=lambda it: it["itemdefid"])
        by_id = self.by_id()
        resolved = None
        for key, s in self.series.items():
            members = self.members(key)
            if members:
                top = members[-1]["itemdefid"]
                if s.get("allocated_through") is None or s["allocated_through"] < top:
                    s["allocated_through"] = top
            for gid, rule in self.generator_rules(key):
                gen = by_id.get(gid)
                if gen is not None and self.schema().kind_of(gen) is None:
                    if resolved is None:
                        resolved, _ = self.resolve_all()
                    gen["bundle"] = self.generator_bundle(key, rule, resolved)
        return self.to_json() != before

    # ------------------------------------------------------------------ build

    def build(self) -> List[dict]:
        """The item definitions to upload to Steam, sorted by itemdefid."""
        return self.build_with_problems()[0]

    def build_with_problems(self) -> Tuple[List[dict], Dict[int, List[str]]]:
        out, problems = self.resolve_all()
        records = self.by_id()
        for key, s in self.series.items():
            members = self.members(key)
            for index, m in enumerate(members, 1):
                i = m["itemdefid"]
                out[i]["description"] = self.render_description(key, out[i], index, len(members), records[i])
            resolved_members = {m["itemdefid"]: out[m["itemdefid"]] for m in members}
            for gid, rule in self.generator_rules(key):
                if gid in out:
                    out[gid]["bundle"] = self.generator_bundle(key, rule, out)
            for cid in self.container_ids(key):
                c = out.get(cid)
                if c is not None and CONTENTS_TOKEN in (c.get("description") or ""):
                    c["description"] = c["description"].replace(CONTENTS_TOKEN,
                                                                self.container_listing(key, cid, out))
            top = max([s.get("allocated_through") or 0] + list(resolved_members))
            for i in range(s["first_id"], top + 1):
                if i not in out:
                    out[i] = self.make_dummy(i)
        return [out[i] for i in sorted(out)], problems

    def export_document(self) -> dict:
        doc = {}
        if self.appid is not None:
            doc["appid"] = self.appid
        doc["items"] = self.build()
        return doc

    # ------------------------------------------------------------------- live

    def owned_names(self, built: Optional[List[dict]] = None) -> Dict[int, str]:
        """Name of every definition players can own (type "item", not a dummy)."""
        built = self.build() if built is None else built
        return {
            it["itemdefid"]: it.get("name", "")
            for it in built
            if it.get("type") == "item" and not self.is_dummy(it)
        }

    def live_names(self) -> Optional[Dict[int, str]]:
        live = self.live
        if not live or not isinstance(live.get("items"), dict):
            return None
        return {int(k): v for k, v in live["items"].items()}

    def retired_ids(self) -> frozenset:
        """Live IDs whose item has been replaced by a dummy. Players may still
        hold them, so appending to a series skips them in release mode."""
        live = self.live_names() or {}
        by_id = self.by_id()
        return frozenset(i for i in live if i not in by_id and self.series_for_id(i) is not None)

    def record_live(self) -> int:
        """Record what is live on Steam. IDs that were live items and are now
        dummies stay in the record: players can still hold them."""
        built = self.build()
        names = self.owned_names(built)
        exported = {it["itemdefid"]: it for it in built}
        for i in self.live_names() or {}:
            if i not in names and self.is_dummy(exported.get(i)):
                names[i] = exported[i]["name"]
        self.data["live"] = {
            "recorded_at": _now(),
            "items": {str(k): v for k, v in sorted(names.items())},
        }
        return len(names)
