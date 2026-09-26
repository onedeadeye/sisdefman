"""Upgrading format 4 projects: the series line setting becomes part of the
kinds and items, and the export stays the same."""

import contextlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock

from sisdefman import adopt, cli, edits, importer, migrate, ui
from sisdefman.project import FORMAT_VERSION, Project

from tests import fixtures


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        project.data.update(fixtures.skin_schema())
        adopt.adopt(project, "skin", list(range(110, 118)))
        # A skin outside any series, and a second series with an item without a kind.
        project.items.append({"itemdefid": 600, "kind": "skin", "weapon": "pistol", "finish": "Gold",
                              "rarity": "common"})
        edits.save_series(project, "crate2", {"name": "Second", "first_id": 210, "last_id": 296}, is_new=True)
        project.items.append({"itemdefid": 210, "type": "item", "name": "Plain", "description": "Plain.",
                              "tags": "series:crate2"})
        self.data = json.loads(project.to_json())

    def tearDown(self):
        self.tmp.cleanup()

    def old(self, template, per_series=None):
        data = json.loads(json.dumps(self.data))
        data["sisdefman"] = 4
        data["settings"]["description_template"] = template
        for key, t in (per_series or {}).items():
            data["series"][key]["description_template"] = t
        return Project(data)

    def built(self, project):
        return {it["itemdefid"]: it for it in project.build()}

    def test_series_line_moves_into_the_kind_and_the_items(self):
        project = self.old("{description}\n\n{series_name} #{index}/{count_no_secret}",
                           {"crate2": "{series_name}: {description}"})
        self.assertEqual(project.data["sisdefman"], FORMAT_VERSION)
        self.assertNotIn("description_template", project.settings)
        self.assertEqual(project.kinds["skin"]["derive"]["description"],
                         ["Applies the {finish} appearance to the {weapon.name}.", "{flavor}",
                          "{series.name} #{series.index}/{series.count_no_secret}"])
        self.assertEqual(project.item(210)["description"], "{series.name}: Plain.")
        built = self.built(project)
        self.assertEqual(built[110]["description"], "Applies the Red appearance to the Pistol.\n\nTest Series #1/6")
        self.assertEqual(built[117]["description"], "Applies the Sparks appearance to the Rifle.\n\nTest Series #8/6")
        self.assertEqual(built[600]["description"], "Applies the Gold appearance to the Pistol.")  # in no series
        self.assertEqual(built[210]["description"], "Second: Plain.")
        self.assertTrue(project.upgrade_notes)

    def test_items_with_their_own_description_and_other_lines(self):
        data = json.loads(json.dumps(self.data))
        data["items"] = [dict(r, description="Custom.") if r["itemdefid"] == 111 else r for r in data["items"]]
        edits_ = {"sisdefman": 4}
        data.update(edits_)
        data["settings"]["description_template"] = "{description} ({series_name} {index:02d})"
        project = Project(data)
        built = self.built(project)
        self.assertEqual(built[111]["description"], "Custom. (Test Series 02)")
        self.assertEqual(built[110]["description"], "Applies the Red appearance to the Pistol. (Test Series 01)")
        self.assertEqual(built[600]["description"], "Applies the Gold appearance to the Pistol.")

    def test_nothing_to_move(self):
        project = self.old("{description}")
        self.assertEqual(project.kinds["skin"]["derive"]["description"],
                         ["Applies the {finish} appearance to the {weapon.name}.", "{flavor}"])
        self.assertEqual(project.upgrade_notes, [])
        self.assertEqual(self.built(project)[110]["description"], "Applies the Red appearance to the Pistol.")

    def test_old_names_become_references(self):
        self.assertEqual(migrate.convert("{series_name} #{index:03d} of {count}; {series} {name} {{x}}"),
                         "{series.name} #{series.index:03d} of {series.count}; {series} {name} {{x}}")
        self.assertEqual(migrate.convert("{{x}} {count_secret}", stored=True), "{x} {series.count_secret}")

    def test_the_upgrade_is_saved_with_a_copy_of_the_old_file(self):
        path = os.path.join(self.tmp.name, "sisdefman.json")
        data = json.loads(json.dumps(self.data))
        data["sisdefman"] = 4
        data["settings"]["description_template"] = "{description}\n\n{series_name} #{index}"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        ui._enabled = False
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                unittest.mock.patch("sys.stdin", io.StringIO()):
            code = cli.main(["-p", path, "show", "110"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("Upgraded sisdefman.json from format 4", out.getvalue())
        self.assertIn("Test Series #1", out.getvalue())
        with open(os.path.join(self.tmp.name, "sisdefman.v4.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["sisdefman"], 4)
        self.assertEqual(Project.load(path).data["sisdefman"], FORMAT_VERSION)


class ParagraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_paragraph_with_an_empty_value_is_left_out(self):
        self.project.kinds["k"] = {"fields": {"place": {"type": "text", "optional": True}}, "derive": {
            "type": "item", "name": "X",
            "description": ["Always.", "Found in {place}.", "{series.name} #{series.index}"]}}
        recs = self.project.items
        recs[[r["itemdefid"] for r in recs].index(110)] = {"itemdefid": 110, "kind": "k"}
        recs.append({"itemdefid": 600, "kind": "k", "place": "the jungle"})
        built = {it["itemdefid"]: it for it in self.project.build()}
        self.assertEqual(built[110]["description"], "Always.\n\nTest Series #1")
        self.assertEqual(built[600]["description"], "Always.\n\nFound in the jungle.")

    def test_crates_can_use_series_references(self):
        crate = self.project.item(1)
        crate["description"] = "{series.count_no_secret} + {series.count_secret} from the {series.name}.\n\n{contents}"
        built = {it["itemdefid"]: it for it in self.project.build()}
        self.assertTrue(built[1]["description"].startswith("6 + 2 from the Test Series.\n\nPistol | Red"))


if __name__ == "__main__":
    unittest.main()
