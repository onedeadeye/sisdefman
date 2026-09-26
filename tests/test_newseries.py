import contextlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock

from sisdefman import check, cli, edits, importer, ui
from sisdefman.project import Project, ProjectError

from tests import fixtures


class NewSeriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def copy(self, key="crate2", name="Second Series", first=210, last=296, **changes):
        plan = edits.series_copy_plan(self.project, "crate1", key, name, first)
        setup = {
            "source": "crate1",
            "ids": [c["id"] for c in plan["candidates"] if c["copy"]],
            "new_ids": {c["id"]: c["new_id"] for c in plan["candidates"]},
            "replacements": plan["replacements"],
        }
        setup.update(changes)
        return edits.create_series(self.project, key, {"name": name, "first_id": first, "last_id": last}, setup)

    def built(self):
        return {it["itemdefid"]: it for it in self.project.build()}

    def test_suggestion_follows_the_last_series(self):
        self.assertEqual(edits.suggest_series(self.project),
                         {"key": "crate2", "first_id": 210, "last_id": 296, "source": "crate1"})
        self.copy()
        self.assertEqual(edits.suggest_series(self.project),
                         {"key": "crate3", "first_id": 310, "last_id": 396, "source": "crate2"})
        self.assertEqual(edits.suggest_series(Project.new(fixtures.APPID))["key"], "series1")

    def test_plan(self):
        plan = edits.series_copy_plan(self.project, "crate1", "crate2", "Second Series", 210)
        by_id = {c["id"]: c for c in plan["candidates"]}
        self.assertEqual(sorted(by_id), [1, 100, 101, 102, 103, 104, 105, 109, 197, 198, 199])
        # The crate gets the next free ID; the rest keep their place in the block.
        self.assertEqual((by_id[1]["new_id"], by_id[1]["role"]), (2, "container"))
        self.assertEqual((by_id[101]["new_id"], by_id[101]["role"]), (201, "generator"))
        self.assertEqual(by_id[199]["new_id"], 299)
        self.assertTrue(all(c["copy"] for c in plan["candidates"]))
        self.assertEqual(plan["replacements"], [["Test Series", "Second Series"], ["Test", "Second"]])

    def test_definitions_used_outside_the_series_are_not_copied_by_default(self):
        self.project.items.append({"itemdefid": 500, "type": "generator", "bundle": "197", "name": "Elsewhere"})
        by_id = {c["id"]: c for c in edits.series_copy_plan(self.project, "crate1", "crate2", "", 210)["candidates"]}
        self.assertFalse(by_id[197]["copy"])
        self.assertIn("shared", by_id[197]["note"])

    def test_copy(self):
        report = self.copy()
        self.assertEqual([(c["old"], c["new"]) for c in report["created"]][:3], [(1, 2), (100, 200), (101, 201)])
        s = self.project.series["crate2"]
        self.assertEqual(s["containers"], {"2": {"exclude": ["rarity:epic"]}})
        self.assertEqual(s["generators"], {"201": "rarity:common", "202": "rarity:uncommon",
                                           "203": "rarity:rare", "204": "rarity:epic"})
        built = self.built()
        self.assertEqual(built[2]["name"], "Second Crate")
        self.assertEqual(built[2]["tags"], "type:lootbox;series:crate2")
        self.assertIn("from the Second Series.", built[2]["description"])
        # References follow the copies; quantities (x199) do not.
        self.assertEqual(built[200]["exchange"], "2")
        self.assertEqual(built[200]["bundle"], "298x199;299x1")
        self.assertEqual(built[297]["bundle"], "201x1000;202x200;203x40;204x8")
        self.assertEqual(built[299]["tag_generators"], "209")
        self.assertEqual(built[205]["exchange"], "series:crate2*5,rarity:common*5")
        # The source is untouched.
        self.assertEqual(built[1]["name"], "Test Crate")
        self.assertEqual(built[100]["bundle"], "198x199;199x1")
        self.assertEqual(built[105]["exchange"], "series:crate1*5,rarity:common*5")

    def test_copied_series_fills_up_as_items_are_added(self):
        self.copy()
        item = fixtures.skin(210, "Pistol | Gold", "common", "pistol", series="crate2")
        self.project.items.append(item)
        built = self.built()
        self.assertEqual(built[201]["bundle"], "210")
        self.assertIn("Pistol | Gold", built[2]["description"])
        self.assertTrue(built[210]["description"].endswith("Second Series #1"))
        self.assertEqual([i for i in check.check_project(self.project) if i.level == "error"], [])

    def test_chosen_ids_and_new_ids(self):
        self.copy(ids=[1, 101], new_ids={1: 5, 101: 300})
        self.assertEqual(self.project.series["crate2"]["containers"], {"5": {"exclude": ["rarity:epic"]}})
        self.assertEqual(self.project.series["crate2"]["generators"], {"300": "rarity:common"})
        self.assertIsNone(self.project.item(200))

    def test_refusals(self):
        before = json.dumps(self.project.data, sort_keys=True)
        for changes in ({"new_ids": {1: 100}},  # already used
                        {"new_ids": {1: 250}},  # inside the new series' range
                        {"new_ids": {1: 200}},  # two copies on 200
                        {"ids": [1, 110]}):  # a series item
            with self.assertRaises(ProjectError, msg=changes):
                self.copy(**changes)
        with self.assertRaises(ProjectError):
            self.copy(first=150, last=180)  # overlaps crate1
        self.assertEqual(json.dumps(self.project.data, sort_keys=True), before)

    def test_empty_series(self):
        report = edits.create_series(self.project, "crate2", {"name": "Second Series", "first_id": 210,
                                                              "last_id": 296})
        self.assertEqual(report["created"], [])
        self.assertEqual(self.project.series["crate2"]["containers"], {})


class NewSeriesCommandTests(unittest.TestCase):
    def setUp(self):
        ui._enabled = False
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "project.json")
        self.cli("import", *fixtures.write_files(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                unittest.mock.patch("sys.stdin", io.StringIO()):
            code = cli.main(["-p", self.path, *argv])
        return code, out.getvalue()

    def test_dry_run_then_copy(self):
        code, out = self.cli("series", "new", "crate2", "--name", "Second Series", "--copy-from", "crate1", "-n")
        self.assertEqual(code, 0, out)
        self.assertIn("IDs 210-296, copied from 'crate1'", out)
        self.assertIn("1 -> 2", out)
        self.assertIn("'Test Series' -> 'Second Series'", out)
        self.assertNotIn("crate2", Project.load(self.path).series)

        code, out = self.cli("series", "new", "crate2", "--name", "Second Series", "--copy-from", "crate1",
                             "--copy-ids", "1,101-104", "--new-id", "1=7", "--replace", "Crate=Box",
                             "--secret", "rarity:epic")
        self.assertEqual(code, 0, out)
        project = Project.load(self.path)
        s = project.series["crate2"]
        self.assertEqual((s["first_id"], s["last_id"]), (210, 296))
        self.assertEqual(list(s["containers"]), ["7"])
        self.assertEqual(sorted(s["generators"]), ["201", "202", "203", "204"])
        self.assertEqual(s["secret"], "rarity:epic")
        self.assertEqual(project.item(7)["name"], "Second Box")
        self.assertIsNone(project.item(200))

    def test_empty_series_dry_run(self):
        code, out = self.cli("series", "new", "crate2", "--name", "Second", "-n")
        self.assertEqual(code, 0, out)
        self.assertIn("Dry run", out)
        self.assertNotIn("crate2", Project.load(self.path).series)
        code, out = self.cli("series", "new", "crate2", "--name", "Second")
        self.assertEqual(code, 0, out)
        self.assertEqual(Project.load(self.path).series["crate2"]["first_id"], 210)


if __name__ == "__main__":
    unittest.main()
