"""Choosing a project in the GUI: recently opened projects, a folder browser
and creating new projects.

The list of recent projects is kept in ``recent.json`` in a per-user
configuration folder (``%APPDATA%\\sisdefman`` on Windows,
``~/Library/Application Support/sisdefman`` on macOS, ``~/.config/sisdefman``
elsewhere, or ``$SISDEFMAN_CONFIG_DIR``).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import string
import sys
from typing import List, Optional, Tuple

from . import importer
from .project import Project, ProjectError

CONFIG_ENV = "SISDEFMAN_CONFIG_DIR"
RECENT_LIMIT = 12
_PROJECT_HEAD = re.compile(rb'^(?:\xef\xbb\xbf)?\s*\{\s*"sisdefman"\s*:')


def config_dir() -> str:
    if os.environ.get(CONFIG_ENV):
        return os.environ[CONFIG_ENV]
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "sisdefman")


def _recent_path() -> str:
    return os.path.join(config_dir(), "recent.json")


def _load_recent() -> List[dict]:
    try:
        with open(_recent_path(), encoding="utf-8") as f:
            data = json.load(f)
        return [r for r in data if isinstance(r, dict) and isinstance(r.get("path"), str)]
    except (OSError, ValueError, TypeError):
        return []


def _save_recent(entries: List[dict]) -> None:
    try:
        os.makedirs(config_dir(), exist_ok=True)
        tmp = _recent_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries[:RECENT_LIMIT], f, indent=2)
        os.replace(tmp, _recent_path())
    except OSError:
        pass  # remembering recent projects is a convenience


def remember(path: str) -> None:
    path = os.path.abspath(path)
    now = datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    entries = [r for r in _load_recent() if os.path.normcase(r["path"]) != os.path.normcase(path)]
    _save_recent([{"path": path, "opened_at": now}] + entries)


def forget(path: str) -> None:
    _save_recent([r for r in _load_recent() if os.path.normcase(r["path"]) != os.path.normcase(path)])


def recent_projects() -> List[dict]:
    return [{
        "path": r["path"],
        "name": os.path.basename(r["path"]),
        "folder": os.path.dirname(r["path"]),
        "exists": os.path.isfile(r["path"]),
        "opened_at": r.get("opened_at", ""),
    } for r in _load_recent()]


def is_project_file(path: str) -> bool:
    """A quick look at the start of a file: is it a sisdefman project?"""
    try:
        with open(path, "rb") as f:
            return bool(_PROJECT_HEAD.match(f.read(256)))
    except OSError:
        return False


def default_folder() -> str:
    for r in recent_projects():
        if r["exists"]:
            return r["folder"]
    return os.getcwd()


def _drives() -> List[str]:
    if os.name != "nt":
        return []
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]


def browse(path: Optional[str] = None) -> dict:
    """Folders and JSON files in ``path`` (default: the folder of the most
    recent project, or the current folder)."""
    path = os.path.abspath(os.path.expanduser(path)) if path else default_folder()
    if not os.path.isdir(path):
        raise ProjectError(f"{path} is not a folder")
    try:
        found = list(os.scandir(path))
    except OSError as e:
        raise ProjectError(f"cannot open {path}: {e.strerror or e}")
    entries = []
    for entry in found:
        if entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            entries.append({"name": entry.name, "path": entry.path, "type": "dir"})
        elif entry.name.lower().endswith(".json"):
            try:
                size = entry.stat().st_size
            except OSError:
                size = None
            entries.append({"name": entry.name, "path": entry.path, "size": size,
                            "type": "project" if is_project_file(entry.path) else "json"})
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else None, "entries": entries,
            "drives": _drives(), "home": os.path.expanduser("~")}


def check_project(path: str) -> str:
    """The absolute path of a loadable project file, or ProjectError."""
    path = os.path.abspath(os.path.expanduser(path or ""))
    if not os.path.isfile(path):
        raise ProjectError(f"{path} does not exist")
    Project.load(path)
    return path


def create_project(folder: str, filename: str, files: List[str], appid: Optional[int]) -> Tuple[str, List[str]]:
    """Create a project file in ``folder``: from Steam item definition files,
    or empty. Returns its path and a report."""
    filename = (filename or "").strip() or "sisdefman.json"
    if os.path.basename(filename) != filename or filename in (".", ".."):
        raise ProjectError("give a file name without folders")
    if not filename.lower().endswith(".json"):
        filename += ".json"
    folder = os.path.abspath(os.path.expanduser(folder or ""))
    if not os.path.isdir(folder):
        raise ProjectError(f"{folder} is not a folder")
    path = os.path.join(folder, filename)
    if os.path.exists(path):
        raise ProjectError(f"{path} already exists; choose another name or open it")
    if files:
        project, report = importer.import_files([os.path.abspath(f) for f in files])
        lines = [text.replace(folder + os.sep, "") for _, text in report.lines]
    else:
        if appid is not None and (not isinstance(appid, int) or isinstance(appid, bool) or appid <= 0):
            raise ProjectError("the app ID must be a positive number")
        project = Project.new(appid)
        lines = ["Created an empty project."]
    project.save(path)
    return path, lines
