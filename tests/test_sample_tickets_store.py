import json
import tempfile
import unittest
from pathlib import Path

from client.sample_tickets_store import SampleTicketsError, ensure_store, load_store, save_store


class TestSampleTicketsStore(unittest.TestCase):
    def test_ensure_store_creates_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sample_tickets.json"
            store = ensure_store(path)
            self.assertIn("templates", store)
            self.assertTrue(store["templates"])
            store2 = load_store(path)
            self.assertEqual(store2["version"], 1)

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sample_tickets.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(SampleTicketsError):
                load_store(path)

    def test_invalid_format_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sample_tickets.json"
            path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            with self.assertRaises(SampleTicketsError):
                load_store(path)

    def test_save_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sample_tickets.json"
            store = {"version": 1, "templates": [], "tickets": [], "pending": [], "stats": {"submitted": 0}}
            save_store(path, store)
            loaded = load_store(path)
            self.assertEqual(loaded["stats"]["submitted"], 0)


if __name__ == "__main__":
    unittest.main()

