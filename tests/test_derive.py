import copy
import tempfile
import unittest

from sisdefman import adopt, derive, importer, ops
from sisdefman.derive import Schema, SeriesInfo

from tests import fixtures


def schema(**tables_rows):
    s = fixtures.skin_schema()
    tables = s["tables"]
    tables["weapon"]["rows"] = {"pistol": {"name": "Pistol"}, "rifle": {"name": "Rifle"}}
    tables["rarity"]["rows"] = {"common": {"color": "d2d2d2"}}
    return Schema(tables, s["kinds"])


RECORD = {"itemdefid": 110, "kind": "skin", "weapon": "pistol", "finish": "Red", "rarity": "common"}


class ResolveTests(unittest.TestCase):
    def test_resolve_kind_item(self):
        item, problems = derive.resolve(schema(), RECORD, SeriesInfo("crate1", "Test Series", 1, 8))
        self.assertEqual(problems, [])
        self.assertEqual(item, {
            "itemdefid": 110,
            "type": "item",
            "name": "Pistol | Red",
            "description": "Applies the Red appearance to the Pistol.",
            "name_color": "d2d2d2",
            "background_color": "292929",
            "tradable": True,
            "marketable": True,
            "tags": "type:skin;series:crate1;rarity:common;weapon:pistol",
        })
        self.assertEqual(list(item)[:3], ["itemdefid", "type", "name"])

    def test_flavor_paragraph_and_overrides(self):
        record = dict(RECORD, flavor="Painted red.", name_color="ff0000", mat_id="red")
        item, _ = derive.resolve(schema(), record)
        self.assertEqual(item["description"], "Applies the Red appearance to the Pistol.\n\nPainted red.")
        self.assertEqual(item["name_color"], "ff0000")
        self.assertEqual(item["mat_id"], "red")  # undeclared values are exported
        self.assertNotIn("flavor", item)
        self.assertNotIn("kind", item)
        self.assertEqual(derive.overridden(schema(), record), ["name_color"])

    def test_problems(self):
        record = dict(RECORD, weapon="knife", finish="")
        item, problems = derive.resolve(schema(), record)
        self.assertEqual(item["name"], " | ")
        self.assertIn("finish is empty", problems)
        self.assertIn("weapon: 'knife' is not a row of table 'weapon'", problems)

        s = schema()
        s.kinds["skin"]["derive"]["icon_url"] = "{weapon.icon}/{nope}"
        s.kinds["skin"]["derive"]["a"] = "{b}"
        s.kinds["skin"]["derive"]["b"] = "{a}"
        _, problems = derive.resolve(s, RECORD)
        self.assertIn("table 'weapon' has no column 'icon'", problems)
        self.assertIn("unknown field {nope}", problems)
        self.assertTrue(any("depends on itself" in p for p in problems))

    def test_templates_can_use_other_fields_and_defaults(self):
        s = schema()
        s.kinds["skin"]["fields"]["mat_id"] = {"type": "text", "default": "{finish}"}
        s.kinds["skin"]["derive"]["mat_id"] = "{mat_id}"
        s.kinds["skin"]["derive"]["icon_url"] = "https://x/{weapon}_{mat_id}.png#{series.index:02d}"
        s.kinds["skin"]["derive"]["display_type"] = "{name} ({itemdefid})"
        item, problems = derive.resolve(s, RECORD, SeriesInfo("crate1", "Test", 3, 8))
        self.assertEqual(problems, [])
        self.assertEqual(item["mat_id"], "Red")
        self.assertEqual(item["icon_url"], "https://x/pistol_Red.png#03")
        self.assertEqual(item["display_type"], "Pistol | Red (110)")

    def test_check_definitions(self):
        s = schema()
        s.kinds["skin"]["fields"]["index"] = {"type": "text"}
        s.kinds["skin"]["fields"]["weapon"]["table"] = "nope"
        s.kinds["skin"]["fields"]["finish"]["type"] = "colour"
        s.kinds["skin"]["derive"]["name"] = "{weapon.name"
        problems = "\n".join(derive.check_definitions(s))
        for text in ("reserved name", "missing table 'nope'", "unknown type 'colour'", "rule for 'name'"):
            self.assertIn(text, problems)


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        self.project.data.update(fixtures.skin_schema())
        self.before = self.project.build()

    def tearDown(self):
        self.tmp.cleanup()

    def test_adopt_keeps_export_identical_and_learns_tables(self):
        result = adopt.adopt(self.project, "skin", list(range(110, 118)))
        self.assertEqual(result.adopted, list(range(110, 118)))
        self.assertEqual(result.skipped, {})
        self.assertEqual(result.overrides, {})
        self.assertEqual(self.project.build(), self.before)
        self.assertEqual(self.project.item(110), RECORD)
        self.assertEqual(self.project.tables["weapon"]["rows"],
                         {"pistol": {"name": "Pistol"}, "rifle": {"name": "Rifle"}, "shotgun": {"name": "Shotgun"}})
        self.assertEqual(self.project.tables["rarity"]["rows"]["epic"], {"color": "f08f35"})

        # Derived fields now follow the fields and tables.
        self.project.item(110)["flavor"] = "Painted red."
        self.project.tables["weapon"]["rows"]["pistol"]["name"] = "Handgun"
        built = {it["itemdefid"]: it for it in self.project.build()}
        self.assertEqual(built[110]["name"], "Handgun | Red")
        self.assertEqual(built[110]["description"],
                         "Applies the Red appearance to the Handgun.\n\nPainted red.\n\nTest Series #1")
        self.assertIn("Handgun | Red", built[1]["description"])  # the crate list follows too

    def test_new_kind_items_get_series_tags_from_the_rule(self):
        adopt.adopt(self.project, "skin", list(range(110, 118)))
        ops.insert_items(self.project, "crate1", [
            {"kind": "skin", "weapon": "rifle", "finish": "Gold", "rarity": "common"}], position=1)
        built = {it["itemdefid"]: it for it in self.project.build()}
        self.assertEqual(built[110]["tags"], "type:skin;series:crate1;rarity:common;weapon:rifle")
        self.assertNotIn("tags", self.project.item(110))
        self.assertEqual(built[101]["bundle"], "110;111;112;113")

    def test_mismatches_become_overrides(self):
        self.project.item(111)["name_color"] = "123456"
        self.before = self.project.build()
        result = adopt.adopt(self.project, "skin", [110, 111])
        self.assertEqual(result.adopted, [110, 111])
        self.assertEqual(result.overrides, {111: ["name_color"]})
        self.assertEqual(self.project.item(111)["name_color"], "123456")
        self.assertEqual(self.project.build(), self.before)

    def test_items_whose_export_would_change_are_skipped(self):
        self.project.kinds["skin"]["derive"]["icon_url"] = "https://x/{weapon}.png"
        self.project.item(110)["icon_url"] = "https://x/pistol.png"
        self.before = self.project.build()
        result = adopt.adopt(self.project, "skin", [110, 111, 101])
        self.assertEqual(result.adopted, [110])
        self.assertIn("icon_url", result.skipped[111])
        self.assertIn("could not work out", result.skipped[101])
        self.assertEqual(self.project.build(), self.before)

    def test_detach(self):
        adopt.adopt(self.project, "skin", list(range(110, 118)))
        original = copy.deepcopy(fixtures.all_items()[110])
        self.assertEqual(adopt.detach(self.project, [110, 101]), [110])
        self.assertEqual(self.project.item(110), original)
        self.assertEqual(self.project.build(), self.before)

    def test_invert(self):
        s = schema()
        ctx = derive.Context(s, {"itemdefid": 1}, None, s.kinds["skin"], strict=True)
        self.assertEqual(adopt.invert("{weapon.name} | {finish}", "Pistol | Red", ctx),
                         {"weapon.name": "Pistol", "finish": "Red"})
        self.assertEqual(adopt.invert("{a} {b}", "x y z", ctx), {})  # ambiguous
        self.assertIsNone(adopt.invert("A {a}", "B c", ctx))
        rule = ["Applies {finish}.", "{flavor}"]
        self.assertEqual(adopt.invert(rule, "Applies Red.", ctx), {"finish": "Red", "flavor": ""})
        self.assertEqual(adopt.invert(rule, "Applies Red.\n\nHot.", ctx), {"finish": "Red", "flavor": "Hot."})


if __name__ == "__main__":
    unittest.main()


class SeriesCountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def built(self):
        return {it["itemdefid"]: it for it in self.project.build()}

    def test_counts_default_to_the_container_exclusions(self):
        # The fixture crate leaves rarity:epic out of its list: 6 regular + 2 secret.
        self.assertEqual(self.project.secret_ids("crate1"), [116, 117])
        info = self.project.series_positions()[110]
        self.assertEqual((info.count, info.count_no_secret, info.count_secret), (8, 6, 2))
        self.project.settings["description_template"] = \
            "{description}\n\n#{index} of {count_no_secret} (+{count_secret}; {series.count} in all)"
        self.assertTrue(self.built()[110]["description"].endswith("#1 of 6 (+2; 8 in all)"))

    def test_explicit_secret_rule_and_kind_templates(self):
        self.project.series["crate1"]["secret"] = ["rarity:rare", "rarity:epic"]
        self.assertEqual(self.project.secret_ids("crate1"), [115, 116, 117])
        self.project.data.update(fixtures.skin_schema())
        adopt.adopt(self.project, "skin", list(range(110, 118)))
        self.project.kinds["skin"]["derive"]["display_type"] = "{series.count_no_secret}/{series.count_secret}"
        self.assertEqual(self.built()[110]["display_type"], "5/3")
        self.project.series["crate1"]["secret"] = []
        self.assertEqual(self.built()[110]["display_type"], "8/0")

    def test_container_tokens(self):
        crate = self.project.item(1)
        crate["description"] = crate["description"].replace(
            "Contains an item from the Test Series.",
            "One of {count_no_secret} items from the {series_name}, or one of {count_secret} secret ones.")
        text = self.built()[1]["description"]
        self.assertTrue(text.startswith("One of 6 items from the Test Series, or one of 2 secret ones.\n\nPistol | Red"))
        ops.insert_items(self.project, "crate1", [{"name": "New", "tags": "rarity:epic"}])
        self.assertTrue(self.built()[1]["description"].startswith("One of 6 items from the Test Series, or one of 3"))


class ContentsKeywordTests(unittest.TestCase):
    def test_contents_with_and_without_secret_rares(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = importer.import_files(fixtures.write_files(tmp))
        crate = project.item(1)
        crate["description"] = "Regular:\n{contents_no_secret}\n\nSecret:\n{contents_secret}"
        project.series["crate1"]["containers"]["1"] = {"exclude": []}  # secret rares now come from "secret"
        project.series["crate1"]["secret"] = "rarity:epic"
        text = {it["itemdefid"]: it for it in project.build()}[1]["description"]
        regular, secret = text.split("\n\n")
        self.assertEqual(regular.split("\n")[1:], [n for n, r, _ in fixtures.ITEMS if r != "epic"])
        self.assertEqual(secret.split("\n")[1:], ["Pistol | Flames", "Rifle | Sparks"])
