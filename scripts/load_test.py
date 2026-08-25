from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime

from runtime_config import load_runtime_config


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(level: str, module: str, message: str) -> None:
    print(f"[{_now()}] [{level}] [{module}] {message}")


def parse_args() -> argparse.Namespace:
    rc = load_runtime_config()
    p = argparse.ArgumentParser()
    p.add_argument("--api-base", default=rc.api_base)
    p.add_argument("--threads", type=int, default=10)
    p.add_argument("--tickets-per-thread", type=int, default=50)
    return p.parse_args()


def post_ticket(api_base: str, payload: dict, timeout_s: float) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/tickets",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        resp.read()


def worker(api_base: str, n: int, idx: int, timeout_s: float, results: dict) -> None:
    for i in range(n):
        tid = f"LT-{idx}-{uuid.uuid4().hex[:10]}"
        payload = {
            "ticket_id": tid,
            "channel": "load_test",
            "submitter": "load_test",
            "title": "压测工单",
            "description": "订单一直显示骑手已取餐但很久没动了，怎么催单？",
        }
        ok = False
        for _ in range(3):
            try:
                post_ticket(api_base, payload, timeout_s=timeout_s)
                ok = True
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        if ok:
            results["ok"] += 1
        else:
            results["fail"] += 1


def main() -> None:
    args = parse_args()
    _log("INFO", "load_test", f"开始压测: threads={args.threads}; per_thread={args.tickets_per_thread}")
    t0 = time.time()
    threads = []
    results = {"ok": 0, "fail": 0}
    for i in range(args.threads):
        t = threading.Thread(
            target=worker,
            args=(args.api_base, args.tickets_per_thread, i, 8.0, results),
            daemon=True,
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0
    total = args.threads * args.tickets_per_thread
    _log(
        "INFO",
        "load_test",
        f"压测完成: total={total}; ok={results['ok']}; fail={results['fail']}; seconds={dt:.3f}; qps={results['ok']/max(dt,1e-6):.2f}",
    )


if __name__ == "__main__":
    main()
