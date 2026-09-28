"""Crates (a series' containers) as items of a kind."""

import tempfile
import unittest

from sisdefman import adopt, check, edits, importer
from sisdefman.project import ProjectError

from tests import fixtures


def crate_kind():
    return {
        "fields": {
            "title": {"type": "text"},
            "origin": {"type": "text", "optional": True, "default": "from the {series.name}"},
            "closing": {"type": "multiline", "optional": True, "default": "...or something rare!"},
        },
        "derive": {
            "type": "item",
            "name": "{title} Crate",
            "description": ["Contains an item {origin}.", "{contents}", "{closing}"],
            "tags": "type:lootbox;series:{series}",
        },
    }


class CrateKindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        self.project.kinds["crate"] = crate_kind()
        self.before = {it["itemdefid"]: it for it in self.project.build()}

    def tearDown(self):
        self.tmp.cleanup()

    def built(self):
        return {it["itemdefid"]: it for it in self.project.build()}

    def test_a_crate_sees_its_series_and_keeps_the_list_token(self):
        recs = self.project.items
        recs[[r["itemdefid"] for r in recs].index(1)] = {"itemdefid": 1, "kind": "crate", "title": "Test"}
        crate = self.built()[1]
        self.assertEqual(crate, self.before[1])  # "from the Test Series", the list, the default closing
        self.assertEqual(crate["tags"], "type:lootbox;series:crate1")
        self.assertEqual([i for i in check.check_project(self.project) if i.level != "note"], [])

    def test_converting_leaves_defaults_to_the_kind(self):
        result = adopt.adopt(self.project, "crate", [1])
        self.assertEqual(result.adopted, [1])
        self.assertEqual(result.overrides, {})
        self.assertEqual(self.project.item(1), {"itemdefid": 1, "kind": "crate", "title": "Test"})
        self.assertEqual(self.built(), self.before)

    def test_values_other_than_the_default_are_kept(self):
        crate = self.project.item(1)
        crate["description"] = crate["description"].replace("from the Test Series", "made by testers") \
            .replace("...or something rare!", "Thanks,\nall of you.")
        before = self.built()
        result = adopt.adopt(self.project, "crate", [1])
        self.assertEqual(result.overrides, {})
        self.assertEqual(self.project.item(1), {"itemdefid": 1, "kind": "crate", "title": "Test",
                                                "origin": "made by testers", "closing": "Thanks,\nall of you."})
        self.assertEqual(self.built(), before)

    def as_crate(self, **values):
        recs = self.project.items
        recs[[r["itemdefid"] for r in recs].index(1)] = {"itemdefid": 1, "kind": "crate", "title": "Test", **values}

    def test_an_empty_value_leaves_the_default_out(self):
        self.as_crate(closing="")
        crate = self.built()[1]
        self.assertEqual(crate["description"], self.before[1]["description"].replace("\n\n...or something rare!", ""))
        self.assertEqual([i for i in check.check_project(self.project) if i.level != "note"], [])

    def test_converting_keeps_a_left_out_default_empty(self):
        crate = self.project.item(1)
        crate["description"] = crate["description"].replace("\n\n...or something rare!", "")
        before = self.built()
        result = adopt.adopt(self.project, "crate", [1])
        self.assertEqual(result.adopted, [1])
        self.assertEqual(self.project.item(1), {"itemdefid": 1, "kind": "crate", "title": "Test", "closing": ""})
        self.assertEqual(self.built(), before)

    def test_set_can_leave_a_field_out(self):
        self.as_crate()
        self.project.kinds["crate"]["fields"]["note"] = {"type": "text", "optional": True}
        edits.set_fields(self.project, [1], [], leave_out=["closing", "note"])
        self.assertEqual(self.project.item(1), {"itemdefid": 1, "kind": "crate", "title": "Test", "closing": ""})
        edits.set_fields(self.project, [1], [("closing", "")])  # a plain empty value goes back to the default
        self.assertNotIn("closing", self.project.item(1))
        with self.assertRaisesRegex(ProjectError, "only kind fields can be left out"):
            edits.set_fields(self.project, [1], [], leave_out=["name"])

    def test_a_required_field_left_out_is_reported(self):
        self.project.kinds["crate"]["fields"]["closing"].pop("optional")
        self.as_crate(closing="")
        self.assertIn("warning: [1] closing is empty", [str(i) for i in check.check_project(self.project)])

    def test_renamed_fields_keep_their_values(self):
        self.as_crate(origin="made by testers", closing="Thanks!")
        before = self.built()
        kind = crate_kind()
        kind["fields"]["extra"] = kind["fields"].pop("closing")
        kind["fields"]["source"] = kind["fields"].pop("origin")
        edits.replace_kind(self.project, "crate", kind, "crate", {"closing": "extra", "origin": "source"})
        self.assertEqual(self.project.item(1), {"itemdefid": 1, "kind": "crate", "title": "Test",
                                                "source": "made by testers", "extra": "Thanks!"})
        self.assertEqual(self.project.kinds["crate"]["derive"]["description"],
                         ["Contains an item {source}.", "{contents}", "{extra}"])
        self.assertEqual(self.built(), before)

    def test_a_rename_leaves_references_to_a_new_field_of_the_old_name(self):
        self.as_crate(closing="Thanks!")
        kind = crate_kind()
        kind["fields"]["extra"] = kind["fields"]["closing"]
        kind["fields"]["closing"] = {"type": "text", "optional": True}
        kind["derive"]["description"] = ["{contents}", "{extra}", "{closing}"]
        edits.replace_kind(self.project, "crate", kind, "crate", {"closing": "extra"})
        self.assertEqual(self.project.item(1), {"itemdefid": 1, "kind": "crate", "title": "Test", "extra": "Thanks!"})
        self.assertEqual(self.project.kinds["crate"]["derive"]["description"], ["{contents}", "{extra}", "{closing}"])

    def test_fields_can_swap_names(self):
        self.as_crate(origin="made by testers", closing="Thanks!")
        before = self.built()
        kind = crate_kind()
        kind["fields"]["origin"], kind["fields"]["closing"] = kind["fields"]["closing"], kind["fields"]["origin"]
        edits.replace_kind(self.project, "crate", kind, "crate", {"origin": "closing", "closing": "origin"})
        self.assertEqual(self.project.item(1)["origin"], "Thanks!")
        self.assertEqual(self.project.kinds["crate"]["derive"]["description"],
                         ["Contains an item {closing}.", "{contents}", "{origin}"])
        self.assertEqual(self.built(), before)

    def test_stray_fields_on_items_of_a_kind_are_reported(self):
        self.as_crate(closing="Thanks!", old_closing="Thanks!", name_german="Testkiste")
        texts = [str(i) for i in check.check_project(self.project)]
        self.assertIn("warning: [1] stores old_closing, which is neither a field of kind 'crate' nor a Steam field, "
                      "so it is exported as it is. If it is left over from a renamed or removed field, remove it "
                      "(`sisdefman set 1 --unset old_closing`, or its remove button under Other fields in the GUI).",
                      texts)
        self.assertEqual([t for t in texts if "name_german" in t], [])

    def test_listing_tokens_outside_a_crate_are_reported(self):
        self.project.item(999999)["description"] = "{contents_no_secret}"
        self.assertIn("warning: [999999] description still contains {contents_no_secret}; only containers "
                      "configured in a series get it filled in",
                      [str(i) for i in check.check_project(self.project)])


if __name__ == "__main__":
    unittest.main()
