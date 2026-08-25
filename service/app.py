from __future__ import annotations

import json
import queue
import sqlite3
from datetime import datetime, timezone
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .db import DB, init_db, insert_event, now_iso, upsert_ticket
from .routing import load_routing_rules
from .schemas import MetricsResponse, TicketCreateRequest, TicketResponse
from .settings import load_settings
from .worker import PubSub, TicketWorker


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


settings = load_settings()
db = DB(path=settings.db_path)
init_db(db)

routing_rules = load_routing_rules(settings.routing_rules_path)
pubsub = PubSub()
worker = TicketWorker(settings=settings, db=db, rules=routing_rules, pubsub=pubsub)
worker.start()

app = FastAPI(title="Ticket Automation Service", version="0.1")


def _row_to_ticket(r: sqlite3.Row) -> TicketResponse:
    return TicketResponse(
        ticket_id=str(r["ticket_id"]),
        created_at=str(r["created_at"]),
        channel=str(r["channel"]),
        submitter=r["submitter"],
        title=str(r["title"]),
        description=str(r["description"]),
        topic=r["topic"],
        topic_confidence=r["topic_confidence"],
        risk_level=r["risk_level"],
        route_action=r["route_action"],
        status=str(r["status"]),
        receipt=r["receipt"],
        updated_at=str(r["updated_at"]),
    )


@app.post("/tickets", response_model=TicketResponse)
def create_ticket(req: TicketCreateRequest) -> TicketResponse:
    con = db.connect()
    try:
        created_at = _iso_now()
        upsert_ticket(
            con,
            ticket_id=req.ticket_id,
            created_at=created_at,
            channel=req.channel,
            submitter=req.submitter,
            title=req.title,
            description=req.description,
            status="NEW",
        )
        insert_event(con, req.ticket_id, "INFO", "api", "工单创建")
        con.commit()
        row = con.execute("SELECT * FROM tickets WHERE ticket_id=?", (req.ticket_id,)).fetchone()
        assert row is not None
        return _row_to_ticket(row)
    finally:
        con.close()


@app.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(ticket_id: str) -> TicketResponse:
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        return _row_to_ticket(row)
    finally:
        con.close()


@app.get("/tickets", response_model=list[TicketResponse])
def list_tickets(status: str | None = None, limit: int = 50) -> list[TicketResponse]:
    con = db.connect()
    try:
        if status:
            cur = con.execute(
                "SELECT * FROM tickets WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = con.execute("SELECT * FROM tickets ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_row_to_ticket(r) for r in cur.fetchall()]
    finally:
        con.close()


@app.get("/metrics", response_model=MetricsResponse)
def metrics() -> MetricsResponse:
    con = db.connect()
    try:
        total = int(con.execute("SELECT COUNT(1) FROM tickets").fetchone()[0])

        status_counts = {}
        for r in con.execute("SELECT status, COUNT(1) AS c FROM tickets GROUP BY status").fetchall():
            status_counts[str(r["status"])] = int(r["c"])

        route_counts = {}
        for r in con.execute("SELECT route_action, COUNT(1) AS c FROM tickets WHERE route_action IS NOT NULL GROUP BY route_action").fetchall():
            route_counts[str(r["route_action"])] = int(r["c"])

        degrade_count = int(
            con.execute(
                "SELECT COUNT(1) FROM tickets WHERE status='PENDING_REVIEW' AND (risk_level='risky' OR topic_confidence < ?)",
                (settings.topic_confidence_threshold,),
            ).fetchone()[0]
        )
        return MetricsResponse(
            total_tickets=total,
            status_counts=status_counts,
            route_counts=route_counts,
            degrade_count=degrade_count,
        )
    finally:
        con.close()


def _sse_format(line: str) -> str:
    return "data: " + line + "\n\n"


@app.get("/events")
def events() -> StreamingResponse:
    qsub: queue.Queue = pubsub.subscribe()

    def gen() -> Iterator[bytes]:
        try:
            yield _sse_format(f"[{now_iso()}] [INFO] [events] connected").encode("utf-8")
            while True:
                ev = qsub.get()
                line = f"[{ev.ts}] [{ev.level}] [{ev.module}] {ev.message}; ticket_id={ev.ticket_id}"
                yield _sse_format(line).encode("utf-8")
        finally:
            pubsub.unsubscribe(qsub)

    return StreamingResponse(gen(), media_type="text/event-stream")
