from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeConfig:
    project_root: Path
    backend_host: str
    backend_port: int
    api_base: str
    topic_confidence_threshold: float
    auto_close_topics: list[str]
    db_path: Path
    routing_rules_path: Path
    local_topic_model_path: Path
    client_sample_tickets_path: Path
    poc_tickets_path: Path
    kb_path: Path
    audit_path: Path
    client_gen_per_min: int


def _deep_get(d: dict[str, Any], key: str, default: Any) -> Any:
    v = d.get(key, default)
    return default if v is None else v


def _resolve(project_root: Path, p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _default_dict(project_root: Path) -> dict[str, Any]:
    backend_host = "127.0.0.1"
    backend_port = 18000
    return {
        "backend_host": backend_host,
        "backend_port": backend_port,
        "api_base": f"http://{backend_host}:{backend_port}",
        "topic_confidence_threshold": 0.7,
        "auto_close_topics": ["order_delivery"],
        "db_path": "data/tickets.db",
        "routing_rules_path": "config/routing_rules.json",
        "local_topic_model_path": "model_service/model.json",
        "client_sample_tickets_path": "client/sample_tickets.json",
        "poc_tickets_path": "data/sample_tickets.json",
        "kb_path": "data/kb.json",
        "audit_path": "logs/audit.jsonl",
        "client_gen_per_min": 30,
    }


def load_runtime_config() -> RuntimeConfig:
    project_root = Path(__file__).resolve().parent
    default_path = project_root / "config" / "runtime.json"
    cfg_path = Path(os.getenv("RUNTIME_CONFIG", str(default_path)))
    if not cfg_path.is_absolute():
        cfg_path = (project_root / cfg_path).resolve()

    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}

    d = _default_dict(project_root)
    d.update(raw)

    backend_host = str(_deep_get(d, "backend_host", "127.0.0.1"))
    backend_port = int(_deep_get(d, "backend_port", 18000))
    api_base = str(_deep_get(d, "api_base", f"http://{backend_host}:{backend_port}")).rstrip("/")
    if not api_base:
        api_base = f"http://{backend_host}:{backend_port}"

    topic_confidence_threshold = float(_deep_get(d, "topic_confidence_threshold", 0.7))
    auto_close_topics = list(_deep_get(d, "auto_close_topics", ["order_delivery"])) or ["order_delivery"]

    return RuntimeConfig(
        project_root=project_root,
        backend_host=backend_host,
        backend_port=backend_port,
        api_base=api_base,
        topic_confidence_threshold=topic_confidence_threshold,
        auto_close_topics=[str(x).strip() for x in auto_close_topics if str(x).strip()],
        db_path=_resolve(project_root, str(_deep_get(d, "db_path", "data/tickets.db"))),
        routing_rules_path=_resolve(project_root, str(_deep_get(d, "routing_rules_path", "config/routing_rules.json"))),
        local_topic_model_path=_resolve(project_root, str(_deep_get(d, "local_topic_model_path", "model_service/model.json"))),
        client_sample_tickets_path=_resolve(project_root, str(_deep_get(d, "client_sample_tickets_path", "client/sample_tickets.json"))),
        poc_tickets_path=_resolve(project_root, str(_deep_get(d, "poc_tickets_path", "data/sample_tickets.json"))),
        kb_path=_resolve(project_root, str(_deep_get(d, "kb_path", "data/kb.json"))),
        audit_path=_resolve(project_root, str(_deep_get(d, "audit_path", "logs/audit.jsonl"))),
        client_gen_per_min=max(1, int(_deep_get(d, "client_gen_per_min", 30))),
    )

