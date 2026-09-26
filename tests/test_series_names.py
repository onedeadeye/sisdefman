"""Series display names: the key never stands in for one in exported text."""

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


def write(directory, name, doc):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return path


def errors(project):
    return [str(i) for i in check.check_project(project) if i.level == "error"]


class SeriesNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def unnamed_crate_file(self):
        doc = fixtures.crate_file()
        doc["items"][0]["description"] = doc["items"][0]["description"].replace(" from the Test Series", "")
        return doc

    def test_import_without_a_name_leaves_it_unset_and_blocks_export(self):
        project, report = importer.import_files([write(self.dir, "c.json", self.unnamed_crate_file())])
        self.assertEqual(project.series["crate1"]["name"], "")
        self.assertTrue(any("no display name found" in text for _, text in report.lines))
        self.assertEqual(errors(project), [
            "error: series 'crate1' has no display name, so the text of 110-117 would show its key 'crate1'. "
            "Set one on the Series setup page or with `sisdefman series set crate1 --name NAME`."])
        # The exported text still shows the key, never the internal marker.
        built = {it["itemdefid"]: it for it in project.build()}
        self.assertTrue(built[110]["description"].endswith("\n\ncrate1 #1"))
        edits.save_series(project, "crate1", {"name": "Test Series"}, is_new=False)
        self.assertEqual(errors(project), [])

    def test_name_used_by_a_kind_or_a_crate_is_caught_too(self):
        project, _ = importer.import_files([write(self.dir, "c.json", self.unnamed_crate_file())])
        project.settings["description_template"] = "{description}"
        self.assertEqual(errors(project), [])
        self.assertIn("note: series 'crate1' has no display name (none of its text uses it yet)",
                      [str(i) for i in check.check_project(project)])
        project.item(1)["description"] += "\n\nFrom the {series_name}."
        self.assertEqual(len(errors(project)), 1)
        self.assertIn("the text of 1 would show", errors(project)[0])
        project.item(1)["description"] = "{contents}"
        project.kinds["k"] = {"fields": {}, "derive": {"type": "item", "name": "X",
                                                        "display_type": "{series.name} skin", "tags": "series:crate1"}}
        project.items[[r["itemdefid"] for r in project.items].index(110)] = {"itemdefid": 110, "kind": "k"}
        self.assertEqual(len(errors(project)), 1)
        self.assertIn("the text of 110 would show", errors(project)[0])

    def test_name_comes_from_existing_series_lines(self):
        project, _ = importer.import_files([write(self.dir, "c.json", self.unnamed_crate_file())])
        project.series["crate1"]["name"] = "Test Series"
        exported = write(self.dir, "export.json", project.export_document())
        again, report = importer.import_files([exported])
        self.assertEqual(again.series["crate1"]["name"], "Test Series")
        self.assertEqual(again.item(110)["description"], "Applies the Red appearance to the Pistol.")
        self.assertEqual(again.build(), project.build())

    def test_key_in_series_lines_is_not_taken_as_the_name(self):
        project, _ = importer.import_files([write(self.dir, "c.json", self.unnamed_crate_file())])
        exported = write(self.dir, "export.json", project.export_document())  # lines say "crate1 #1"
        again, report = importer.import_files([exported])
        self.assertEqual(again.series["crate1"]["name"], "")
        self.assertTrue(any("show the key 'crate1' instead" in text for _, text in report.lines))
        self.assertEqual(again.item(110)["description"], "Applies the Red appearance to the Pistol.")

    def test_series_lines_of_another_template_are_recognised(self):
        project, _ = importer.import_files(fixtures.write_files(self.dir))
        project.settings["description_template"] = "{description}\n\n{series_name} #{index:02d}/{count_no_secret}"
        exported = write(self.dir, "export.json", project.export_document())
        self.assertTrue(project.build()[8]["description"].endswith("Test Series #01/6"))
        again, report = importer.import_files([exported])
        self.assertEqual(again.settings["description_template"], project.settings["description_template"])
        self.assertEqual(again.item(110)["description"], "Applies the Red appearance to the Pistol.")
        self.assertEqual(again.build(), project.build())
        # Merging into a project with another template sets the series' own template.
        base = Project.new(fixtures.APPID)
        merged, _ = importer.import_files([exported], base)
        self.assertEqual(merged.series["crate1"]["description_template"],
                         project.settings["description_template"])
        self.assertEqual(merged.build(), project.build())

    def test_new_series_need_a_name(self):
        project, _ = importer.import_files(fixtures.write_files(self.dir))
        with self.assertRaises(ProjectError):
            edits.save_series(project, "crate2", {"first_id": 210, "last_id": 296}, is_new=True)
        edits.save_series(project, "crate2", {"name": " Second ", "first_id": 210, "last_id": 296}, is_new=True)
        self.assertEqual(project.series["crate2"]["name"], "Second")

    def test_older_files_lose_the_key_placeholder(self):
        project, _ = importer.import_files(fixtures.write_files(self.dir))
        data = json.loads(project.to_json())
        data["sisdefman"] = 3
        data["series"]["crate1"]["name"] = "crate1"  # what earlier versions stored for "no name"
        path = write(self.dir, "old.json", data)
        self.assertEqual(Project.load(path).series["crate1"]["name"], "")
        ui._enabled = False
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                unittest.mock.patch("sys.stdin", io.StringIO()):
            code = cli.main(["-p", path, "export", "-o", os.path.join(self.dir, "x.json")])
        self.assertEqual(code, 1)
        self.assertIn("has no display name", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "x.json")))


if __name__ == "__main__":
    unittest.main()
