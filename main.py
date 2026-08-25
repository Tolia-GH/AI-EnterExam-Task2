from __future__ import annotations

"""
命令行入口：外卖平台客服工单自动化处理 PoC

该入口负责把“样例输入 → 处理流水线 → 审计落盘 → 可观测日志输出”串成一个可运行的端到端演示。

核心流程（每次运行）：
1) 解析参数与日志级别（支持 --log-level 或环境变量 LOG_LEVEL）
2) 加载本地 KB（data/kb.json）与样例工单（data/sample_tickets.json）
3) 初始化审计日志（默认 logs/audit.jsonl；运行前清空旧文件）
4) 对每条工单执行处理流水线：
   - 归一化 Ticket
   - 主题分类 + 风险判定 + 证据检索 + 决策路由 +（可选）草稿生成
   - 写入审计记录（JSONL，每条一行）
   - 输出标准化工作流程日志（可按日志级别控制）
5) 正常退出或异常退出（均有可追溯日志）
"""

import argparse
import os
import json
from datetime import datetime
from pathlib import Path

from app.poc import append_audit_record, load_kb, normalize_ticket, process_ticket


_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_level(level: str) -> str:
    return level.strip().upper()


def _get_level_value(level: str) -> int:
    return _LEVELS.get(_normalize_level(level), _LEVELS["INFO"])


def _log(enabled_level_value: int, level: str, module: str, message: str) -> None:
    level = _normalize_level(level)
    if _get_level_value(level) < enabled_level_value:
        return
    print(f"[{_now()}] [{level}] [{module}] {message}")


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    - --tickets: mock 工单输入文件（JSON 数组）
    - --kb: 本地知识库文件（JSON 数组）
    - --audit: 审计日志输出路径（JSONL）
    - --log-level: 日志级别（覆盖 LOG_LEVEL 环境变量）
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickets", default="data/sample_tickets.json")
    parser.add_argument("--kb", default="data/kb.json")
    parser.add_argument("--audit", default="logs/audit.jsonl")
    parser.add_argument("--log-level", default=None)
    return parser.parse_args()


def safe_unlink(path: Path) -> None:
    """
    清理运行产物：如果文件存在则删除，不存在则忽略。

    用于每次运行前清空审计日志文件，确保 demo 输出可复现。
    """
    try:
        path.unlink()
    except FileNotFoundError:
        return


def main() -> None:
    """
    程序主入口。

    注意：该函数不打印任何原始 JSON/对象，而是用标准化工作流程日志输出关键节点信息。
    """
    args = parse_args()
    env_level = os.getenv("LOG_LEVEL", "INFO")
    enabled_level_value = _get_level_value(args.log_level or env_level)

    _log(
        enabled_level_value,
        "INFO",
        "main",
        f"程序启动: tickets={args.tickets}; kb={args.kb}; audit={args.audit}; log_level={args.log_level or env_level}",
    )

    try:
        _log(enabled_level_value, "INFO", "loader", f"加载KB: path={args.kb}")
        kb_docs = load_kb(args.kb)
        _log(enabled_level_value, "INFO", "loader", f"KB加载完成: docs={len(kb_docs)}")

        _log(enabled_level_value, "INFO", "loader", f"加载工单样例: path={args.tickets}")
        raw_tickets = json.loads(Path(args.tickets).read_text(encoding="utf-8"))
        _log(enabled_level_value, "INFO", "loader", f"工单样例加载完成: tickets={len(raw_tickets)}")

        audit_path = Path(args.audit)
        _log(enabled_level_value, "DEBUG", "audit", f"初始化审计日志文件: path={audit_path.as_posix()}")
        safe_unlink(audit_path)

        for idx, raw in enumerate(raw_tickets, start=1):
            ticket = normalize_ticket(raw)
            _log(
                enabled_level_value,
                "INFO",
                "pipeline",
                f"开始处理工单: index={idx}; ticket_id={ticket.ticket_id}; channel={ticket.channel}; order_id={ticket.order_id or '-'}",
            )

            try:
                decision, audit = process_ticket(ticket, kb_docs)
            except Exception as e:
                _log(
                    enabled_level_value,
                    "ERROR",
                    "pipeline",
                    f"处理工单失败: ticket_id={ticket.ticket_id}; error={type(e).__name__}: {e}",
                )
                continue

            level = "WARN" if decision.risk_level == "risky" else "INFO"
            _log(
                enabled_level_value,
                level,
                "pipeline",
                "处理结果: "
                f"ticket_id={ticket.ticket_id}; topic={decision.topic}; risk={decision.risk_level}; "
                f"confidence={decision.confidence:.4f}; action={decision.action}; reason={decision.reason}",
            )

            if decision.evidence:
                top = decision.evidence[0]
                _log(
                    enabled_level_value,
                    "DEBUG",
                    "retrieval",
                    f"召回证据: ticket_id={ticket.ticket_id}; top1_doc={top.doc_id}; title={top.title}; score={top.score:.4f}",
                )
            else:
                _log(enabled_level_value, "DEBUG", "retrieval", f"召回证据为空: ticket_id={ticket.ticket_id}")

            if decision.draft_reply:
                _log(enabled_level_value, "INFO", "draft", f"生成草稿完成: ticket_id={ticket.ticket_id}")

            append_audit_record(audit_path, audit)
            _log(
                enabled_level_value,
                "INFO",
                "audit",
                f"审计记录已写入: ticket_id={ticket.ticket_id}; audit_id={audit.audit_id}; path={audit_path.as_posix()}",
            )

        _log(enabled_level_value, "INFO", "main", f"程序退出: status=success; audit_log={audit_path.as_posix()}")
    except Exception as e:
        _log(enabled_level_value, "ERROR", "main", f"程序异常退出: error={type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
