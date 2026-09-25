import unittest

from sisdefman import steam


class TagTests(unittest.TestCase):
    def test_parse_and_match(self):
        item = {"tags": "type:skin;series:crate1;rarity:common"}
        self.assertEqual(steam.tag_values(item, "series"), ["crate1"])
        self.assertTrue(steam.has_tags(item, ["rarity:common", "type:skin"]))
        self.assertFalse(steam.has_tags(item, ["rarity:rare"]))
        self.assertEqual(steam.parse_tags(None), [])

    def test_set_single_tag_replaces_and_inserts(self):
        item = {"tags": "type:skin;series:crate2;rarity:rare"}
        self.assertTrue(steam.set_single_tag(item, "series", "crate1"))
        self.assertEqual(item["tags"], "type:skin;series:crate1;rarity:rare")
        self.assertFalse(steam.set_single_tag(item, "series", "crate1"))

        item = {"tags": "type:skin;rarity:rare"}
        steam.set_single_tag(item, "series", "crate1")
        self.assertEqual(item["tags"], "type:skin;series:crate1;rarity:rare")

        item = {}
        steam.set_single_tag(item, "series", "crate1")
        self.assertEqual(item["tags"], "series:crate1")

    def test_parse_tag_rule(self):
        self.assertEqual(steam.parse_tag_rule("rarity:common"), ["rarity:common"])
        self.assertEqual(steam.parse_tag_rule(["a:b", "c:d;e:f"]), ["a:b", "c:d", "e:f"])
        with self.assertRaises(steam.SyntaxProblem):
            steam.parse_tag_rule(5)


class ReferenceTests(unittest.TestCase):
    def test_references(self):
        item = {
            "bundle": "101x1000;102;103x5",
            "exchange": "1;110x2,rarity:common*5",
            "tag_generators": "109",
        }
        self.assertEqual(
            steam.references(item),
            [("bundle", 101), ("bundle", 102), ("bundle", 103),
             ("exchange", 1), ("exchange", 110), ("tag_generators", 109)],
        )

    def test_remap_keeps_quantities_and_tag_materials(self):
        item = {"bundle": "110x3;111", "exchange": "110,111x2;type:skin*10", "tag_generators": "109"}
        changed = steam.remap_references(item, {110: 111, 111: 112})
        self.assertEqual(item["bundle"], "111x3;112")
        self.assertEqual(item["exchange"], "111,112x2;type:skin*10")
        self.assertEqual(item["tag_generators"], "109")
        self.assertEqual(changed, ["bundle", "exchange"])

    def test_empty_fields_are_ignored(self):
        item = {"bundle": "", "exchange": "series:crate1*5"}
        self.assertEqual(steam.references(item), [])
        self.assertEqual(steam.remap_references(item, {1: 2}), [])

    def test_syntax_errors(self):
        for item in ({"bundle": "abc"}, {"exchange": "rarity:common"}, {"tag_generators": "1x2"}):
            with self.assertRaises(steam.SyntaxProblem):
                steam.references(item)

    def test_plain_bundle(self):
        self.assertEqual(steam.bundle_is_plain_list("1;2;3"), [1, 2, 3])
        self.assertIsNone(steam.bundle_is_plain_list("1x2;3"))
        self.assertIsNone(steam.bundle_is_plain_list(""))


if __name__ == "__main__":
    unittest.main()
