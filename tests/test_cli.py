import contextlib
import unittest.mock
import io
import json
import os
import tempfile
import unittest

from sisdefman import cli, ui

from tests import fixtures


class CliTests(unittest.TestCase):
    def setUp(self):
        ui._enabled = False
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.files = fixtures.write_files(self.dir)
        self.project_path = os.path.join(self.dir, "project.json")
        self.export_path = os.path.join(self.dir, "out.json")
        self.assertEqual(self.run_cli("import", *self.files)[0], 0)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO()  # not a terminal: confirmations are refused
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                unittest.mock.patch("sys.stdin", stdin):
            code = cli.main(["-p", self.project_path, *argv])
        return code, out.getvalue() + err.getvalue()

    def project(self):
        with open(self.project_path, encoding="utf-8") as f:
            return json.load(f)

    def exported(self):
        with open(self.export_path, encoding="utf-8") as f:
            return {it["itemdefid"]: it for it in json.load(f)["items"]}

    def test_export(self):
        code, out = self.run_cli("export", "-o", self.export_path)
        self.assertEqual(code, 0, out)
        items = self.exported()
        self.assertEqual(len(items), len(fixtures.all_items()))
        self.assertTrue(items[110]["description"].endswith("Test Series #1"))
        with open(self.export_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["appid"], fixtures.APPID)

    def test_prerelease_insert_needs_no_confirmation(self):
        code, out = self.run_cli("add", "crate1", "--position", "1", "--set", "name=New | Thing",
                                 "--set", "tags=type:skin;rarity:common", "--set-json", "tradable=true")
        self.assertEqual(code, 0, out)
        self.assertIn("Renumbered 8 item(s): 110-117 -> 111-118", out)
        item = [it for it in self.project()["items"] if it["itemdefid"] == 110][0]
        self.assertEqual(item["name"], "New | Thing")
        self.assertIs(item["tradable"], True)

    def test_release_mode_guards_destructive_changes(self):
        self.assertEqual(self.run_cli("mode", "release")[0], 0)
        before = self.project()

        code, out = self.run_cli("add", "crate1", "--position", "1", "--set", "name=New")
        self.assertEqual(code, 1)
        self.assertIn("RELEASE MODE", out)
        self.assertIn("#110  'Pistol | Red' -> 'New'", out)
        self.assertEqual(self.project(), before)

        code, out = self.run_cli("add", "crate1", "--position", "1", "--set", "name=New", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.project(), before)

        # Appending is safe and needs no confirmation.
        code, out = self.run_cli("add", "crate1", "--like", "117", "--set", "name=Rifle | Late")
        self.assertEqual(code, 0, out)
        self.assertNotIn("RELEASE MODE", out)

        code, out = self.run_cli("move", "110", "--position", "2", "--accept-inventory-changes")
        self.assertEqual(code, 0, out)
        self.assertIn("RELEASE MODE", out)

        # The export compares with what is live and refuses without confirmation.
        code, out = self.run_cli("export", "-o", self.export_path)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.export_path))
        code, out = self.run_cli("export", "-o", self.export_path, "--accept-inventory-changes", "--mark-live")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("diff")
        self.assertIn("no changes to what players can own", out)

    def test_leaving_release_mode_needs_confirmation(self):
        self.run_cli("mode", "release")
        self.assertEqual(self.run_cli("mode", "prerelease")[0], 1)
        self.assertEqual(self.project()["mode"], "release")
        self.assertEqual(self.run_cli("mode", "prerelease", "--accept-inventory-changes")[0], 0)
        self.assertEqual(self.project()["mode"], "prerelease")

    def test_remove_and_list(self):
        code, out = self.run_cli("remove", "112")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("list", "crate1")
        self.assertIn("117  (dummy)", out)
        self.assertNotIn("Shotgun | Green", out)

    def test_series_commands(self):
        code, out = self.run_cli("series", "new", "crate2", "--first-id", "210", "--last-id", "296",
                                 "--name", "Second Series")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("series", "new", "clash", "--first-id", "150", "--last-id", "160")
        self.assertEqual(code, 1)
        code, out = self.run_cli("series", "new", "swallow", "--first-id", "100", "--last-id", "105")
        self.assertEqual(code, 1)
        self.assertIn("not tagged series:swallow", out)
        code, out = self.run_cli("series", "set", "crate2", "--container", "1")
        self.assertEqual(code, 1)
        self.assertIn("already belongs to series 'crate1'", out)
        code, out = self.run_cli("add", "crate2", "--set", "name=Two | One")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("series", "set", "crate2", "--template", "{name}\\n{series_name} {index}/{count}")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("show", "210")
        self.assertIn('"description": "Two | One\\nSecond Series 1/1"', out)
        code, out = self.run_cli("series", "set", "crate2", "--last-id", "300")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("series")
        self.assertIn("IDs 210-300", out)

    def test_diff_against_file(self):
        code, out = self.run_cli("diff", "--against", self.files[0], "-v")
        self.assertEqual(code, 0, out)
        self.assertIn("~ 110  Pistol | Red: description", out)
        self.assertIn("+ 109", out)

    def test_check_and_template(self):
        code, out = self.run_cli("check")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("template", "{description} ({series_name} no. {index:02d})")
        self.assertEqual(code, 0, out)
        self.assertIn("(Test Series no. 01)", out)
        code, out = self.run_cli("template", "{nope}")
        self.assertEqual(code, 1)
        self.assertIn("unknown field", out)

    def test_errors_are_reported(self):
        code, out = self.run_cli("show", "4242")
        self.assertEqual(code, 1)
        self.assertIn("error:", out)
        code, out = cli.main(["-p", os.path.join(self.dir, "missing.json"), "check"]), ""
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
