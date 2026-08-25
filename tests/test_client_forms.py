import sqlite3
import unittest

from client.forms import FieldDef, FormDef, init_tables, upsert_form, validate_form


class TestClientForms(unittest.TestCase):
    def test_validate_form_requires_fields(self):
        f = FormDef(form_id=None, name="f1", fields=[])
        errs = validate_form(f)
        self.assertTrue(errs)

    def test_validate_select_requires_options(self):
        f = FormDef(
            form_id=None,
            name="f2",
            fields=[FieldDef(field_id="x", label="X", field_type="select", required=True, options=[])],
        )
        errs = validate_form(f)
        self.assertTrue(errs)

    def test_upsert_form_roundtrip(self):
        con = sqlite3.connect(":memory:")
        init_tables(con)
        f = FormDef(
            form_id=None,
            name="f3",
            fields=[
                FieldDef(field_id="title", label="Title", field_type="text", required=True),
                FieldDef(field_id="channel", label="Channel", field_type="select", required=True, options=["a", "b"]),
            ],
        )
        fid = upsert_form(con, f)
        self.assertTrue(fid > 0)


if __name__ == "__main__":
    unittest.main()
