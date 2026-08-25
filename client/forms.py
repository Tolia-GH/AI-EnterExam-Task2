from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDef:
    field_id: str
    label: str
    field_type: str
    required: bool
    options: list[str] | None = None
    pattern: str | None = None


@dataclass(frozen=True)
class FormDef:
    form_id: int | None
    name: str
    fields: list[FieldDef]


def init_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_forms (
          form_id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          form_json TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS client_kv (
          k TEXT PRIMARY KEY,
          v TEXT NOT NULL
        )
        """
    )
    con.commit()


def validate_form(form: FormDef) -> list[str]:
    errors: list[str] = []
    if not form.name.strip():
        errors.append("Form name must not be empty")
    if not form.fields:
        errors.append("Form must contain at least one field")
        return errors

    seen = set()
    for f in form.fields:
        if not f.field_id.strip():
            errors.append("Field ID must not be empty")
        if f.field_id in seen:
            errors.append("Field ID must be unique")
        seen.add(f.field_id)
        if not f.label.strip():
            errors.append("Field label must not be empty")
        if f.field_type not in {"text", "select", "date", "file"}:
            errors.append("Unsupported field type")
        if f.field_type == "select":
            if not f.options or not any(o.strip() for o in f.options):
                errors.append("Select field requires at least one option")
        if f.pattern:
            try:
                re.compile(f.pattern)
            except re.error:
                errors.append("Invalid regex pattern")
    return errors


def _to_json(form: FormDef) -> str:
    payload = {
        "name": form.name,
        "fields": [
            {
                "field_id": f.field_id,
                "label": f.label,
                "field_type": f.field_type,
                "required": f.required,
                "options": f.options,
                "pattern": f.pattern,
            }
            for f in form.fields
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _from_json(form_id: int, raw: str) -> FormDef:
    o = json.loads(raw)
    fields = []
    for f in o.get("fields", []):
        fields.append(
            FieldDef(
                field_id=str(f.get("field_id", "")),
                label=str(f.get("label", "")),
                field_type=str(f.get("field_type", "text")),
                required=bool(f.get("required", False)),
                options=list(f.get("options") or []) or None,
                pattern=f.get("pattern"),
            )
        )
    return FormDef(form_id=form_id, name=str(o.get("name", "")), fields=fields)


def list_forms(con: sqlite3.Connection) -> list[FormDef]:
    cur = con.execute("SELECT form_id, form_json FROM ticket_forms ORDER BY form_id ASC")
    out: list[FormDef] = []
    for r in cur.fetchall():
        out.append(_from_json(int(r[0]), str(r[1])))
    return out


def upsert_form(con: sqlite3.Connection, form: FormDef) -> int:
    form_json = _to_json(form)
    if form.form_id is None:
        cur = con.execute("INSERT INTO ticket_forms(name, form_json) VALUES(?,?)", (form.name.strip(), form_json))
        con.commit()
        return int(cur.lastrowid)
    con.execute(
        "UPDATE ticket_forms SET name=?, form_json=? WHERE form_id=?",
        (form.name.strip(), form_json, int(form.form_id)),
    )
    con.commit()
    return int(form.form_id)


def delete_form(con: sqlite3.Connection, form_id: int) -> None:
    con.execute("DELETE FROM ticket_forms WHERE form_id=?", (int(form_id),))
    con.commit()


def get_kv(con: sqlite3.Connection, k: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT v FROM client_kv WHERE k=?", (k,)).fetchone()
    if row is None:
        return default
    return str(row[0])


def set_kv(con: sqlite3.Connection, k: str, v: str) -> None:
    con.execute(
        "INSERT INTO client_kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (k, v),
    )
    con.commit()
