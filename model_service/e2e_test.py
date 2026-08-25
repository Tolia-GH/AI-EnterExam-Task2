from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

from app.model_client import TopicClassifierClient
from app.poc import load_kb, normalize_ticket, process_ticket
from model_service.server import Handler, AppState, ThreadingHTTPServer, _level_value, log


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> None:
    enabled_level = _level_value("INFO")

    model = json.loads((_PROJECT_ROOT / "model_service/model.json").read_text(encoding="utf-8"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.state = AppState(model=model, enabled_level=enabled_level)  # type: ignore[attr-defined]
    host, port = httpd.server_address
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    log(enabled_level, "INFO", "e2e", f"模型服务启动: host={host}; port={port}")

    client = TopicClassifierClient(f"http://{host}:{port}", timeout_s=1.0)
    kb_docs = load_kb(_PROJECT_ROOT / "data/kb.json")
    raw_tickets = json.loads((_PROJECT_ROOT / "data/sample_tickets.json").read_text(encoding="utf-8"))

    expected = {
        "TCK-0001": ("order_delivery", "AUTO_SUGGEST"),
        "TCK-0002": ("payment", "ROUTE_TO_HUMAN_PAYMENT"),
    }

    ok = True
    for raw in raw_tickets:
        ticket = normalize_ticket(raw)
        decision, audit = process_ticket(ticket, kb_docs, model_client=client)
        exp_topic, exp_action = expected.get(ticket.ticket_id, ("other", "ROUTE_TO_HUMAN_GENERAL"))
        if decision.topic != exp_topic or decision.action != exp_action:
            ok = False
            log(
                enabled_level,
                "ERROR",
                "e2e",
                f"断言失败: ticket_id={ticket.ticket_id}; topic={decision.topic}!= {exp_topic}; action={decision.action}!= {exp_action}",
            )
        else:
            log(
                enabled_level,
                "INFO",
                "e2e",
                f"断言通过: ticket_id={ticket.ticket_id}; topic={decision.topic}; action={decision.action}; ts={_now()}",
            )

        if audit.errors:
            ok = False
            log(enabled_level, "ERROR", "e2e", f"模型降级不应发生: ticket_id={ticket.ticket_id}; errors={audit.errors}")

    httpd.shutdown()
    t.join(timeout=2)
    httpd.server_close()
    time.sleep(0.1)

    if ok:
        log(enabled_level, "INFO", "e2e", "端到端验证完成: status=success")
    else:
        log(enabled_level, "ERROR", "e2e", "端到端验证完成: status=failed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
