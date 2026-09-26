import http.client
import json
import os
import tempfile
import threading
import unittest.mock
import unittest

from sisdefman import gui, importer
from sisdefman.project import Project, ProjectError

from tests import fixtures


class GuiServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        project.data.update(fixtures.skin_schema())
        self.path = os.path.join(self.tmp.name, "project.json")
        project.save(self.path)
        self.server, self.app = gui.make_server(self.path)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json", "Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Sisdefman-Token"] = self.app.token
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        res = conn.getresponse()
        data = res.read().decode("utf-8")
        conn.close()
        try:
            return res.status, json.loads(data)
        except ValueError:
            return res.status, data

    def post(self, route, body=None):
        return self.call("POST", "/api/" + route, body or {})

    def project(self):
        return Project.load(self.path)

    def test_page_and_security(self):
        status, html = self.call("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.app.token, html)
        self.assertEqual(self.call("GET", "/static/app.js", token=False)[0], 200)
        self.assertEqual(self.call("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.call("GET", "/", token=False, host="evil.example")[0], 403)
        self.assertEqual(self.call("GET", "/static/../gui.py", token=False)[0], 404)

    def test_state(self):
        status, data = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        state = data["result"]
        self.assertEqual(state["series"]["crate1"]["members"], list(range(110, 118)))
        self.assertEqual(len([e for e in state["items"] if e["dummy"]]), 2)
        self.assertIn("skin", state["kinds"])

    def test_adopt_preview_and_save(self):
        status, data = self.post("items/adopt", {"kind": "skin", "ids": list(range(110, 118)), "dry_run": True})
        self.assertEqual(len(data["result"]["adopted"]), 8)
        self.assertNotIn("kind", self.project().item(110))  # dry run saved nothing
        self.post("items/adopt", {"kind": "skin", "ids": list(range(110, 118))})
        record = self.project().item(110)
        self.assertEqual(record["kind"], "skin")

        record["flavor"] = "Painted red."
        status, data = self.post("preview", {"record": record})
        self.assertEqual(data["result"]["item"]["description"],
                         "Applies the Red appearance to the Pistol.\n\nPainted red.\n\nTest Series #1")
        self.assertEqual(data["result"]["rules"]["name"], "Pistol | Red")
        status, _ = self.post("item/save", {"record": record})
        self.assertEqual(status, 200)
        self.assertEqual(self.project().item(110)["flavor"], "Painted red.")

        kind = self.project().kinds["skin"]
        kind["derive"]["name"] = "{finish} {weapon.name}"
        status, data = self.post("preview", {"record": record, "kind_draft": {"name": "skin", "old_name": "skin",
                                                                              "kind": kind}})
        self.assertEqual(data["result"]["item"]["name"], "Red Pistol")
        self.assertEqual(self.project().kinds["skin"]["derive"]["name"], "{weapon.name} | {finish}")

        # A new item is previewed with the ID and series position it will get.
        new = {"kind": "skin", "weapon": "rifle", "finish": "Gold", "rarity": "common"}
        status, data = self.post("preview", {"record": new, "series": "crate1"})
        item = data["result"]["item"]
        self.assertEqual(list(item)[0], "itemdefid")
        self.assertEqual(item["itemdefid"], 118)
        self.assertEqual(item["tags"], "type:skin;series:crate1;rarity:common;weapon:rifle")
        self.assertTrue(item["description"].endswith("Test Series #9"))
        status, data = self.post("preview", {"record": new, "series": "crate1", "position": 2})
        self.assertEqual(data["result"]["item"]["itemdefid"], 111)
        self.assertTrue(data["result"]["item"]["description"].endswith("Test Series #2"))

    def test_release_mode_needs_confirmation_and_undo(self):
        self.post("mode", {"mode": "release"})
        body = {"record": {"type": "item", "name": "New"}, "series": "crate1", "position": 1}
        status, data = self.post("item/create", body)
        self.assertEqual(status, 409)
        self.assertEqual(data["impacts"][0]["itemdefid"], 110)
        self.assertEqual(self.project().item(110)["name"], "Pistol | Red")
        status, data = self.post("item/create", dict(body, confirm=True))
        self.assertEqual((status, data["result"]["id"]), (200, 110))
        self.assertEqual(self.project().item(110)["name"], "New")
        # Undoing it changes live items back, so it needs confirmation too.
        self.assertEqual(self.post("undo")[0], 409)
        self.assertEqual(self.post("undo", {"confirm": True})[0], 200)
        self.assertEqual(self.project().item(110)["name"], "Pistol | Red")
        self.assertEqual(self.post("undo")[1]["error"], "nothing to undo")  # mode switches are not undone
        self.assertEqual(self.project().mode, "release")
        # Leaving release mode always needs confirmation.
        self.assertEqual(self.post("mode", {"mode": "prerelease"})[0], 409)
        self.assertEqual(self.post("mode", {"mode": "prerelease", "confirm": True})[0], 200)

    def test_tables_kinds_series_settings(self):
        self.post("items/adopt", {"kind": "skin", "ids": list(range(110, 118))})
        table = self.project().tables["weapon"]
        table["rows"]["handgun"] = table["rows"].pop("pistol")
        status, _ = self.post("table/save", {"name": "weapon", "old_name": "weapon", "table": table,
                                             "renames": {"pistol": "handgun"}})
        self.assertEqual(status, 200)
        self.assertEqual(self.project().item(110)["weapon"], "handgun")

        status, data = self.post("kind/delete", {"name": "skin"})
        self.assertEqual(status, 400)
        status, _ = self.post("kind/delete", {"name": "skin", "detach_items": True})
        self.assertEqual(status, 200)
        self.assertEqual(self.project().item(110)["name"], "Pistol | Red")

        status, data = self.post("series/save", {"key": "crate1", "config": {"name": "Renamed", "last_id": 150}})
        self.assertEqual(status, 200)
        self.assertEqual(self.project().series["crate1"]["last_id"], 150)
        status, data = self.post("series/save", {"key": "crate1", "config": {"last_id": 115}})
        self.assertEqual(status, 400)
        status, _ = self.post("settings/save", {"description_template": "{description} ({index})"})
        self.assertEqual(status, 200)
        status, data = self.post("settings/save", {"description_template": "{nope}"})
        self.assertEqual(status, 400)

    def test_table_csv_import(self):
        status, data = self.post("table/csv-preview", {"csv": fixtures.WEAPON_CSV})
        self.assertEqual(data["result"]["rows"], 3)
        self.assertEqual(data["result"]["samples"][0][1], "Pistol")
        self.assertEqual(data["result"]["columns"][0], "column")
        body = {"name": "weapon_data", "csv": fixtures.WEAPON_CSV, "only": ["DisplayName", "Range"],
                "rename": {"DisplayName": "label"}, "key_case": "lower"}
        status, data = self.post("table/import", dict(body, dry_run=True))
        self.assertEqual(data["result"]["added"], ["pistol", "rifle", "knife"])
        self.assertNotIn("weapon_data", self.project().tables)
        status, data = self.post("table/import", body)
        self.assertEqual(status, 200)
        self.assertEqual(self.project().tables["weapon_data"]["rows"]["rifle"], {"label": "Long Rifle", "Range": "LONG"})
        self.assertEqual(self.post("table/csv-preview", {"csv": ""})[0], 400)

    def test_items_remove_move_set_export_import(self):
        status, data = self.post("item/move", {"id": 117, "position": 1})
        self.assertEqual(data["result"]["moved"]["117"], 110)
        status, _ = self.post("items/set", {"ids": [110, 111], "assignments": [["marketable", False]]})
        self.assertIs(self.project().item(111)["marketable"], False)
        status, _ = self.post("item/remove", {"id": 111, "shift": False})
        self.assertIsNone(self.project().item(111))
        status, data = self.post("item/create", {"record": {"type": "item", "name": "Pack"}, "itemdefid": 500})
        self.assertEqual(data["result"]["id"], 500)
        self.assertEqual(self.post("item/create", {"record": {}, "itemdefid": 500})[0], 400)
        self.assertEqual(self.post("item/remove", {"id": 500})[0], 200)

        out = os.path.join(self.tmp.name, "out.json")
        status, data = self.post("export", {"path": out, "mark_live": True})
        self.assertEqual(status, 200)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["items"]), data["result"]["items"])
        self.assertEqual(self.post("export", {"path": self.path})[0], 400)

        extra = {"appid": fixtures.APPID, "items": [{"itemdefid": 700, "type": "item", "name": "Imported"}]}
        status, data = self.post("import", {"files": [{"name": "x.json", "content": json.dumps(extra)}]})
        self.assertEqual(status, 200)
        self.assertEqual(self.project().item(700)["name"], "Imported")
        self.assertEqual(self.post("nope")[0], 400)


if __name__ == "__main__":
    unittest.main()


class LauncherTests(unittest.TestCase):
    """The GUI started without a project."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.config = tempfile.TemporaryDirectory()
        self.env = unittest.mock.patch.dict(os.environ, {"SISDEFMAN_CONFIG_DIR": self.config.name})
        self.env.start()
        self.files = fixtures.write_files(self.dir)
        project, _ = importer.import_files(self.files)
        self.existing = os.path.join(self.dir, "existing.json")
        project.save(self.existing)
        os.makedirs(os.path.join(self.dir, "subfolder"))
        self.server, self.app = gui.make_server(None)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.env.stop()
        self.tmp.cleanup()
        self.config.cleanup()

    call = GuiServerTests.call
    post = GuiServerTests.post

    def state(self):
        return self.call("GET", "/api/state")[1]["result"]

    def test_choose_open_switch_and_create(self):
        state = self.state()
        self.assertFalse(state["open"])
        self.assertEqual(state["recent"], [])
        status, data = self.post("item/save", {"record": {"itemdefid": 1}})
        self.assertEqual((status, data["error"]), (400, "no project is open"))

        listing = self.post("launcher/browse", {"path": self.dir})[1]["result"]
        types = {e["name"]: e["type"] for e in listing["entries"]}
        self.assertEqual(types, {"subfolder": "dir", "crate.json": "json", "extras.json": "json",
                                 "existing.json": "project"})
        self.assertEqual(listing["entries"][0]["name"], "subfolder")  # folders first
        self.assertEqual(self.post("launcher/browse", {"path": self.files[0]})[0], 400)

        self.assertEqual(self.post("launcher/open", {"path": self.files[0]})[0], 400)  # not a project
        status, _ = self.post("launcher/open", {"path": self.existing})
        self.assertEqual(status, 200)
        state = self.state()
        self.assertTrue(state["open"])
        self.assertEqual(state["file"], "existing.json")

        # Edits, then switching projects clears the undo history.
        self.post("items/set", {"ids": [110], "assignments": [["name", "Changed"]]})
        self.assertEqual(self.state()["undo"], ["This change"])
        self.post("launcher/close")
        self.assertFalse(self.state()["open"])
        self.assertEqual(self.state()["recent"][0]["path"], self.existing)

        status, data = self.post("launcher/create", {"folder": self.dir, "filename": "new", "files": self.files})
        self.assertEqual(status, 200, data)
        state = self.state()
        self.assertEqual((state["file"], state["undo"]), ("new.json", []))
        self.assertIn("crate1", state["series"])
        self.assertTrue(any("series 'crate1'" in line for line in data["result"]["report"]))
        self.post("launcher/close")
        status, data = self.post("launcher/create", {"folder": self.dir, "filename": "new.json", "files": []})
        self.assertEqual(status, 400)
        self.assertIn("already exists", data["error"])
        status, _ = self.post("launcher/create", {"folder": self.dir, "filename": "empty.json", "appid": "480"})
        self.assertEqual(status, 200)
        self.assertEqual(self.state()["appid"], 480)
        self.assertEqual(self.post("launcher/create", {"folder": self.dir, "filename": "../x.json"})[0], 400)

        self.post("launcher/close")
        recent = [r["name"] for r in self.state()["recent"]]
        self.assertEqual(recent, ["empty.json", "new.json", "existing.json"])
        os.remove(os.path.join(self.dir, "new.json"))
        missing = {r["name"]: r["exists"] for r in self.state()["recent"]}
        self.assertFalse(missing["new.json"])
        data = self.post("launcher/forget", {"path": os.path.join(self.dir, "new.json")})[1]["result"]
        self.assertEqual([r["name"] for r in data["recent"]], ["empty.json", "existing.json"])

    def test_quit_stops_the_server(self):
        self.assertEqual(self.post("app/quit")[0], 200)
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())


class GuiStartTests(unittest.TestCase):
    def test_which_project_the_gui_opens(self):
        from sisdefman import cli

        parse = cli.build_parser().parse_args
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SISDEFMAN_PROJECT", None)
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertIsNone(cli.gui_project(parse(["gui"])))  # nothing here: the chooser
                with open("sisdefman.json", "w") as f:
                    f.write("{}")
                self.assertEqual(cli.gui_project(parse(["gui"])), "sisdefman.json")
                self.assertIsNone(cli.gui_project(parse(["gui", "--choose"])))
                self.assertEqual(cli.gui_project(parse(["-p", "other.json", "gui"])), "other.json")
            finally:
                os.chdir(cwd)
        with self.assertRaises(ProjectError):
            gui.make_server(os.path.join(tempfile.gettempdir(), "does-not-exist.json"))

