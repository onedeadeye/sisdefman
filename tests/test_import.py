import json
import os
import tempfile
import unittest

from sisdefman import check, importer
from sisdefman.project import CONTENTS_TOKEN, Project, ProjectError

from tests import fixtures


def write(directory, name, doc):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return path


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.paths = fixtures.write_files(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_series_structure(self):
        project, _ = importer.import_files(self.paths)
        self.assertEqual(project.appid, fixtures.APPID)
        s = project.series["crate1"]
        self.assertEqual(s["name"], "Test Series")
        self.assertEqual((s["first_id"], s["last_id"], s["allocated_through"]), (110, 196, 119))
        self.assertEqual(s["containers"], {"1": {"exclude": ["rarity:epic"]}})
        self.assertEqual(s["generators"], {
            "101": "rarity:common", "102": "rarity:uncommon", "103": "rarity:rare", "104": "rarity:epic",
        })
        self.assertEqual([m["itemdefid"] for m in project.members("crate1")], list(range(110, 118)))
        # Dummies are regenerated, not stored.
        self.assertIsNone(project.item(118))
        self.assertIn(CONTENTS_TOKEN, project.item(1)["description"])

    def test_round_trip_without_series_text(self):
        project, _ = importer.import_files(self.paths)
        project.settings["description_template"] = "{description}"
        built = {it["itemdefid"]: it for it in project.build()}
        original = fixtures.all_items()
        self.assertEqual(built, original)
        for i in original:
            self.assertEqual(list(built[i]), list(original[i]), f"key order of {i}")

    def test_series_text_in_descriptions(self):
        project, _ = importer.import_files(self.paths)
        built = {it["itemdefid"]: it for it in project.build()}
        self.assertEqual(built[110]["description"],
                         "Applies the Red appearance to the Pistol.\n\nTest Series #1")
        self.assertTrue(built[117]["description"].endswith("\n\nTest Series #8"))
        # Only series items get it.
        self.assertNotIn("#", built[101].get("description", ""))

    def test_reimporting_an_export_strips_series_text(self):
        project, _ = importer.import_files(self.paths)
        exported = write(self.dir, "export.json", project.export_document())
        again, _ = importer.import_files([exported])
        self.assertEqual(again.item(110)["description"], "Applies the Red appearance to the Pistol.")
        self.assertEqual(again.build(), project.build())

    def test_stale_container_list_is_reported(self):
        doc = fixtures.crate_file()
        crate = doc["items"][0]
        crate["description"] = crate["description"].replace("Rifle | Camo", "Rifle | Old Name")
        path = write(self.dir, "crate.json", doc)
        project, report = importer.import_files([path, self.paths[1]])
        warnings = "\n".join(text for level, text in report.lines if level == "warn")
        self.assertIn("- Rifle | Old Name", warnings)
        self.assertIn("+ Rifle | Camo", warnings)
        self.assertEqual(project.series["crate1"]["containers"]["1"], {"exclude": ["rarity:epic"]})
        built = {it["itemdefid"]: it for it in project.build()}
        self.assertIn("Rifle | Camo", built[1]["description"])

    def test_generator_that_is_not_a_tag_rule_is_left_alone(self):
        doc = fixtures.crate_file()
        doc["items"].append({"itemdefid": 150, "type": "generator", "bundle": "110;113", "name": "Mixed"})
        project, _ = importer.import_files([write(self.dir, "c.json", doc)])
        # 150 is outside the series' items, but inside its ID range, so it limits the range.
        self.assertEqual(project.series["crate1"]["last_id"], 149)
        self.assertNotIn("150", project.series["crate1"]["generators"])

    def untag_113(self):
        doc = fixtures.crate_file()
        for it in doc["items"]:
            if it["itemdefid"] == 113:
                it["tags"] = "type:skin"
        return write(self.dir, "c.json", doc)

    def test_untagged_item_inside_run_joins_the_series_with_a_warning(self):
        project, report = importer.import_files([self.untag_113()])
        s = project.series["crate1"]
        self.assertEqual((s["first_id"], s["last_id"]), (110, 196))
        self.assertEqual([m["itemdefid"] for m in project.members("crate1")], list(range(110, 118)))
        warnings = "\n".join(text for level, text in report.lines if level == "warn")
        self.assertIn("the definitions at 113 sit between its items", warnings)
        issues = [str(i) for i in check.check_project(project)]
        self.assertIn("warning: [113] is in series 'crate1' but not tagged series:crate1", issues)

    def test_release_mode_keeps_the_restriction(self):
        project = Project.new(fixtures.APPID)
        project.mode = "release"
        project, report = importer.import_files([self.untag_113()], project)
        self.assertNotIn("crate1", project.series)
        self.assertTrue(any("release mode it is not set up" in text for _, text in report.lines))

    def test_far_away_tagged_item_is_left_out(self):
        doc = fixtures.crate_file()
        doc["items"][0]["description"] = "A crate that does not list its items yet."
        project, report = importer.import_files([write(self.dir, "c.json", doc)])
        s = project.series["crate1"]
        self.assertEqual((s["first_id"], s["containers"]), (110, {}))
        warnings = "\n".join(text for level, text in report.lines if level == "warn")
        self.assertIn("1 is tagged series:crate1 but far from its other items (IDs 110-117)", warnings)
        self.assertIn("--container ID", warnings)

    def test_gaps_are_reported(self):
        doc = fixtures.crate_file()
        doc["items"] = [it for it in doc["items"] if it["itemdefid"] != 113]
        doc["items"][0]["description"] = doc["items"][0]["description"].replace("Pistol | Stripes\n", "")
        project, report = importer.import_files([write(self.dir, "c.json", doc)])
        warnings = "\n".join(text for level, text in report.lines if level == "warn")
        self.assertIn("IDs 113 inside the series are unused", warnings)

    def test_merging_into_an_existing_project(self):
        project, _ = importer.import_files(self.paths[:1])
        self.assertIsNone(project.item(999999))
        project, _ = importer.import_files(self.paths[1:], project)
        self.assertIsNotNone(project.item(999999))
        with self.assertRaises(ProjectError):
            importer.import_files(self.paths[1:], project)
        project, report = importer.import_files(self.paths[1:], project, replace=True)
        self.assertTrue(any("replaced" in text for _, text in report.lines))

    def test_rejects_duplicates_and_mixed_apps(self):
        with self.assertRaises(ProjectError):
            importer.import_files([self.paths[0], self.paths[0]])
        other = write(self.dir, "other.json", {"appid": 1, "items": [{"itemdefid": 5, "type": "item"}]})
        with self.assertRaises(ProjectError):
            importer.import_files([self.paths[0], other])

    def test_imported_project_checks_clean(self):
        project, _ = importer.import_files(self.paths)
        issues = check.check_project(project)
        self.assertEqual([i for i in issues if i.level != "note"], [])

    def test_save_and_load(self):
        project, _ = importer.import_files(self.paths)
        path = os.path.join(self.dir, "p.json")
        project.save(path)
        loaded = Project.load(path)
        self.assertEqual(loaded.build(), project.build())
        with open(path, encoding="utf-8") as f:
            self.assertIn('"exclude": ["rarity:epic"]', f.read())


if __name__ == "__main__":
    unittest.main()
