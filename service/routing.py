from __future__ import annotations

"""
Routing rules loader and router.

Routing is intentionally rule-based and deterministic in the PoC.
It maps a topic label to an action/queue identifier.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RoutingRules:
    """In-memory routing rules."""

    topic_to_queue: dict[str, str]


def load_routing_rules(path: Path) -> RoutingRules:
    """Load routing rules from a JSON file."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    topic_to_queue = dict(raw.get("topic_to_queue", {}))
    return RoutingRules(topic_to_queue=topic_to_queue)


def route(topic: str, rules: RoutingRules) -> str:
    """Return the routing action for a topic."""

    return rules.topic_to_queue.get(topic, "ROUTE_TO_HUMAN_GENERAL")
