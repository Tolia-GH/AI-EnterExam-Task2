from __future__ import annotations

import queue
import sqlite3
import threading
import time
from dataclasses import dataclass

from app.poc import classify_risk, mask_pii

from .db import DB, insert_event, update_ticket_processing
from .model_infer import LocalNBTopicModel, OpenAICompatibleLLMResponder, infer_topic
from .routing import RoutingRules, route
from .settings import Settings


@dataclass(frozen=True)
class Event:
    ticket_id: str
    ts: str
    level: str
    module: str
    message: str


class PubSub:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[Event]] = []

    def subscribe(self) -> queue.Queue[Event]:
        q: queue.Queue[Event] = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[Event]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, ev: Event) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except Exception:
                continue


def _select_pending(con: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    cur = con.execute(
        "SELECT * FROM tickets WHERE status='NEW' ORDER BY created_at ASC LIMIT ?",
        (limit,),
    )
    return list(cur.fetchall())


def _auto_receipt(topic: str) -> str:
    if topic == "order_delivery":
        return (
            "We have processed your request automatically. "
            "Please try checking the latest delivery status and contacting the courier in the app. "
            "If the issue persists, we will escalate it for human follow-up."
        )
    return "We have processed your request automatically. A support receipt has been generated."


class TicketWorker:
    def __init__(self, settings: Settings, db: DB, rules: RoutingRules, pubsub: PubSub):
        self._settings = settings
        self._db = db
        self._rules = rules
        self._pubsub = pubsub
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._local_model = LocalNBTopicModel(settings.model_local_path)
        self._responder = None
        if settings.llm_base_url and settings.llm_api_key and settings.llm_model:
            self._responder = OpenAICompatibleLLMResponder(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout_s=2.0,
            )

    def start(self) -> None:
        if self._thread is not None:
            return
        t = threading.Thread(target=self._run, daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _emit(self, ticket_id: str, ts: str, level: str, module: str, message: str) -> None:
        self._pubsub.publish(Event(ticket_id=ticket_id, ts=ts, level=level, module=module, message=message))

    def _run(self) -> None:
        while not self._stop.is_set():
            con = self._db.connect()
            try:
                rows = _select_pending(con, limit=20)
                if not rows:
                    time.sleep(0.2)
                    continue

                for r in rows:
                    ticket_id = str(r["ticket_id"])
                    title = str(r["title"])
                    desc = str(r["description"])
                    text = f"{title}\n{desc}"
                    text_masked = mask_pii(text)

                    insert_event(con, ticket_id, "INFO", "worker", "Ticket processing started")
                    con.commit()

                    topic_res = infer_topic(
                        text_masked=text_masked,
                        labels=["order_delivery", "payment", "after_sales", "account", "other"],
                        local_model=self._local_model,
                    )

                    risk = classify_risk(text)
                    route_action = route(topic_res.topic, self._rules)

                    status = "PROCESSING"
                    receipt = None
                    receipt_source = "none"
                    if risk == "risky":
                        status = "PENDING_REVIEW"
                    elif topic_res.confidence < self._settings.topic_confidence_threshold:
                        status = "PENDING_REVIEW"
                    elif topic_res.topic in self._settings.auto_close_topics:
                        status = "RESOLVED"
                        receipt = _auto_receipt(topic_res.topic)
                        receipt_source = "template"
                        if self._responder is not None:
                            try:
                                rr = self._responder.generate_reply(text_masked=text_masked, topic=topic_res.topic)
                                if rr.reply.strip():
                                    receipt = rr.reply
                                    receipt_source = rr.source
                            except Exception:
                                receipt_source = "template_fallback"
                    else:
                        status = "PENDING_REVIEW"

                    reason = []
                    if risk == "risky":
                        reason.append("risky_policy")
                    if topic_res.confidence < self._settings.topic_confidence_threshold:
                        reason.append("low_confidence")
                    if status == "RESOLVED":
                        reason.append("auto_close")
                    if status == "PENDING_REVIEW":
                        reason.append("human_in_loop")

                    update_ticket_processing(
                        con,
                        ticket_id=ticket_id,
                        topic=topic_res.topic,
                        confidence=topic_res.confidence,
                        risk_level=risk,
                        route_action=route_action,
                        status=status,
                        receipt=receipt,
                    )
                    insert_event(
                        con,
                        ticket_id,
                        "INFO" if status == "RESOLVED" else ("WARN" if status == "PENDING_REVIEW" else "INFO"),
                        "worker",
                        "Ticket processed: "
                        f"topic={topic_res.topic}; conf={topic_res.confidence:.4f}; "
                        f"risk={risk}; route={route_action}; status={status}; source={topic_res.source}; "
                        f"receipt_source={receipt_source}; reason={','.join(reason)}",
                    )
                    con.commit()

                    ts = str(con.execute("SELECT updated_at FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()[0])
                    self._emit(ticket_id, ts, "INFO", "worker", f"Status updated: status={status}; route={route_action}")
            finally:
                con.close()
