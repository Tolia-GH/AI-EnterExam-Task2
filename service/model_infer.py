from __future__ import annotations

"""
Model inference utilities for the backend service.

This module contains:
- LocalNBTopicModel: lightweight topic classifier for the hot path
- infer_topic(): adapter that returns a typed TopicResult with latency/source fields
- OpenAICompatibleLLMResponder: optional external LLM integration used only for
  response generation (slow path), with PII masked inputs and fallback handled by the caller
"""

import json
import math
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


def tokenize(text: str) -> list[str]:
    """Tokenize Latin alnum words and CJK characters for the NB model."""

    tokens: list[str] = []
    buff: list[str] = []
    for ch in text:
        o = ord(ch)
        if "0" <= ch <= "9" or "a" <= ch.lower() <= "z":
            buff.append(ch.lower())
            continue
        if buff:
            tokens.append("".join(buff))
            buff = []
        if 0x4E00 <= o <= 0x9FFF:
            tokens.append(ch)
    if buff:
        tokens.append("".join(buff))
    return tokens


@dataclass(frozen=True)
class TopicResult:
    """Topic inference result returned to the caller."""

    topic: str
    confidence: float
    source: str
    latency_ms: int


class LocalNBTopicModel:
    """Local Multinomial Naive Bayes topic model loaded from a JSON artifact."""

    def __init__(self, model_path: Path):
        self._model_path = model_path
        self._model = json.loads(model_path.read_text(encoding="utf-8"))

    @property
    def version(self) -> str:
        """Model version string stored in the artifact."""

        return str(self._model.get("model_version", "unknown"))

    def predict(self, text: str) -> tuple[str, float]:
        """Predict (topic, confidence) for a given masked text input."""

        labels: list[str] = self._model["labels"]
        alpha: float = float(self._model["alpha"])
        priors: dict[str, float] = self._model["priors"]
        token_count: dict[str, dict[str, int]] = self._model["token_count"]
        total_tokens: dict[str, int] = self._model["total_tokens"]
        vocab_size: int = int(self._model["vocab_size"]) or 1

        toks = tokenize(text)
        logps: dict[str, float] = {}
        for l in labels:
            lp = math.log(max(priors.get(l, 1e-12), 1e-12))
            denom = total_tokens.get(l, 0) + alpha * vocab_size
            for t in toks:
                c = token_count.get(l, {}).get(t, 0)
                lp += math.log((c + alpha) / denom)
            logps[l] = lp

        m = max(logps.values()) if logps else 0.0
        exps = {l: math.exp(v - m) for l, v in logps.items()}
        z = sum(exps.values()) or 1.0
        probs = {l: exps[l] / z for l in labels}
        topic, conf = max(probs.items(), key=lambda x: x[1])
        return topic, float(conf)


@dataclass(frozen=True)
class ReplyResult:
    """Response generation result with metadata for observability."""

    reply: str
    source: str
    latency_ms: int


class OpenAICompatibleLLMResponder:
    """OpenAI-compatible chat completion client used for response generation."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    @property
    def model(self) -> str:
        """Configured model id (provider specific)."""

        return self._model

    def generate_reply(self, text_masked: str, topic: str) -> ReplyResult:
        """Generate a short English reply for a masked ticket text."""

        t0 = time.time()
        prompt = "\n".join(
            [
                "You are a customer support agent for a food delivery platform.",
                "Generate a concise and professional reply to the user ticket in English.",
                "Do not ask for sensitive information (password, full card number, OTP).",
                "If the issue requires a human agent, say that it has been escalated for review.",
                f"Topic: {topic}",
                "",
                "Ticket (PII masked):",
                text_masked,
            ]
        )
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
            raw = resp.read().decode("utf-8")
        out = json.loads(raw)
        content = str(out["choices"][0]["message"]["content"]).strip()
        return ReplyResult(
            reply=content,
            source=f"llm:{self._model}",
            latency_ms=int((time.time() - t0) * 1000),
        )


def infer_topic(
    text_masked: str,
    labels: list[str],
    local_model: LocalNBTopicModel,
) -> TopicResult:
    """Infer topic for masked text using the local NB model."""

    t0 = time.time()
    topic, conf = local_model.predict(text_masked)
    if topic not in labels:
        topic = "other"
    return TopicResult(
        topic=topic,
        confidence=max(0.0, min(1.0, conf)),
        source=f"local_nb:{local_model.version}",
        latency_ms=int((time.time() - t0) * 1000),
    )
