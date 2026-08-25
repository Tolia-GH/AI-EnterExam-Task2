from __future__ import annotations

"""
CLI entrypoint: Support Ticket Automation PoC

This script runs an end-to-end demo:
sample inputs -> processing pipeline -> audit log -> standardized workflow logs.

Main flow (per run):
1) Parse args and log level (via --log-level or LOG_LEVEL env)
2) Load KB and sample tickets
3) Initialize audit output (JSONL) and clear previous run artifacts
4) Process each ticket:
   - Normalize ticket
   - Topic + risk classification, retrieval, routing decision, optional draft generation
   - Append audit record (JSONL)
   - Emit standardized workflow logs
5) Exit with traceable logs on both success and failure
"""

import argparse
import os
import json
from datetime import datetime
from pathlib import Path

from app.poc import append_audit_record, load_kb, normalize_ticket, process_ticket
from runtime_config import load_runtime_config


_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_PROJECT_ROOT = Path(__file__).resolve().parent


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_level(level: str) -> str:
    return level.strip().upper()


def _get_level_value(level: str) -> int:
    return _LEVELS.get(_normalize_level(level), _LEVELS["INFO"])


def _log(enabled_level_value: int, level: str, module: str, message: str) -> None:
    level = _normalize_level(level)
    if _get_level_value(level) < enabled_level_value:
        return
    print(f"[{_now()}] [{level}] [{module}] {message}")


def _resolve_path(p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (_PROJECT_ROOT / path).resolve()


def parse_args() -> argparse.Namespace:
    """
    Parse CLI args.

    - --tickets: input tickets (JSON array)
    - --kb: local KB (JSON array)
    - --audit: audit output path (JSONL)
    - --log-level: log level override (LOG_LEVEL env)
    """
    rc = load_runtime_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickets", default=str(rc.poc_tickets_path))
    parser.add_argument("--kb", default=str(rc.kb_path))
    parser.add_argument("--audit", default=str(rc.audit_path))
    parser.add_argument("--log-level", default=None)
    return parser.parse_args()


def safe_unlink(path: Path) -> None:
    """
    Cleanup run artifacts. If the file exists, delete it; otherwise ignore.

    Used to clear the audit log before each run, so the demo output is reproducible.
    """
    try:
        path.unlink()
    except FileNotFoundError:
        return


def main() -> None:
    """
    Program entrypoint.

    This function must not print raw JSON/objects; it only emits standardized workflow logs.
    """
    args = parse_args()
    env_level = os.getenv("LOG_LEVEL", "INFO")
    enabled_level_value = _get_level_value(args.log_level or env_level)

    tickets_path = _resolve_path(args.tickets)
    kb_path = _resolve_path(args.kb)
    audit_path = _resolve_path(args.audit)

    _log(
        enabled_level_value,
        "INFO",
        "main",
        "Process started: "
        f"tickets={tickets_path.as_posix()}; kb={kb_path.as_posix()}; audit={audit_path.as_posix()}; "
        f"log_level={args.log_level or env_level}",
    )

    try:
        _log(enabled_level_value, "INFO", "loader", f"Loading KB: path={kb_path.as_posix()}")
        kb_docs = load_kb(kb_path)
        _log(enabled_level_value, "INFO", "loader", f"KB loaded: docs={len(kb_docs)}")

        _log(enabled_level_value, "INFO", "loader", f"Loading tickets: path={tickets_path.as_posix()}")
        raw_tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
        _log(enabled_level_value, "INFO", "loader", f"Tickets loaded: tickets={len(raw_tickets)}")

        _log(enabled_level_value, "DEBUG", "audit", f"Initializing audit log: path={audit_path.as_posix()}")
        safe_unlink(audit_path)

        for idx, raw in enumerate(raw_tickets, start=1):
            ticket = normalize_ticket(raw)
            _log(
                enabled_level_value,
                "INFO",
                "pipeline",
                f"Ticket processing started: index={idx}; ticket_id={ticket.ticket_id}; channel={ticket.channel}; order_id={ticket.order_id or '-'}",
            )

            try:
                decision, audit = process_ticket(ticket, kb_docs)
            except Exception as e:
                _log(
                    enabled_level_value,
                    "ERROR",
                    "pipeline",
                    f"Ticket processing failed: ticket_id={ticket.ticket_id}; error={type(e).__name__}: {e}",
                )
                continue

            level = "WARN" if decision.risk_level == "risky" else "INFO"
            _log(
                enabled_level_value,
                level,
                "pipeline",
                "Ticket decision: "
                f"ticket_id={ticket.ticket_id}; topic={decision.topic}; risk={decision.risk_level}; "
                f"confidence={decision.confidence:.4f}; action={decision.action}; reason={decision.reason}",
            )

            if decision.evidence:
                top = decision.evidence[0]
                _log(
                    enabled_level_value,
                    "DEBUG",
                    "retrieval",
                    f"Evidence retrieved: ticket_id={ticket.ticket_id}; top1_doc={top.doc_id}; title={top.title}; score={top.score:.4f}",
                )
            else:
                _log(enabled_level_value, "DEBUG", "retrieval", f"No evidence retrieved: ticket_id={ticket.ticket_id}")

            if decision.draft_reply:
                _log(enabled_level_value, "INFO", "draft", f"Draft generated: ticket_id={ticket.ticket_id}")

            append_audit_record(audit_path, audit)
            _log(
                enabled_level_value,
                "INFO",
                "audit",
                f"Audit record appended: ticket_id={ticket.ticket_id}; audit_id={audit.audit_id}; path={audit_path.as_posix()}",
            )

            if audit.errors:
                _log(
                    enabled_level_value,
                    "WARN",
                    "pipeline",
                    f"Degradation detected: ticket_id={ticket.ticket_id}; details={'; '.join(audit.errors)}",
                )

        _log(enabled_level_value, "INFO", "main", f"Process finished: status=success; audit_log={audit_path.as_posix()}")
    except Exception as e:
        _log(enabled_level_value, "ERROR", "main", f"Process failed: error={type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
