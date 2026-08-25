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


def classify_topic(text: str) -> tuple[str, float]:
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


def classify_risk(text: str) -> str:
    if contains_pii(text):
        return "risky"
    if any(kw in text for kw in config.RISKY_KEYWORDS):
        return "risky"
    return "safe"


_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")


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
    top = evidence[0].title if evidence else "相关帮助文档"
    if topic == "order_delivery":
        return f"已收到反馈。建议先在订单页点击“联系骑手/催单”，若长时间无更新可在售后入口发起申诉。参考：{top}"
    if topic == "after_sales":
        return f"已收到反馈。可在订单页进入“售后/退款”提交申请，我们会根据订单状态与商家反馈处理。参考：{top}"
    if topic == "account":
        return f"已收到反馈。可在“账号与安全”中修改信息或发起找回申诉；若疑似被盗请优先修改密码并开启安全验证。参考：{top}"
    if topic == "payment":
        return f"已收到反馈。支付/资金问题需要人工核查以保证安全，请提供订单号与支付凭证后由专席处理。参考：{top}"
    return f"已收到反馈，我们会尽快协助处理。参考：{top}"


def append_audit_record(path: str | Path, record: AuditRecord) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_audit_id() -> str:
    return uuid.uuid4().hex
