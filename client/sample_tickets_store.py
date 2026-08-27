from __future__ import annotations

"""
JSON-based ticket store for the desktop client.

The desktop client uses a single JSON file (sample_tickets.json) as:
- template source (templates)
- local ticket history (tickets)
- offline upload queue when the backend is unavailable (pending)
- small counters used by the UI (stats)

The store functions are defensive and raise SampleTicketsError with
machine-readable reason codes.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    """Return a local timezone ISO-8601 timestamp."""

    return datetime.now().astimezone().isoformat(timespec="seconds")


class SampleTicketsError(RuntimeError):
    """Raised when the sample ticket store cannot be read or validated."""

    pass


@dataclass(frozen=True)
class Template:
    """Ticket template used by the generator."""

    template_id: str
    channel: str
    title: str
    description: str
    submitter: str


def _default_store() -> dict:
    """Create an empty store structure."""

    return {
        "version": 1,
        "templates": [],
        "tickets": [],
        "pending": [],
        "stats": {"submitted": 0},
    }


def _read_json(path: Path) -> object:
    """Read and parse JSON from a file, raising SampleTicketsError on failures."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise SampleTicketsError(f"sample_tickets_not_found: path={path.as_posix()}") from e
    except OSError as e:
        raise SampleTicketsError(f"sample_tickets_read_failed: path={path.as_posix()}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SampleTicketsError(f"sample_tickets_invalid_json: path={path.as_posix()}") from e


def load_store(path: Path) -> dict:
    """Load and validate the store structure from a JSON file."""

    obj = _read_json(path)
    if not isinstance(obj, dict):
        raise SampleTicketsError("sample_tickets_invalid_format: expected_object")

    for k in ("templates", "tickets", "pending", "stats"):
        if k not in obj:
            raise SampleTicketsError(f"sample_tickets_invalid_format: missing={k}")

    if not isinstance(obj["templates"], list) or not isinstance(obj["tickets"], list) or not isinstance(obj["pending"], list):
        raise SampleTicketsError("sample_tickets_invalid_format: templates/tickets/pending_must_be_list")
    if not isinstance(obj["stats"], dict):
        raise SampleTicketsError("sample_tickets_invalid_format: stats_must_be_object")

    return obj


def save_store(path: Path, store: dict) -> None:
    """Atomically write store to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(store, ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def ensure_store(path: Path) -> dict:
    """Ensure the store exists. If missing, create a default store with templates."""

    if path.exists():
        return load_store(path)

    store = _default_store()
    store["templates"] = [
        {
            "template_id": "tpl_delivery",
            "channel": "app",
            "title": "Delivery delay",
            "description": "The courier picked up the order but has not moved for 40 minutes. How can I urge the delivery?",
            "submitter": "simulator",
        },
        {
            "template_id": "tpl_payment",
            "channel": "web",
            "title": "Payment issue",
            "description": "I was charged twice and the order was canceled. I need a refund as soon as possible.",
            "submitter": "simulator",
        },
        {
            "template_id": "tpl_after_sales",
            "channel": "mini_program",
            "title": "After-sales request",
            "description": "My meal was missing items / damaged. How can I request compensation?",
            "submitter": "simulator",
        },
        {
            "template_id": "tpl_account",
            "channel": "app",
            "title": "Account problem",
            "description": "I forgot my password and cannot sign in. How can I recover my account?",
            "submitter": "simulator",
        },
    ]
    save_store(path, store)
    return store


def next_template(store: dict) -> dict:
    templates = store.get("templates") or []
    if not templates:
        raise SampleTicketsError("sample_tickets_no_templates")
    idx = int(store.get("stats", {}).get("template_cursor", 0))
    tpl = templates[idx % len(templates)]
    store.setdefault("stats", {})["template_cursor"] = idx + 1
    return tpl


def make_ticket_from_template(tpl: dict) -> dict:
    ticket_id = "SIM-" + uuid.uuid4().hex[:10]
    return {
        "ticket_id": ticket_id,
        "channel": str(tpl.get("channel", "desktop_client")),
        "submitter": str(tpl.get("submitter", "simulator")),
        "title": str(tpl.get("title", "Ticket")),
        "description": str(tpl.get("description", "")),
        "created_at": now_iso(),
        "status": "NEW",
    }


def record_submitted(store: dict, payload: dict) -> None:
    store.setdefault("tickets", []).append(
        {
            "ticket_id": payload.get("ticket_id"),
            "channel": payload.get("channel"),
            "submitter": payload.get("submitter"),
            "title": payload.get("title"),
            "description": payload.get("description"),
            "created_at": payload.get("created_at") or now_iso(),
            "status": "NEW",
            "updated_at": now_iso(),
        }
    )
    store.setdefault("stats", {})["submitted"] = int(store.get("stats", {}).get("submitted", 0)) + 1


def enqueue_pending(store: dict, payload: dict, error: str) -> None:
    store.setdefault("pending", []).append(
        {
            "ticket_id": payload.get("ticket_id"),
            "payload": payload,
            "created_at": now_iso(),
            "error": error,
        }
    )


def pending_batch(store: dict, limit: int) -> list[dict]:
    items = store.get("pending") or []
    return list(items[:limit])


def drop_pending_by_id(store: dict, ticket_id: str) -> None:
    items = store.get("pending") or []
    store["pending"] = [x for x in items if str(x.get("ticket_id")) != ticket_id]


def update_ticket_status(store: dict, ticket_id: str, status: str) -> None:
    if status not in {"NEW", "PROCESSING", "PENDING_REVIEW", "RESOLVED"}:
        return
    for t in store.get("tickets") or []:
        if str(t.get("ticket_id")) == ticket_id:
            t["status"] = status
            t["updated_at"] = now_iso()
            return
