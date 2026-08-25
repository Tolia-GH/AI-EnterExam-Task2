from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

from app.model_client import TopicClassifierClient
from model_service.server import Handler, AppState, ThreadingHTTPServer, _level_value, log


def main() -> None:
    enabled_level = _level_value("INFO")
    model_path = _PROJECT_ROOT / "model_service/model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.state = AppState(model=model, enabled_level=enabled_level)  # type: ignore[attr-defined]
    host, port = httpd.server_address

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    log(enabled_level, "INFO", "self_test", f"服务启动: host={host}; port={port}")

    client = TopicClassifierClient(f"http://{host}:{port}", timeout_s=1.0)
    r1 = client.classify("订单一直显示骑手已取餐但40分钟没动了，怎么催单？", meta={"channel": "app"})
    log(enabled_level, "INFO", "self_test", f"用例1: topic={r1.topic}; confidence={r1.confidence:.4f}; version={r1.model_version}")

    r2 = client.classify("我被扣了两次钱订单还取消了，要求立刻退款！", meta={"channel": "email"})
    log(enabled_level, "INFO", "self_test", f"用例2: topic={r2.topic}; confidence={r2.confidence:.4f}; version={r2.model_version}")

    httpd.shutdown()
    t.join(timeout=2)
    httpd.server_close()
    time.sleep(0.1)
    log(enabled_level, "INFO", "self_test", "自检完成: status=success")


if __name__ == "__main__":
    main()
