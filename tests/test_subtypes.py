"""Kinds that extend another kind (sub-types)."""

import tempfile
import unittest

from sisdefman import adopt, check, derive, edits, importer
from sisdefman.project import ProjectError

from tests import fixtures

PROMO = {
    "extends": "skin",
    "fields": {},
    "derive": {"description": ["Applies the {finish} appearance to the {weapon.name}.", "{flavor}",
                               "A promotional item."]},
}


class SubtypeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        self.project.data.update(fixtures.skin_schema())
        adopt.adopt(self.project, "skin", list(range(110, 118)))
        self.project.kinds["promo"] = dict(PROMO)

    def tearDown(self):
        self.tmp.cleanup()

    def built(self):
        return {it["itemdefid"]: it for it in self.project.build()}

    def test_a_sub_type_inherits_and_replaces_rules(self):
        kind = self.project.schema().kinds["promo"]
        self.assertEqual(list(kind["fields"]), ["weapon", "finish", "rarity", "flavor"])
        self.assertEqual(kind["derive"]["name"], "{weapon.name} | {finish}")
        before = self.built()[110]
        edits.set_fields(self.project, [110], [("kind", "promo")])
        after = self.built()[110]
        self.assertEqual(after["description"], "Applies the Red appearance to the Pistol.\n\nA promotional item.")
        self.assertEqual({k: v for k, v in after.items() if k != "description"},
                         {k: v for k, v in before.items() if k != "description"})
        self.assertEqual([i for i in check.check_project(self.project) if i.level != "note"], [])

    def test_a_sub_type_can_change_just_a_default(self):
        self.project.kinds["promo"] = {"extends": "skin", "fields": {"flavor": {"default": "Handed out at a show."}}}
        spec = self.project.schema().kinds["promo"]["fields"]["flavor"]
        self.assertEqual(spec, {"type": "multiline", "optional": True, "default": "Handed out at a show."})
        edits.set_fields(self.project, [111], [("kind", "promo")])
        self.assertEqual(self.built()[111]["description"],
                         "Applies the Blue appearance to the Rifle.\n\nHanded out at a show.")

    def test_sub_types_of_sub_types(self):
        self.project.kinds["gold"] = {"extends": "promo", "derive": {"name_color": "ffd700"}}
        edits.set_fields(self.project, [112], [("kind", "gold")])
        item = self.built()[112]
        self.assertEqual(item["name_color"], "ffd700")
        self.assertTrue(item["description"].endswith("A promotional item."))
        self.assertEqual(derive.family(self.project.kinds, "gold"), "skin")
        self.assertEqual(sorted(derive.descendants(self.project.kinds, "skin")), ["gold", "promo"])

    def test_bad_extends_are_errors(self):
        self.project.kinds["promo"]["extends"] = "nothing"
        self.assertIn("kind 'promo' extends 'nothing', which is not a kind",
                      derive.check_definitions(self.project.schema()))
        self.project.kinds["promo"]["extends"] = "loop"
        self.project.kinds["loop"] = {"extends": "promo"}
        self.assertIn("kind 'promo' extends itself (promo -> loop -> promo)",
                      derive.check_definitions(self.project.schema()))

    def test_only_related_kinds_can_be_switched(self):
        self.project.kinds["other"] = {"fields": {}, "derive": {"name": "x"}}
        with self.assertRaisesRegex(ProjectError, "only switch between a kind and the kinds that extend it"):
            edits.set_fields(self.project, [110], [("kind", "other")])
        with self.assertRaisesRegex(ProjectError, "only switch"):
            edits.set_fields(self.project, [999999], [("kind", "promo")])  # a plain item: use adopt
        with self.assertRaisesRegex(ProjectError, "no kind named"):
            edits.set_fields(self.project, [110], [("kind", "missing")])

    def test_adopting_into_a_sub_type(self):
        adopt.detach(self.project, [113])
        record = self.project.item(113)
        record["description"] = "Applies the Stripes appearance to the Pistol.\n\nA promotional item."
        before = self.built()
        result = adopt.adopt(self.project, "promo", [113])
        self.assertEqual(result.adopted, [113])
        self.assertEqual(self.project.item(113)["kind"], "promo")
        self.assertEqual(self.built(), before)

    def test_renaming_and_deleting_a_base_kind(self):
        edits.set_fields(self.project, [110], [("kind", "promo")])
        edits.replace_kind(self.project, "weaponskin", self.project.kinds["skin"], "skin")
        self.assertEqual(self.project.kinds["promo"]["extends"], "weaponskin")
        with self.assertRaisesRegex(ProjectError, "is used by"):
            edits.delete_kind(self.project, "weaponskin")
        self.project.items[:] = [r for r in self.project.items if r.get("kind") not in ("weaponskin", "promo")]
        with self.assertRaisesRegex(ProjectError, "is extended by promo"):
            edits.delete_kind(self.project, "weaponskin")

    def test_renaming_a_base_field_reaches_sub_types(self):
        self.project.kinds["promo"]["fields"] = {"flavor": {"default": "From a show."}}
        edits.set_fields(self.project, [110], [("kind", "promo"), ("flavor", "Painted red.")])
        before = self.built()
        kind = self.project.kinds["skin"]
        kind["fields"]["note"] = kind["fields"].pop("flavor")
        edits.replace_kind(self.project, "skin", kind, "skin", {"flavor": "note"})
        self.assertEqual(self.project.item(110)["note"], "Painted red.")
        self.assertEqual(self.project.kinds["promo"]["fields"], {"note": {"default": "From a show."}})
        self.assertEqual(self.project.kinds["promo"]["derive"]["description"][1], "{note}")
        self.assertEqual(self.built(), before)

    def test_table_rows_found_through_inherited_fields(self):
        edits.set_fields(self.project, [110], [("kind", "promo")])
        self.assertIn("promo", edits.ref_fields(self.project, "weapon"))
        edits.rename_row(self.project, "weapon", "pistol", "handgun")
        self.assertEqual(self.project.item(110)["weapon"], "handgun")


if __name__ == "__main__":
    unittest.main()
