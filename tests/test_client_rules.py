import sqlite3
import unittest

from client.rules import TicketGenRule, init_tables, match_rule, upsert_rule, validate_rule


class TestClientRules(unittest.TestCase):
    def test_validate_rule_requires_trigger(self):
        r = TicketGenRule(
            rule_id=None,
            name="r1",
            enabled=True,
            priority=1,
            channel="",
            keywords=[],
            topic_hint="",
            submitter="s",
            title_template="t",
            description_template="d",
            route_hint="",
        )
        errs = validate_rule(r)
        self.assertTrue(errs)

    def test_match_rule_keywords(self):
        r = TicketGenRule(
            rule_id=None,
            name="r2",
            enabled=True,
            priority=1,
            channel="desktop_client",
            keywords=["charge"],
            topic_hint="",
            submitter="s",
            title_template="t",
            description_template="d",
            route_hint="",
        )
        self.assertTrue(match_rule(r, "I was charged twice", "desktop_client"))
        self.assertFalse(match_rule(r, "Delivery is delayed", "desktop_client"))
        self.assertFalse(match_rule(r, "I was charged twice", "web"))

    def test_upsert_rule_roundtrip(self):
        con = sqlite3.connect(":memory:")
        init_tables(con)
        r = TicketGenRule(
            rule_id=None,
            name="r3",
            enabled=True,
            priority=10,
            channel="desktop_client",
            keywords=["refund", "payment"],
            topic_hint="",
            submitter="sim",
            title_template="title",
            description_template="{sample}",
            route_hint="ROUTE_TO_HUMAN_PAYMENT",
        )
        rid = upsert_rule(con, r)
        self.assertTrue(rid > 0)


if __name__ == "__main__":
    unittest.main()
