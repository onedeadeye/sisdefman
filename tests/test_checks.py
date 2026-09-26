"""Checks for mistakes players would see and for definitions nothing uses."""

import tempfile
import unittest

from sisdefman import check, importer
from sisdefman.project import Project

from tests import fixtures


def issues(project, level=None):
    return [str(i) for i in check.check_project(project) if level is None or i.level == level]


class VisibleTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_series_key_in_templates(self):
        self.project.settings["description_template"] = "{description}\n\n{series} #{index}"
        self.assertIn("warning: the description template uses {series}, the series' key; use {series_name} for "
                      "its display name", issues(self.project))

    def test_table_key_in_a_kind_rule(self):
        self.project.data.update(fixtures.skin_schema())
        self.project.tables["weapon"]["rows"] = {"pistol": {"name": "Pistol"}}
        rules = self.project.kinds["skin"]["derive"]
        self.assertFalse([i for i in issues(self.project) if "row key" in i])
        rules["name"] = "{weapon} | {finish}"
        rules["display_type"] = "{series} skin"
        found = issues(self.project)
        self.assertIn("warning: kind 'skin': the rule for name uses {weapon}, which is the row key of table "
                      "'weapon' (such as 'pistol'); use a column such as {weapon.name} for text players read", found)
        self.assertIn("warning: kind 'skin': the rule for display_type uses {series}, the series' key; use "
                      "{series.name} for its display name", found)

    def test_placeholders_and_colour_keywords_left_in_text(self):
        self.project.item(999999)["description"] = "Drops from the {series_name}. Shown in @common."
        self.project.colors["common"] = "d2d2d2"
        found = issues(self.project)
        self.assertIn("warning: [999999] description contains {series_name}, which is not filled in here, so "
                      "players would see it as it is", found)
        self.assertIn("warning: [999999] description shows the colour keyword @common as text; keywords only work "
                      "in colour fields", found)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_unused_and_empty_definitions(self):
        self.assertEqual(issues(self.project, "note"), [])
        self.project.items.append({"itemdefid": 106, "type": "tag_generator", "name": "Unused",
                                   "tag_generator_name": "x", "tag_generator_values": "a"})
        self.project.items.append({"itemdefid": 107, "type": "generator", "bundle": "110", "name": "Orphan"})
        self.project.items.append({"itemdefid": 108, "type": "generator", "bundle": "", "name": "Swap",
                                   "exchange": "rarity:epic*5"})
        found = issues(self.project)
        self.assertIn("note: [106] tag generator is not used by any generator's tag_generators", found)
        self.assertIn("note: [107] nothing refers to this definition and it has no exchange recipe or promo, so "
                      "players can only get it if your game server grants it", found)
        self.assertIn("warning: [108] has an exchange recipe but an empty bundle: players who exchange for it get "
                      "nothing", found)

    def test_oversized_range(self):
        self.project.items[:] = [it for it in self.project.items if it["itemdefid"] < 197 or it["itemdefid"] > 199]
        self.project.series["crate1"]["last_id"] = 999998
        self.assertIn("warning: series 'crate1' reserves IDs 110-999998 for 8 item(s); every definition added in "
                      "that range becomes one of its items. Shrink it with `sisdefman series set crate1 --last-id "
                      "199` (or on the Series setup page).", issues(self.project))


class OutlierTests(unittest.TestCase):
    COLORS = {"common": "d2d2d2", "uncommon": "5e90e0", "rare": "eb7ce9", "epic": "f08f35"}

    def project(self):
        project = Project.new(fixtures.APPID)
        i = 10
        for rarity, color in self.COLORS.items():
            for n in range(4):
                project.items.append({"itemdefid": i, "type": "item", "name": f"{rarity} {n}", "name_color": color,
                                      "tradable": True, "tags": f"rarity:{rarity};pack:{'a' if n < 2 else 'b'}"})
                i += 1
        return project

    def test_one_item_in_the_wrong_colour(self):
        project = self.project()
        self.assertEqual(issues(project), [])
        project.items[8]["name_color"] = "d2d2d2"  # a rare in the common colour
        self.assertEqual(issues(project), [
            'warning: [18] name_color is "d2d2d2", but 3 of the 4 items tagged rarity:rare have "eb7ce9"'])

    def test_a_tag_that_explains_every_value_silences_it(self):
        project = self.project()
        for it in project.items:
            if it["itemdefid"] % 4 == 0:
                it["tags"] += ";promo:yes"
                it["tradable"] = False
            else:
                it["tags"] += ";promo:no"
        self.assertEqual([i for i in issues(project) if "tradable" in i], [])


if __name__ == "__main__":
    unittest.main()
