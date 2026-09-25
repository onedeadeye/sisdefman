import contextlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock

from sisdefman import cli, ui

from tests import fixtures


class DatabaseCommandTests(unittest.TestCase):
    def setUp(self):
        ui._enabled = False
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.project_path = os.path.join(self.dir, "project.json")
        self.schema_path = os.path.join(self.dir, "schema.json")
        with open(self.schema_path, "w", encoding="utf-8") as f:
            json.dump(fixtures.skin_schema(), f)
        self.cli("import", *fixtures.write_files(self.dir))
        self.before = self.export("before.json")
        self.cli("schema", "import", self.schema_path)
        code, out = self.cli("adopt", "skin", "--where", "tags.type=skin", "-v")
        self.assertEqual(code, 0, out)
        self.assertIn("8 item(s) now use kind 'skin'", out)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                unittest.mock.patch("sys.stdin", io.StringIO()):
            code = cli.main(["-p", self.project_path, *argv])
        return code, out.getvalue()

    def export(self, name):
        path = os.path.join(self.dir, name)
        code, out = self.cli("export", "-o", path)
        self.assertEqual(code, 0, out)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def items(self):
        return {it["itemdefid"]: it for it in json.loads(self.export("now.json"))["items"]}

    def record(self, i):
        with open(self.project_path, encoding="utf-8") as f:
            return [r for r in json.load(f)["items"] if r["itemdefid"] == i][0]

    def test_adopt_keeps_export_byte_identical(self):
        self.assertEqual(self.export("after.json"), self.before)
        self.assertEqual(self.record(110), {"itemdefid": 110, "kind": "skin", "weapon": "pistol",
                                            "finish": "Red", "rarity": "common"})

    def test_query(self):
        code, out = self.cli("query", "-w", "rarity=epic", "-f", "id,name,weapon")
        self.assertEqual(code, 0, out)
        self.assertIn("116  Pistol | Flames  pistol", out)
        self.assertIn("2 item(s)", out)
        code, out = self.cli("query", "-w", "id>=116", "-w", "kind=skin", "-w", "name!~flames", "--format", "json", "-f", "id")
        self.assertEqual(json.loads(out), [{"id": 117}])
        code, out = self.cli("query", "-w", "type=generator", "--format", "csv", "-f", "id,bundle")
        self.assertIn("101,110;111;112", out)
        code, out = self.cli("query", "-w", "!kind", "-w", "series", "--dummies", "-f", "id")
        self.assertIn("118", out)

    def test_set_flavor_override_and_unset(self):
        code, out = self.cli("set", "110", "flavor=Painted red.")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.items()[110]["description"],
                         "Applies the Red appearance to the Pistol.\n\nPainted red.\n\nTest Series #1")
        code, out = self.cli("set", "110-111", "name_color=ffffff", "marketable:=false")
        self.assertIn("now overrides the skin rule", out)
        self.assertEqual(self.items()[111]["name_color"], "ffffff")
        self.assertIs(self.items()[111]["marketable"], False)
        self.cli("set", "--where", "id<=111", "--unset", "name_color", "--unset", "marketable")
        self.assertEqual(self.items()[111]["name_color"], "d2d2d2")
        code, out = self.cli("set", "110", "weapon=knife")
        self.assertEqual(code, 1)
        self.assertIn("not a row of table 'weapon'", out)
        code, out = self.cli("set", "110", "kind=other")
        self.assertEqual(code, 1)

    def test_tables(self):
        code, out = self.cli("table", "show", "weapon")
        self.assertIn("pistol   Pistol   3", out)
        self.cli("table", "set", "weapon", "pistol", "name=Handgun", "icon=hg")
        self.assertEqual(self.items()[113]["name"], "Handgun | Stripes")
        code, out = self.cli("table", "rename", "weapon", "pistol", "handgun")
        self.assertIn("updated 3 item(s)", out)
        self.assertEqual(self.record(110)["weapon"], "handgun")
        self.assertEqual(self.cli("table", "delete", "weapon", "handgun")[0], 1)
        self.cli("table", "new", "stickers", "name")
        self.cli("table", "set", "stickers", "dot", "name=Dot")
        self.assertEqual(self.cli("table", "delete", "stickers", "dot")[0], 0)

    def test_add_kind_item_and_detach(self):
        code, out = self.cli("add", "crate1", "--kind", "skin", "weapon=rifle", "finish=Gold", "rarity=common",
                             "--position", "4")
        self.assertEqual(code, 0, out)
        self.assertIn("'Rifle | Gold' becomes crate1 #4, itemdefid 113", out)
        items = self.items()
        self.assertEqual(items[113]["tags"], "type:skin;series:crate1;rarity:common;weapon:rifle")
        self.assertEqual(items[101]["bundle"], "110;111;112;113")
        code, out = self.cli("detach", "113")
        self.assertEqual(code, 0, out)
        self.assertNotIn("kind", self.record(113))
        self.assertEqual(self.items(), items)

    def test_release_mode_guards_table_edits(self):
        self.cli("mode", "release")
        code, out = self.cli("table", "set", "weapon", "pistol", "name=Handgun")
        self.assertEqual(code, 1)
        self.assertIn("#110  'Pistol | Red' -> 'Handgun | Red'", out)
        code, out = self.cli("set", "110", "flavor=Harmless")  # does not change the name
        self.assertEqual(code, 0, out)

    def test_schema_show_and_export(self):
        code, out = self.cli("schema")
        self.assertIn("kind skin  (8 item(s))", out)
        path = os.path.join(self.dir, "out-schema.json")
        self.cli("schema", "export", "-o", path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["tables"]["weapon"]["rows"]["rifle"], {"name": "Rifle"})


if __name__ == "__main__":
    unittest.main()
