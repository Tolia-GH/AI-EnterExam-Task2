from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


def tokenize(text: str) -> list[str]:
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
    topic: str
    confidence: float
    source: str
    latency_ms: int


class LocalNBTopicModel:
    def __init__(self, model_path: Path):
        self._model_path = model_path
        self._model = json.loads(model_path.read_text(encoding="utf-8"))

    @property
    def version(self) -> str:
        return str(self._model.get("model_version", "unknown"))

    def predict(self, text: str) -> tuple[str, float]:
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


class OpenAICompatibleLLMTopicClassifier:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    def classify(self, text: str, labels: list[str]) -> tuple[str, float]:
        prompt = (
            "你是工单主题分类器。仅输出JSON，键为topic与confidence。\n"
            f"可选topic：{labels}\n"
            "confidence取0到1。\n"
            f"工单内容：{text}\n"
        )

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
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
        content = out["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        topic = str(parsed.get("topic", "other"))
        confidence = float(parsed.get("confidence", 0.0))
        return topic, confidence


def infer_topic(
    text_masked: str,
    labels: list[str],
    local_model: LocalNBTopicModel,
    llm: OpenAICompatibleLLMTopicClassifier | None,
) -> TopicResult:
    t0 = time.time()
    if llm is not None:
        try:
            topic, conf = llm.classify(text_masked, labels=labels)
            return TopicResult(
                topic=topic if topic in labels else "other",
                confidence=max(0.0, min(1.0, conf)),
                source=f"llm:{llm._model}",
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception:
            pass

    topic, conf = local_model.predict(text_masked)
    return TopicResult(
        topic=topic,
        confidence=max(0.0, min(1.0, conf)),
        source=f"local_nb:{local_model.version}",
        latency_ms=int((time.time() - t0) * 1000),
    )
