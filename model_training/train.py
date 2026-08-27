from __future__ import annotations

"""
Local topic model training (Multinomial Naive Bayes).

This script trains a lightweight topic classifier on a small CSV dataset and
exports a JSON model artifact consumed by the backend and PoC pipeline.

Inputs:
- data/labeled_tickets.csv

Outputs:
- model_service/model.json
- model_training/training_report.json

The implementation uses only the Python standard library to keep the project
self-contained for exam evaluation.
"""

import argparse
import csv
import json
import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _level_value(level: str) -> int:
    return _LEVELS.get(level.upper().strip(), _LEVELS["INFO"])


def log(enabled_level: int, level: str, module: str, message: str) -> None:
    if _level_value(level) < enabled_level:
        return
    print(f"[{_now()}] [{level.upper()}] [{module}] {message}")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    buff: list[str] = []
    for ch in text:
        o = ord(ch)
        if "0" <= ch <= "9" or "a" <= ch.lower() <= "z":
            buff.append(ch.lower())
            continue
        if buff:
            tokens.append("".join(buff))
            buff = []
        if 0x4E00 <= o <= 0x9FFF:
            tokens.append(ch)
    if buff:
        tokens.append("".join(buff))
    return tokens


@dataclass(frozen=True)
class Example:
    topic: str
    text: str


def load_dataset(path: str | Path) -> list[Example]:
    p = Path(path)
    rows: list[Example] = []
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            topic = (r.get("topic") or "").strip()
            text = (r.get("text") or "").strip()
            if not topic or not text:
                continue
            rows.append(Example(topic=topic, text=text))
    return rows


def split_dataset(examples: list[Example], seed: int) -> tuple[list[Example], list[Example], list[Example]]:
    rnd = random.Random(seed)
    items = list(examples)
    rnd.shuffle(items)
    n = len(items)
    n_train = int(n * 0.8)
    n_val = int(n * 0.1)
    train = items[:n_train]
    val = items[n_train : n_train + n_val]
    test = items[n_train + n_val :]
    return train, val, test


def train_nb(train: list[Example], alpha: float) -> dict:
    doc_count = Counter([e.topic for e in train])
    labels = sorted(doc_count.keys())

    token_count: dict[str, Counter[str]] = {l: Counter() for l in labels}
    total_tokens: dict[str, int] = {l: 0 for l in labels}
    vocab: set[str] = set()

    for e in train:
        toks = tokenize(e.text)
        token_count[e.topic].update(toks)
        total_tokens[e.topic] += len(toks)
        vocab.update(toks)

    priors = {l: doc_count[l] / float(len(train)) for l in labels}
    model = {
        "model_type": "multinomial_nb",
        "model_version": datetime.now().strftime("nb-%Y%m%d-%H%M%S"),
        "alpha": alpha,
        "labels": labels,
        "priors": priors,
        "token_count": {l: dict(token_count[l]) for l in labels},
        "total_tokens": total_tokens,
        "vocab_size": len(vocab),
        "tokenizer": "char+alnum",
    }
    return model


def predict_proba(model: dict, text: str) -> dict[str, float]:
    labels: list[str] = model["labels"]
    alpha: float = float(model["alpha"])
    priors: dict[str, float] = model["priors"]
    token_count: dict[str, dict[str, int]] = model["token_count"]
    total_tokens: dict[str, int] = model["total_tokens"]
    vocab_size: int = int(model["vocab_size"]) or 1

    toks = tokenize(text)

    logps: dict[str, float] = {}
    for l in labels:
        lp = math.log(max(priors.get(l, 1e-12), 1e-12))
        denom = total_tokens.get(l, 0) + alpha * vocab_size
        for t in toks:
            c = token_count.get(l, {}).get(t, 0)
            lp += math.log((c + alpha) / denom)
        logps[l] = lp

    m = max(logps.values()) if logps else 0.0
    exps = {l: math.exp(v - m) for l, v in logps.items()}
    z = sum(exps.values()) or 1.0
    return {l: exps[l] / z for l in labels}


def evaluate(model: dict, examples: list[Example]) -> dict:
    labels: list[str] = model["labels"]
    correct = 0
    total = 0

    tp = Counter()
    fp = Counter()
    fn = Counter()

    for e in examples:
        proba = predict_proba(model, e.text)
        pred = max(proba.items(), key=lambda x: x[1])[0]
        total += 1
        if pred == e.topic:
            correct += 1
            tp[pred] += 1
        else:
            fp[pred] += 1
            fn[e.topic] += 1

    acc = correct / float(total) if total else 0.0
    recall = {}
    for l in labels:
        denom = tp[l] + fn[l]
        recall[l] = (tp[l] / float(denom)) if denom else 0.0

    return {"accuracy": acc, "recall": recall, "total": total}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/labeled_tickets.csv")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out-model", default="model_service/model.json")
    p.add_argument("--out-report", default="model_training/training_report.json")
    p.add_argument("--log-level", default=None)
    return p.parse_args()


def _resolve_path(p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (_PROJECT_ROOT / path).resolve()



def main() -> None:
    args = parse_args()
    env_level = os.getenv("LOG_LEVEL", "INFO")
    enabled_level = _level_value(args.log_level or env_level)
    data_path = _resolve_path(args.data)
    out_model_path = _resolve_path(args.out_model)
    out_report_path = _resolve_path(args.out_report)

    log(enabled_level, "INFO", "train", f"Training started: data={data_path.as_posix()}; seed={args.seed}")
    examples = load_dataset(data_path)
    log(enabled_level, "INFO", "train", f"Dataset loaded: samples={len(examples)}")

    train_set, val_set, test_set = split_dataset(examples, seed=args.seed)
    log(
        enabled_level,
        "INFO",
        "train",
        f"Dataset split: train={len(train_set)}; val={len(val_set)}; test={len(test_set)}",
    )

    candidates = [0.5, 1.0, 2.0]
    best = None
    best_val = -1.0
    best_metrics = None

    for a in candidates:
        m = train_nb(train_set, alpha=a)
        metrics = evaluate(m, val_set)
        log(enabled_level, "INFO", "tune", f"alpha={a}; val_accuracy={metrics['accuracy']:.4f}")
        if metrics["accuracy"] > best_val:
            best_val = metrics["accuracy"]
            best = m
            best_metrics = metrics

    assert best is not None and best_metrics is not None
    test_metrics = evaluate(best, test_set)
    log(enabled_level, "INFO", "eval", f"best_alpha={best['alpha']}; test_accuracy={test_metrics['accuracy']:.4f}")

    out_model_path.parent.mkdir(parents=True, exist_ok=True)
    out_model_path.write_text(json.dumps(best, ensure_ascii=False), encoding="utf-8")
    log(
        enabled_level,
        "INFO",
        "export",
        f"Model exported: path={out_model_path.as_posix()}; version={best['model_version']}",
    )

    report = {
        "data": data_path.as_posix(),
        "seed": args.seed,
        "best_alpha": best["alpha"],
        "val_metrics": best_metrics,
        "test_metrics": test_metrics,
        "model_path": out_model_path.as_posix(),
        "model_version": best["model_version"],
    }
    out_report_path.parent.mkdir(parents=True, exist_ok=True)
    out_report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(enabled_level, "INFO", "export", f"Training report exported: path={out_report_path.as_posix()}")
    log(enabled_level, "INFO", "train", "Training finished: status=success")


if __name__ == "__main__":
    main()
