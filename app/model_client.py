from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicModelResult:
    topic: str
    confidence: float
    model_version: str
    latency_ms: int


class TopicClassifierClient:
    def __init__(self, url: str, timeout_s: float):
        self._url = url.rstrip("/")
        self._timeout_s = timeout_s

    def classify(self, text: str, meta: dict | None = None) -> TopicModelResult:
        payload = json.dumps({"text": text, "meta": meta or {}}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self._url}/classify",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
            body = resp.read().decode("utf-8")
        latency_ms = int((time.time() - t0) * 1000)
        out = json.loads(body)
        return TopicModelResult(
            topic=str(out.get("topic", "other")),
            confidence=float(out.get("confidence", 0.0)),
            model_version=str(out.get("model_version", "unknown")),
            latency_ms=int(out.get("latency_ms", latency_ms)),
        )
