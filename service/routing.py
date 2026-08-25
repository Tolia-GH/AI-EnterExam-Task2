from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RoutingRules:
    topic_to_queue: dict[str, str]


def load_routing_rules(path: Path) -> RoutingRules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    topic_to_queue = dict(raw.get("topic_to_queue", {}))
    return RoutingRules(topic_to_queue=topic_to_queue)


def route(topic: str, rules: RoutingRules) -> str:
    return rules.topic_to_queue.get(topic, "ROUTE_TO_HUMAN_GENERAL")
