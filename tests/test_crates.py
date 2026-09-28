"""Crates (a series' containers) as items of a kind."""

import tempfile
import unittest

from sisdefman import adopt, check, importer

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

    def test_listing_tokens_outside_a_crate_are_reported(self):
        self.project.item(999999)["description"] = "{contents_no_secret}"
        self.assertIn("warning: [999999] description still contains {contents_no_secret}; only containers "
                      "configured in a series get it filled in",
                      [str(i) for i in check.check_project(self.project)])


if __name__ == "__main__":
    unittest.main()
