from __future__ import annotations

"""
Simple supervisor for running the backend as a daemon-like process.

This script launches uvicorn and restarts it if the process exits.
It is intentionally minimal (no external dependencies) and suitable for PoC demos.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from runtime_config import load_runtime_config


def _now() -> str:
    """Return a local timezone ISO-8601 timestamp."""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(level: str, module: str, message: str) -> None:
    """Emit a standardized workflow log line."""

    print(f"[{_now()}] [{level}] [{module}] {message}")


def parse_args() -> argparse.Namespace:
    """Parse CLI args with defaults from runtime_config."""

    rc = load_runtime_config()
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=rc.backend_host)
    p.add_argument("--port", type=int, default=rc.backend_port)
    p.add_argument("--restart-delay-ms", type=int, default=1000)
    p.add_argument("--max-restarts", type=int, default=100)
    return p.parse_args()


def main() -> None:
    """Run uvicorn under supervision with auto-restart."""

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
        _log("INFO", "supervisor", f"Service starting: host={args.host}; port={args.port}; restarts={restarts}")
        p = subprocess.Popen(cmd, cwd=str(project_root))
        code = p.wait()
        restarts += 1
        _log("ERROR", "supervisor", f"Service exited: exit_code={code}; restart_scheduled=true")
        time.sleep(max(0, args.restart_delay_ms) / 1000.0)

    _log("ERROR", "supervisor", "Max restarts exceeded: status=stopped")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
