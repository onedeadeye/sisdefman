import tempfile
import unittest

from sisdefman import importer, ops
from sisdefman.project import ProjectError

from tests import fixtures


def new_skin(name, rarity="common"):
    return {"type": "item", "name": name, "description": "New.", "tags": f"type:skin;rarity:{rarity}"}


class OpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        # An ordinary bundle that names series items directly.
        self.project.items.append({"itemdefid": 500, "type": "bundle", "name": "Pack", "bundle": "113x2;117"})
        self.project.normalize()

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        return [m["name"] for m in self.project.members("crate1")]

    def built(self):
        return {it["itemdefid"]: it for it in self.project.build()}

    def test_append_reuses_dummy_slot_without_renumbering(self):
        result = ops.insert_items(self.project, "crate1", [new_skin("Knife | Dots")])
        self.assertEqual(result.added, [118])
        self.assertEqual(result.moved, {})
        self.assertEqual(self.project.item(118)["tags"], "type:skin;series:crate1;rarity:common")
        self.assertEqual(self.built()[101]["bundle"], "110;111;112;118")
        self.assertTrue(self.built()[118]["description"].endswith("Test Series #9"))

    def test_insert_renumbers_and_follows_references(self):
        result = ops.insert_items(self.project, "crate1", [new_skin("Knife | Dots")], before=113)
        self.assertEqual(result.added, [113])
        self.assertEqual(result.moved, {113: 114, 114: 115, 115: 116, 116: 117, 117: 118})
        self.assertEqual(self.names()[2:5], ["Shotgun | Green", "Knife | Dots", "Pistol | Stripes"])
        built = self.built()
        # Rule-based generators are rebuilt, other references follow their items.
        self.assertEqual(built[101]["bundle"], "110;111;112;113")
        self.assertEqual(built[102]["bundle"], "114;115")
        self.assertEqual(built[104]["bundle"], "117;118")
        self.assertEqual(built[500]["bundle"], "114x2;118")
        self.assertEqual([i for i, _ in result.rewritten], [500])
        # Series text and the crate list follow the new order.
        self.assertTrue(built[114]["description"].endswith("Test Series #5"))
        self.assertIn("Shotgun | Green\nKnife | Dots\nPistol | Stripes", built[1]["description"])
        self.assertEqual(built[119]["name"], "Dummy Item #119")

    def test_insert_at_position_and_several_items(self):
        ops.insert_items(self.project, "crate1", [new_skin("A | 1"), new_skin("A | 2")], position=1)
        self.assertEqual(self.names()[:3], ["A | 1", "A | 2", "Pistol | Red"])
        self.assertEqual(self.project.item(110)["name"], "A | 1")
        self.assertEqual(self.project.members("crate1")[-1]["itemdefid"], 119)

    def test_insert_shift_stops_at_a_gap(self):
        ops.remove_item(self.project, 114, shift=False)
        result = ops.insert_items(self.project, "crate1", [new_skin("Knife | Dots")], position=1)
        self.assertEqual(result.moved, {110: 111, 111: 112, 112: 113, 113: 114})
        self.assertEqual(self.project.item(115)["name"], "Shotgun | Cracks")

    def test_remove_with_and_without_shift(self):
        ops.insert_items(self.project, "crate1", [new_skin("Extra")])  # 118, so 113 is unreferenced below
        self.project.item(500)["bundle"] = "114"
        result = ops.remove_item(self.project, 113, shift=False)
        self.assertEqual(result.moved, {})
        self.assertEqual(self.built()[113]["name"], "Dummy Item #113")
        self.assertTrue(self.built()[114]["description"].endswith("Test Series #4"))

        result = ops.remove_item(self.project, 112)
        self.assertEqual(result.moved, {})  # 113 is empty, so nothing after it moves
        result = ops.remove_item(self.project, 110)
        self.assertEqual(result.moved, {111: 110})
        self.assertEqual(self.project.series["crate1"]["allocated_through"], 119)

    def test_remove_refuses_referenced_items(self):
        with self.assertRaises(ProjectError) as ctx:
            ops.remove_item(self.project, 117)
        self.assertIn("500", str(ctx.exception))
        # Rule-based generator bundles do not count as references.
        ops.remove_item(self.project, 112)
        self.assertEqual(self.built()[101]["bundle"], "110;111")

    def test_remove_outside_series(self):
        with self.assertRaises(ProjectError):
            ops.remove_item(self.project, 101)

    def test_move(self):
        result = ops.move_item(self.project, 117, position=1)
        self.assertEqual(self.names()[:2], ["Rifle | Sparks", "Pistol | Red"])
        self.assertEqual(result.moved[117], 110)
        self.assertEqual(self.project.item(500)["bundle"], "114x2;110")

        ops.move_item(self.project, 110, after=117)
        self.assertEqual(self.names()[-1], "Rifle | Sparks")
        ops.move_item(self.project, 110, before=112)
        self.assertEqual(self.names()[:3], ["Rifle | Blue", "Pistol | Red", "Shotgun | Green"])
        with self.assertRaises(ProjectError):
            ops.move_item(self.project, 110, before=110)

    def test_series_full(self):
        self.project.series["crate1"]["last_id"] = 119
        before = self.project.to_json()
        with self.assertRaises(ProjectError):
            ops.insert_items(self.project, "crate1", [new_skin("a"), new_skin("b"), new_skin("c")])
        self.assertEqual(self.project.to_json(), before)

    def test_copied_item_changes_series_tag(self):
        item = dict(new_skin("Copy"), tags="type:skin;series:crate9;rarity:rare", itemdefid=42)
        result = ops.insert_items(self.project, "crate1", [item])
        self.assertEqual(self.project.item(118)["tags"], "type:skin;series:crate1;rarity:rare")
        self.assertEqual(len(result.notes), 2)

    def test_append_skips_ids(self):
        result = ops.insert_items(self.project, "crate1", [new_skin("x")], skip_ids=frozenset({118}))
        self.assertEqual(result.added, [119])

    def test_shift_summary(self):
        self.assertEqual(ops.shift_summary({113: 114, 114: 115, 117: 110}), ["113-114 -> 114-115", "117 -> 110"])


if __name__ == "__main__":
    unittest.main()
