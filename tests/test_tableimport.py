import tempfile
import unittest

from sisdefman import adopt, importer, query, tableimport
from sisdefman.project import ProjectError

from tests import fixtures


class CleanValueTests(unittest.TestCase):
    def test_unreal_notation(self):
        clean = tableimport.clean_value
        self.assertEqual(clean('NSLOCTEXT("[B6]", "78A8", "Handgun")'), "Handgun")
        self.assertEqual(clean('NSLOCTEXT("ns", "k", "Say \\"hi\\"\\nnow")'), 'Say "hi"\nnow')
        self.assertEqual(clean('LOCTEXT("k", "Text")'), "Text")
        self.assertEqual(clean('INVTEXT("Text")'), "Text")
        self.assertEqual(clean('(DataTable="/Script/Engine.DataTable\'/Game/DT.DT\'",RowName="Basic")'), "Basic")
        self.assertEqual(clean("/Game/A/B/BP_X_Pickup.BP_X_Pickup_C"), "BP_X_Pickup_C")
        self.assertEqual(clean("/Script/Engine.Texture2D'/Game/UI/T_Icon.T_Icon'"), "T_Icon")
        for plain in ("MEDIUM", "12", "None", "(X=1,Y=2)", "/not a path", ""):
            self.assertEqual(clean(plain), plain)

    def test_column_names(self):
        self.assertEqual(tableimport.column_name("Display Name"), "Display_Name")
        self.assertEqual(tableimport.column_name("3D Model"), "c_3D_Model")
        self.assertEqual(tableimport.column_name("---"), "column")


class TableImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project, _ = importer.import_files(fixtures.write_files(self.tmp.name))
        self.project.data.update(fixtures.skin_schema())
        adopt.adopt(self.project, "skin", list(range(110, 118)))
        self.before = self.project.build()
        self.data = tableimport.read_csv(fixtures.WEAPON_CSV)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        return self.project.tables["weapon"]["rows"]

    def test_reference_import_leaves_items_alone(self):
        report = tableimport.import_csv(self.project, "weapon", self.data, skip=["ActorClass"],
                                        fills=[("name", "DisplayName")])
        self.assertEqual(report.added, ["knife"])  # new keys follow the table's lower case
        self.assertEqual(report.updated, ["pistol", "rifle"])
        self.assertEqual(report.columns_added, ["DisplayName", "Description", "Category", "Range"])
        self.assertEqual(report.filled, 1)
        self.assertEqual(report.kept, ["rifle.name is 'Rifle'; the file's DisplayName is 'Long Rifle'"])
        self.assertEqual(self.rows()["pistol"], {"name": "Pistol", "DisplayName": "Pistol",
                                                 "Description": 'A small "gun".', "Category": "Basic",
                                                 "Range": "SHORT"})
        self.assertEqual(self.rows()["knife"]["name"], "Knife")
        self.assertNotIn("Description", self.rows()["knife"])  # empty in the file
        self.assertEqual(self.project.build(), self.before)

        views = {v["id"]: v for v in query.select(self.project, ["weapon.Range=LONG"])}
        self.assertEqual(sorted(views), [111, 114, 117])
        self.assertEqual(views[111]["weapon.Description"], "Reaches far.")

    def test_mapping_onto_a_used_column_changes_items(self):
        report = tableimport.import_csv(self.project, "weapon", self.data, only=["DisplayName"],
                                        rename={"DisplayName": "name"})
        self.assertEqual(report.changes, ["rifle.name: 'Rifle' -> 'Long Rifle'"])
        built = {it["itemdefid"]: it for it in self.project.build()}
        self.assertEqual(built[111]["name"], "Long Rifle | Blue")

    def test_new_table_keeps_key_case_and_cleans_paths(self):
        report = tableimport.import_csv(self.project, "weapon_data", self.data)
        self.assertTrue(report.created)
        self.assertEqual(list(self.project.tables["weapon_data"]["rows"]), ["Pistol", "Rifle", "Knife"])
        self.assertEqual(self.project.tables["weapon_data"]["rows"]["Knife"]["ActorClass"], "BP_Knife_Actor_C")
        report = tableimport.import_csv(self.project, "weapon_raw", self.data, key_case="lower", raw=True,
                                        only=["Category"])
        self.assertTrue(self.project.tables["weapon_raw"]["rows"]["pistol"]["Category"].startswith("(DataTable="))

    def test_errors(self):
        with self.assertRaises(ProjectError):
            tableimport.import_csv(self.project, "weapon", self.data, only=["Nope"])
        with self.assertRaises(ProjectError):
            tableimport.import_csv(self.project, "bad name", self.data)
        with self.assertRaises(ProjectError):
            tableimport.import_csv(self.project, "weapon", self.data, rename={"DisplayName": "Range"})
        with self.assertRaises(ProjectError):
            tableimport.read_csv("")
        data = tableimport.read_csv("key,a\n,1\nx,2\nX,3\n")
        report = tableimport.import_csv(self.project, "t", data)
        self.assertEqual(len(report.skipped), 2)
        self.assertEqual(self.project.tables["t"]["rows"], {"x": {"a": "3"}})


if __name__ == "__main__":
    unittest.main()
