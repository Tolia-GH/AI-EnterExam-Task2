from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(level: str, module: str, message: str) -> None:
    print(f"[{_now()}] [{level}] [{module}] {message}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18000)
    p.add_argument("--restart-delay-ms", type=int, default=1000)
    p.add_argument("--max-restarts", type=int, default=100)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    os.environ.setdefault("PYTHONPATH", str(project_root))

    restarts = 0
    while restarts <= args.max_restarts:
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "service.app:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
        ]
        _log("INFO", "supervisor", f"启动服务: host={args.host}; port={args.port}; restarts={restarts}")
        p = subprocess.Popen(cmd, cwd=str(project_root))
        code = p.wait()
        restarts += 1
        _log("ERROR", "supervisor", f"服务退出: exit_code={code}; 将尝试重启")
        time.sleep(max(0, args.restart_delay_ms) / 1000.0)

    _log("ERROR", "supervisor", "超过最大重启次数: status=stopped")
    raise SystemExit(1)


if __name__ == "__main__":
    main()

