from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from runtime_config import load_runtime_config
from service.model_infer import LocalNBTopicModel


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    channel: str
    created_at: str
    user_id: str
    text: str
    order_id: str | None = None
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class Evidence:
    source: str
    doc_id: str
    title: str
    score: float


@dataclass(frozen=True)
class Decision:
    topic: str
    risk_level: str
    confidence: float
    action: str
    reason: str
    draft_reply: str | None
    evidence: list[Evidence]


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    timestamp: str
    ticket_id: str
    channel: str
    input_text_masked: str
    topic: str
    risk_level: str
    confidence: float
    action: str
    reason: str
    evidence: list[dict[str, Any]]
    versions: dict[str, str]
    errors: list[str] | None = None


def normalize_ticket(raw: dict) -> Ticket:
    return Ticket(
        ticket_id=str(raw.get("ticket_id", "")),
        channel=str(raw.get("channel", "")),
        created_at=str(raw.get("created_at", "")),
        user_id=str(raw.get("user_id", "")),
        text=str(raw.get("text", "")),
        order_id=raw.get("order_id"),
        meta=raw.get("meta"),
    )


_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_CARD_RE = re.compile(r"(?<!\d)(\d{16,19})(?!\d)")


def mask_pii(text: str) -> str:
    text = _PHONE_RE.sub("1**********", text)
    text = _EMAIL_RE.sub("***@***", text)
    text = _CARD_RE.sub("****************", text)
    return text


def contains_pii(text: str) -> bool:
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text) or _CARD_RE.search(text))


_NB_MODEL: LocalNBTopicModel | None = None
_NB_MODEL_ERROR: str | None = None


def _get_nb_model() -> LocalNBTopicModel | None:
    global _NB_MODEL
    global _NB_MODEL_ERROR

    if _NB_MODEL is not None:
        return _NB_MODEL
    if _NB_MODEL_ERROR is not None:
        return None

    rc = load_runtime_config()
    model_path = rc.local_topic_model_path
    try:
        if model_path.exists():
            _NB_MODEL = LocalNBTopicModel(model_path)
            return _NB_MODEL
        _NB_MODEL_ERROR = f"missing_model_file={model_path.as_posix()}"
        return None
    except Exception as e:
        _NB_MODEL_ERROR = f"nb_model_load_error={type(e).__name__}: {e}"
        return None


def _classify_topic_rule(text: str) -> tuple[str, float]:
    scores: dict[str, int] = {}
    for topic, keywords in config.TOPIC_KEYWORDS.items():
        scores[topic] = sum(1 for kw in keywords if kw in text)

    best_topic = "other"
    best_score = 0
    second_score = 0
    for topic, score in scores.items():
        if score > best_score:
            second_score = best_score
            best_score = score
            best_topic = topic
        elif score > second_score:
            second_score = score

    if best_score == 0:
        return "other", 0.0

    margin = best_score - second_score
    confidence = min(1.0, 0.5 + 0.2 * best_score + 0.1 * margin)
    return best_topic, float(confidence)


def classify_topic(text: str) -> tuple[str, float, str, list[str]]:
    errors: list[str] = []
    model = _get_nb_model()
    if model is not None:
        try:
            topic, confidence = model.predict(mask_pii(text))
            if topic not in config.TOPIC_KEYWORDS and topic != "other":
                topic = "other"
            return topic, float(confidence), f"local_nb:{model.version}", errors
        except Exception as e:
            errors.append(f"nb_infer_error={type(e).__name__}: {e}")

    if _NB_MODEL_ERROR:
        errors.append(_NB_MODEL_ERROR)
    topic, confidence = _classify_topic_rule(text)
    return topic, float(confidence), "rule_fallback", errors


def classify_risk(text: str) -> str:
    if contains_pii(text):
        return "risky"
    if any(kw in text for kw in config.RISKY_KEYWORDS):
        return "risky"
    return "safe"


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> set[str]:
    return set(t.lower() for t in _TOKEN_RE.findall(text))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def load_kb(path: str | Path) -> list[dict]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def retrieve_topk(query: str, kb_docs: list[dict], k: int = 3) -> list[Evidence]:
    q = _tokenize(query)
    scored: list[Evidence] = []
    for d in kb_docs:
        t = _tokenize(str(d.get("title", "")) + " " + str(d.get("text", "")))
        score = _jaccard(q, t)
        if math.isfinite(score) and score > 0:
            scored.append(
                Evidence(
                    source="kb",
                    doc_id=str(d.get("id", "")),
                    title=str(d.get("title", "")),
                    score=float(score),
                )
            )

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:k]


def decide_action(topic: str, risk_level: str, confidence: float) -> tuple[str, str]:
    if risk_level == "risky":
        if topic == "payment":
            return "ROUTE_TO_HUMAN_PAYMENT", "risky_policy"
        if topic == "account":
            return "ROUTE_TO_HUMAN_ACCOUNT_SECURITY", "risky_policy"
        return "ROUTE_TO_HUMAN_AFTER_SALES", "risky_policy"

    if confidence < config.CONFIDENCE_THRESHOLD:
        return "ROUTE_TO_HUMAN_GENERAL", "low_confidence"

    return "AUTO_SUGGEST", "safe_high_confidence"


def draft_reply(ticket: Ticket, topic: str, evidence: list[Evidence]) -> str:
    top = evidence[0].title if evidence else "a relevant help article"
    if topic == "order_delivery":
        return (
            "We have received your request. Please check the order page and try contacting the courier. "
            f"If there is no progress for a long time, please open an after-sales request. Reference: {top}"
        )
    if topic == "after_sales":
        return (
            "We have received your request. Please submit an after-sales/refund request from the order page. "
            f"We will handle it based on order status and merchant feedback. Reference: {top}"
        )
    if topic == "account":
        return (
            "We have received your request. Please update your account details in the security settings. "
            f"If you suspect account compromise, reset your password immediately. Reference: {top}"
        )
    if topic == "payment":
        return (
            "We have received your request. Payment-related issues require human review for safety. "
            f"Please provide the order ID and payment proof. Reference: {top}"
        )
    return f"We have received your request and will assist you shortly. Reference: {top}"


def process_ticket(
    ticket: Ticket,
    kb_docs: list[dict],
) -> tuple[Decision, AuditRecord]:
    topic, confidence, topic_source, errors = classify_topic(ticket.text)
    risk_level = classify_risk(ticket.text)
    preferred_kb = [d for d in kb_docs if str(d.get("category", "")) == topic]
    evidence = retrieve_topk(ticket.text, preferred_kb or kb_docs, k=3)
    action, reason = decide_action(topic, risk_level, confidence)

    draft: str | None = None
    if action == "AUTO_SUGGEST":
        draft = draft_reply(ticket, topic, evidence)

    masked = mask_pii(ticket.text)
    audit = AuditRecord(
        audit_id=new_audit_id(),
        timestamp=now_iso(),
        ticket_id=ticket.ticket_id,
        channel=ticket.channel,
        input_text_masked=masked,
        topic=topic,
        risk_level=risk_level,
        confidence=confidence,
        action=action,
        reason=reason,
        evidence=[
            {"source": e.source, "doc_id": e.doc_id, "title": e.title, "score": e.score}
            for e in evidence
        ],
        versions={
            "system": config.SYSTEM_NAME,
            "policy": config.POLICY_VERSION,
            "topic_classifier": topic_source,
            "classifier": config.CLASSIFIER_VERSION,
            "retrieval": config.RETRIEVAL_VERSION,
        },
        errors=errors or None,
    )
    decision = Decision(
        topic=topic,
        risk_level=risk_level,
        confidence=confidence,
        action=action,
        reason=reason,
        draft_reply=draft,
        evidence=evidence,
    )
    return decision, audit


def append_audit_record(path: str | Path, record: AuditRecord) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_audit_id() -> str:
    return uuid.uuid4().hex
