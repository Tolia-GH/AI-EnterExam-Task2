from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _level_value(level: str) -> int:
    return _LEVELS.get(level.upper().strip(), _LEVELS["INFO"])


def log(enabled_level: int, level: str, module: str, message: str) -> None:
    if _level_value(level) < enabled_level:
        return
    print(f"[{_now()}] [{level.upper()}] [{module}] {message}")


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


def predict(model: dict, text: str) -> tuple[str, float]:
    labels: list[str] = model["labels"]
    alpha: float = float(model["alpha"])
    priors: dict[str, float] = model["priors"]
    token_count: dict[str, dict[str, int]] = model["token_count"]
    total_tokens: dict[str, int] = model["total_tokens"]
    vocab_size: int = int(model["vocab_size"]) or 1

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


class AppState:
    def __init__(self, model: dict, enabled_level: int):
        self.model = model
        self.enabled_level = enabled_level


class Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, {"status": "ok"})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/classify":
            self._send(404, {"error": "not_found"})
            return

        state: AppState = self.server.state  # type: ignore[attr-defined]
        t0 = time.time()

        try:
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n).decode("utf-8")
            req = json.loads(raw) if raw else {}
            text = str(req.get("text", ""))
            if not text:
                self._send(400, {"error": "empty_text"})
                return

            topic, conf = predict(state.model, text)
            latency_ms = int((time.time() - t0) * 1000)
            resp = {
                "topic": topic,
                "confidence": conf,
                "model_version": state.model.get("model_version", "unknown"),
                "latency_ms": latency_ms,
            }
            self._send(200, resp)
        except Exception as e:
            latency_ms = int((time.time() - t0) * 1000)
            log(state.enabled_level, "ERROR", "server", f"推理失败: error={type(e).__name__}: {e}; latency_ms={latency_ms}")
            self._send(500, {"error": "internal_error", "latency_ms": latency_ms})

    def log_message(self, format: str, *args) -> None:
        return


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--model", default="model_service/model.json")
    p.add_argument("--log-level", default=None)
    return p.parse_args()


def _resolve_path(p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (_PROJECT_ROOT / path).resolve()



def main() -> None:
    args = parse_args()
    env_level = os.getenv("LOG_LEVEL", "INFO")
    enabled_level = _level_value(args.log_level or env_level)
    model_path = _resolve_path(args.model)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    log(enabled_level, "INFO", "server", f"模型服务启动: host={args.host}; port={args.port}; model={model_path.as_posix()}")
    log(enabled_level, "INFO", "server", f"模型加载完成: version={model.get('model_version', 'unknown')}")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.state = AppState(model=model, enabled_level=enabled_level)  # type: ignore[attr-defined]
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log(enabled_level, "WARN", "server", "收到中断信号: status=shutdown")
    finally:
        httpd.server_close()
        log(enabled_level, "INFO", "server", "服务退出: status=stopped")


if __name__ == "__main__":
    main()
