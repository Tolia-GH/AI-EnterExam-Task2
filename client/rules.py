from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class TicketGenRule:
    rule_id: int | None
    name: str
    enabled: bool
    priority: int
    channel: str
    keywords: list[str]
    topic_hint: str
    submitter: str
    title_template: str
    description_template: str
    route_hint: str


def init_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_gen_rules (
          rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          enabled INTEGER NOT NULL,
          priority INTEGER NOT NULL,
          channel TEXT NOT NULL,
          keywords_json TEXT NOT NULL,
          topic_hint TEXT NOT NULL,
          submitter TEXT NOT NULL,
          title_template TEXT NOT NULL,
          description_template TEXT NOT NULL,
          route_hint TEXT NOT NULL
        )
        """
    )
    con.commit()


def validate_rule(rule: TicketGenRule) -> list[str]:
    errors: list[str] = []
    if not rule.name.strip():
        errors.append("Rule name must not be empty")
    if rule.priority < 0 or rule.priority > 1_000_000:
        errors.append("Priority must be in range [0, 1000000]")
    if not rule.title_template.strip():
        errors.append("Title template must not be empty")
    if not rule.description_template.strip():
        errors.append("Description template must not be empty")

    triggers = 0
    if rule.channel.strip():
        triggers += 1
    if rule.topic_hint.strip():
        triggers += 1
    if any(k.strip() for k in rule.keywords):
        triggers += 1
    if triggers == 0:
        errors.append("At least one trigger must be configured: channel/topic_hint/keywords")

    for k in rule.keywords:
        if len(k.strip()) > 40:
            errors.append("Keyword is too long (>40)")
            break
    return errors


def list_rules(con: sqlite3.Connection) -> list[TicketGenRule]:
    cur = con.execute(
        """
        SELECT rule_id,name,enabled,priority,channel,keywords_json,topic_hint,submitter,title_template,description_template,route_hint
        FROM ticket_gen_rules
        ORDER BY priority DESC, rule_id ASC
        """
    )
    out: list[TicketGenRule] = []
    for r in cur.fetchall():
        out.append(
            TicketGenRule(
                rule_id=int(r[0]),
                name=str(r[1]),
                enabled=bool(int(r[2])),
                priority=int(r[3]),
                channel=str(r[4]),
                keywords=list(json.loads(str(r[5]))),
                topic_hint=str(r[6]),
                submitter=str(r[7]),
                title_template=str(r[8]),
                description_template=str(r[9]),
                route_hint=str(r[10]),
            )
        )
    return out


def upsert_rule(con: sqlite3.Connection, rule: TicketGenRule) -> int:
    keywords_json = json.dumps([k.strip() for k in rule.keywords if k.strip()], ensure_ascii=False)
    if rule.rule_id is None:
        cur = con.execute(
            """
            INSERT INTO ticket_gen_rules(name,enabled,priority,channel,keywords_json,topic_hint,submitter,title_template,description_template,route_hint)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rule.name.strip(),
                1 if rule.enabled else 0,
                int(rule.priority),
                rule.channel.strip(),
                keywords_json,
                rule.topic_hint.strip(),
                rule.submitter.strip() or "simulator",
                rule.title_template,
                rule.description_template,
                rule.route_hint.strip(),
            ),
        )
        con.commit()
        return int(cur.lastrowid)

    con.execute(
        """
        UPDATE ticket_gen_rules
        SET name=?, enabled=?, priority=?, channel=?, keywords_json=?, topic_hint=?, submitter=?, title_template=?, description_template=?, route_hint=?
        WHERE rule_id=?
        """,
        (
            rule.name.strip(),
            1 if rule.enabled else 0,
            int(rule.priority),
            rule.channel.strip(),
            keywords_json,
            rule.topic_hint.strip(),
            rule.submitter.strip() or "simulator",
            rule.title_template,
            rule.description_template,
            rule.route_hint.strip(),
            int(rule.rule_id),
        ),
    )
    con.commit()
    return int(rule.rule_id)


def delete_rule(con: sqlite3.Connection, rule_id: int) -> None:
    con.execute("DELETE FROM ticket_gen_rules WHERE rule_id=?", (int(rule_id),))
    con.commit()


def match_rule(rule: TicketGenRule, text: str, channel: str) -> bool:
    if rule.channel.strip() and rule.channel.strip() != channel:
        return False
    if rule.topic_hint.strip() and rule.topic_hint.strip() not in text:
        return False
    kws = [k.strip() for k in rule.keywords if k.strip()]
    if kws and not any(k in text for k in kws):
        return False
    return True
