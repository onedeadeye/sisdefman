"""Lookup-table references in item fields: {weapon.name} with the row named
by the item's weapon field or weapon: tag."""

import tempfile
import unittest

from sisdefman import check, edits, importer, query
from sisdefman.project import ProjectError

from tests import fixtures


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        # Keys in another case than the items' tags, as in a CSV import.
        self.project.tables["weapon"] = {"columns": ["name", "Range"], "rows": {
            "Pistol": {"name": "Handgun", "Range": "SHORT"},
            "Rifle": {"name": "Long Rifle", "Range": "LONG"},
            "Shotgun": {"name": "Shotgun"},
        }}

    def tearDown(self):
        self.tmp.cleanup()

    def built(self, i):
        return {it["itemdefid"]: it for it in self.project.build()}[i]

    def issues(self):
        return [str(i) for i in check.check_project(self.project)]

    def test_plain_item_reads_the_row_named_by_its_tag(self):
        rec = self.project.item(110)
        rec["name"] = "{weapon.name} | Red"
        rec["icon_url"] = "https://example.com/{weapon}_{finish}.png"
        rec["finish"] = "red"
        item = self.built(110)
        self.assertEqual(item["name"], "Handgun | Red")
        self.assertEqual(item["icon_url"], "https://example.com/pistol_red.png")  # the tag's own spelling
        self.assertTrue(item["description"].endswith("Test Series #1"))
        self.assertEqual([i for i in self.issues() if "[110]" in i], [])

    def test_a_field_of_the_same_name_comes_before_the_tag(self):
        rec = self.project.item(110)
        rec.update(name="{weapon.name} | Red", weapon="rifle")
        self.assertEqual(self.built(110)["name"], "Long Rifle | Red")

    def test_other_text_in_braces_is_left_alone(self):
        rec = self.project.item(999999)
        rec["description"] = "Keep {this} and {contents}; the ID is {itemdefid}."
        self.assertEqual(self.built(999999)["description"], "Keep {this} and {contents}; the ID is 999999.")
        self.assertIn("warning: [999999] description contains {this}, which is not filled in here, so players "
                      "would see it as it is", self.issues())
        self.assertTrue(self.built(1)["description"].count("\n") > 3)  # the crate list is still generated
        self.project.item(111)["tags"] = "type:skin;series:crate1;weapon:{weapon}"  # never a template
        self.assertEqual(self.built(111)["tags"], "type:skin;series:crate1;weapon:{weapon}")

    def test_problems_are_reported_and_the_text_kept(self):
        self.project.item(999999)["name"] = "{weapon.name} drops"
        self.project.item(110)["name"] = "{weapon.Nope} | Red"
        self.project.item(116)["name"] = "{weapon.Range} | Flames"  # the Pistol row has one...
        self.project.item(117)["name"] = "{weapon.Range} | Sparks"  # ...and so does Rifle
        self.project.item(115)["name"] = "{weapon.Range} | Cracks"  # Shotgun has none
        self.assertEqual(self.built(999999)["name"], "{weapon.name} drops")
        found = self.issues()
        self.assertIn("warning: [999999] name: {weapon}: this item has no weapon field or weapon: tag to choose a "
                      "row of table 'weapon'", found)
        self.assertIn("warning: [110] name: table 'weapon' has no column 'Nope'", found)
        self.assertIn("warning: [115] name: table 'weapon': row 'shotgun' has no Range", found)
        self.assertEqual(self.built(116)["name"], "SHORT | Flames")
        self.assertFalse([i for i in found if "not filled in here" in i])  # no second warning for the same thing

    def test_a_bare_key_in_visible_text_is_flagged(self):
        self.project.item(110)["name"] = "{weapon} | Red"
        self.assertIn("warning: [110] name uses {weapon}, which is a row key of table 'weapon' (such as "
                      "'Pistol'); use a column such as {weapon.name} for text players read", self.issues())

    def test_kinds_can_use_tags_and_overrides_can_use_references(self):
        self.project.kinds["skin"] = {
            "fields": {"finish": {"type": "text"}},
            "derive": {"type": "item", "name": "{weapon.name} | {finish}",
                       "tags": "type:skin;series:{series};rarity:common;weapon:{w}"},
        }
        self.project.kinds["skin"]["fields"]["w"] = {"type": "text"}
        recs = self.project.items
        at = [r["itemdefid"] for r in recs].index(110)
        recs[at] = {"itemdefid": 110, "kind": "skin", "finish": "Red", "w": "rifle"}
        self.assertEqual(self.built(110)["name"], "Long Rifle | Red")  # from the derived tag
        recs[at]["name"] = "{weapon.name} (special)"  # an override
        self.assertEqual(self.built(110)["name"], "Long Rifle (special)")

    def test_ref_fields_match_keys_ignoring_case(self):
        self.project.data.update({"kinds": fixtures.skin_schema()["kinds"]})
        self.project.tables["rarity"] = {"columns": ["color"], "rows": {"common": {"color": "d2d2d2"}}}
        recs = self.project.items
        at = [r["itemdefid"] for r in recs].index(110)
        recs[at] = {"itemdefid": 110, "kind": "skin", "weapon": "pistol", "finish": "Red", "rarity": "common"}
        item = self.built(110)
        self.assertEqual(item["name"], "Handgun | Red")
        self.assertEqual([i for i in self.issues() if "[110]" in i], [])

    def test_row_usage_rename_and_delete(self):
        self.project.item(110)["name"] = "{weapon.name} | Red"
        self.project.item(111).update(name="{weapon.name} | Blue", weapon="Rifle")
        self.assertEqual(edits.rows_in_use(self.project, "weapon"), {"Pistol": [110], "Rifle": [111]})
        with self.assertRaises(ProjectError):
            edits.delete_row(self.project, "weapon", "Pistol")
        with self.assertRaises(ProjectError) as e:
            edits.rename_row(self.project, "weapon", "Pistol", "handgun")  # 110's tag says pistol
        self.assertIn("by their weapon: tag", str(e.exception))
        edits.rename_row(self.project, "weapon", "Pistol", "pistol")  # still matches the tag
        self.assertEqual(self.built(110)["name"], "Handgun | Red")
        edits.rename_row(self.project, "weapon", "Rifle", "longrifle")  # a field: renamed along
        self.assertEqual(self.project.item(111)["weapon"], "longrifle")
        self.assertEqual(self.built(111)["name"], "Long Rifle | Blue")
        with self.assertRaises(ProjectError):
            edits.delete_table(self.project, "weapon")

    def test_queries_can_use_the_row(self):
        rows = query.select(self.project, ["weapon.Range=LONG"])
        self.assertEqual([r["id"] for r in rows], [111, 114, 117])


if __name__ == "__main__":
    unittest.main()
