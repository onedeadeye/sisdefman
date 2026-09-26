import contextlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock

from sisdefman import adopt, check, cli, colors, importer, ui
from sisdefman.project import Project, ProjectError

from tests import fixtures


class ColorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        self.before = self.project.build()

    def tearDown(self):
        self.tmp.cleanup()

    def built(self):
        return {it["itemdefid"]: it for it in self.project.build()}

    def test_references_are_resolved_on_export(self):
        self.project.colors.update(common="d2d2d2", bg="101010")
        rec = self.project.item(110)
        rec["name_color"] = "@common"
        rec["background_color"] = "@bg"
        rec["description"] = "@bg stays as it is outside colour fields"
        item = self.built()[110]
        self.assertEqual((item["name_color"], item["background_color"]), ("d2d2d2", "101010"))
        self.assertTrue(item["description"].startswith("@bg stays"))

        rec["name_color"] = "@nope"
        issues = [str(i) for i in check.check_project(self.project)]
        self.assertIn("error: [110] unknown colour @nope in name_color", issues)
        self.project.colors["bad"] = "12345"
        self.assertTrue(any("'bad': '12345' is not a six-digit hex colour" in i
                            for i in map(str, check.check_project(self.project))))

    def test_convert_names_colours_and_keeps_the_export(self):
        report = colors.convert(self.project)
        self.assertEqual(self.project.colors, {"background": "292929", "common": "d2d2d2", "uncommon": "5e90e0",
                                               "rare": "eb7ce9", "epic": "f08f35"})
        self.assertEqual(self.project.item(110)["name_color"], "@common")
        self.assertEqual(self.project.settings["dummy_item"]["name_color"], "@common")
        self.assertEqual(self.project.build(), self.before)
        self.assertEqual(report.replaced, 18)
        self.assertEqual(colors.convert(self.project).replaced, 0)  # nothing left

    def test_convert_uses_table_keys_and_existing_entries(self):
        self.project.data.update(fixtures.skin_schema())
        adopt.adopt(self.project, "skin", list(range(110, 118)))
        self.project.kinds["skin"]["derive"]["background_color"] = "292929"
        self.project.colors["dark"] = "292929"
        self.project.item(1)["name_color"] = "FFFFFF"
        before = self.project.build()
        report = colors.convert(self.project)
        self.assertEqual(report.reused, {"dark": "292929"})
        self.assertEqual(self.project.tables["rarity"]["rows"]["epic"]["color"], "@epic")
        self.assertEqual(self.project.kinds["skin"]["derive"]["background_color"], "@dark")
        self.assertEqual(self.project.colors["white"], "FFFFFF")  # spelling kept
        self.assertEqual(self.project.build(), before)
        # Adopting more items keeps working with references in the table.
        self.project.item(1)["name_color"] = "@white"
        self.assertEqual(self.built()[1]["name_color"], "FFFFFF")

    def test_rename_delete_and_set(self):
        colors.convert(self.project)
        self.assertEqual(colors.rename(self.project, "epic", "secret_rare"), 2)
        self.assertEqual(self.project.item(116)["name_color"], "@secret_rare")
        self.assertEqual(list(self.project.colors)[-1], "secret_rare")
        with self.assertRaises(ProjectError):
            colors.delete(self.project, "common")
        colors.set_color(self.project, "spare", "#ABCDEF")
        self.assertEqual(self.project.colors["spare"], "ABCDEF")
        colors.delete(self.project, "spare")
        for bad in (("x y", "123456"), ("ok", "12345g")):
            with self.assertRaises(ProjectError):
                colors.set_color(self.project, *bad)
        self.assertEqual(self.project.build(), self.before)

    def test_version_2_files_are_upgraded(self):
        path = os.path.join(self.tmp.name, "old.json")
        data = json.loads(self.project.to_json())
        data["sisdefman"] = 2
        del data["colors"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        loaded = Project.load(path)
        self.assertEqual((loaded.data["sisdefman"], loaded.colors), (3, {}))


class ColorCliTests(unittest.TestCase):
    def setUp(self):
        ui._enabled = False
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "p.json")
        self.cli("import", *fixtures.write_files(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                unittest.mock.patch("sys.stdin", io.StringIO()):
            code = cli.main(["-p", self.path, *argv])
        return code, out.getvalue()

    def test_commands(self):
        code, out = self.cli("colors")
        self.assertIn("18 colour value(s) are still hex", out)
        code, out = self.cli("colors", "convert")
        self.assertEqual(code, 0, out)
        self.assertIn("@common = d2d2d2 (new, 4 value(s))", out)
        code, out = self.cli("colors")
        self.assertIn("@common", out)
        self.assertIn("4 use(s)", out)
        self.assertEqual(self.cli("colors", "rename", "@epic", "secret")[0], 0)
        self.assertEqual(self.cli("colors", "delete", "secret")[0], 1)
        self.assertEqual(self.cli("colors", "set", "secret", "ff8800")[0], 0)
        code, out = self.cli("show", "116")
        self.assertIn('"name_color": "ff8800"', out)


if __name__ == "__main__":
    unittest.main()
