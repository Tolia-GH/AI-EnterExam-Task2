from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    db_path: Path
    routing_rules_path: Path
    model_local_path: Path
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None
    topic_confidence_threshold: float
    auto_close_topics: set[str]


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parent.parent

    db_path = Path(os.getenv("TICKET_DB_PATH", str(project_root / "data" / "tickets.db")))
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()

    routing_rules_path = Path(os.getenv("ROUTING_RULES_PATH", str(project_root / "config" / "routing_rules.json")))
    if not routing_rules_path.is_absolute():
        routing_rules_path = (project_root / routing_rules_path).resolve()

    model_local_path = Path(os.getenv("LOCAL_TOPIC_MODEL_PATH", str(project_root / "model_service" / "model.json")))
    if not model_local_path.is_absolute():
        model_local_path = (project_root / model_local_path).resolve()

    llm_base_url = os.getenv("LLM_BASE_URL")
    llm_api_key = os.getenv("LLM_API_KEY")
    llm_model = os.getenv("LLM_MODEL")

    topic_confidence_threshold = float(os.getenv("TOPIC_CONFIDENCE_THRESHOLD", "0.7"))
    auto_close_topics = set(
        t.strip()
        for t in os.getenv("AUTO_CLOSE_TOPICS", "order_delivery").split(",")
        if t.strip()
    )

    return Settings(
        project_root=project_root,
        db_path=db_path,
        routing_rules_path=routing_rules_path,
        model_local_path=model_local_path,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        topic_confidence_threshold=topic_confidence_threshold,
        auto_close_topics=auto_close_topics,
    )
