import io
import tempfile
import unittest

from sisdefman import importer, ops, safety, ui

from tests import fixtures


class SafetyTests(unittest.TestCase):
    def setUp(self):
        ui._enabled = False
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_live_lists_ownable_items_only(self):
        n = self.project.record_live()
        live = self.project.live_names()
        self.assertEqual(n, 9)  # the crate and 8 series items
        self.assertEqual(live[110], "Pistol | Red")
        self.assertNotIn(101, live)  # generators cannot be owned
        self.assertNotIn(118, live)  # dummies from before release are not owned

    def test_compare(self):
        self.project.record_live()
        live = self.project.live_names()
        self.assertEqual(safety.compare(live, self.project, self.project.build()), [])

        ops.insert_items(self.project, "crate1", [{"name": "New", "type": "item"}], position=8)
        impacts = safety.compare(live, self.project, self.project.build())
        self.assertEqual([(i.itemdefid, i.kind, i.now) for i in impacts],
                         [(117, "reassigned", "New")])

        ops.remove_item(self.project, 118)
        ops.remove_item(self.project, 117)
        impacts = safety.compare(live, self.project, self.project.build())
        self.assertEqual([(i.itemdefid, i.kind) for i in impacts], [(117, "dummied")])

    def test_retired_ids_stay_protected(self):
        self.project.record_live()
        ops.remove_item(self.project, 117, shift=False)
        self.assertEqual(self.project.retired_ids(), frozenset({117}))
        self.project.record_live()  # the removal was uploaded
        self.assertEqual(self.project.live_names()[117], "Dummy Item #117")

        before = self.project.build()
        protect = safety.protected_before(self.project, before)
        self.assertEqual(protect[117], "Dummy Item #117")
        ops.insert_items(self.project, "crate1", [{"name": "New", "type": "item"}], position=8)
        impacts = safety.compare(protect, self.project, self.project.build())
        self.assertEqual([(i.itemdefid, i.kind) for i in impacts], [(117, "reused")])
        self.assertIn("retired item", impacts[0].describe())

    def test_warning_needs_confirmation(self):
        self.project.mode = "release"
        impacts = [safety.Impact(110, "Pistol | Red", "New", "reassigned")]
        out = io.StringIO()
        ok = safety.warn_and_confirm(self.project, impacts, False, action="Test", out=out, stdin=io.StringIO())
        self.assertFalse(ok)
        self.assertIn("RELEASE MODE", out.getvalue())
        self.assertIn("#110", out.getvalue())

        class Tty(io.StringIO):
            def isatty(self):
                return True

        for answer, expected in (("yes", False), (ui.CONFIRM_PHRASE, True)):
            ok = safety.warn_and_confirm(self.project, impacts, False, action="Test", out=io.StringIO(),
                                         stdin=Tty(), read=lambda prompt, a=answer: a)
            self.assertEqual(ok, expected)
        self.assertTrue(safety.warn_and_confirm(self.project, impacts, True, action="Test", out=io.StringIO()))

    def test_no_warning_in_prerelease_or_without_impacts(self):
        impacts = [safety.Impact(110, "a", "b", "reassigned")]
        self.assertTrue(safety.warn_and_confirm(self.project, impacts, False, action="x", out=io.StringIO()))
        self.project.mode = "release"
        self.assertTrue(safety.warn_and_confirm(self.project, [], False, action="x", out=io.StringIO()))


if __name__ == "__main__":
    unittest.main()
