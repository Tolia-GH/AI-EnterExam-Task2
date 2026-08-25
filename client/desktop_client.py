from __future__ import annotations

import argparse
import json
import queue
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, StringVar, Tk, Toplevel, filedialog, ttk

from client.sample_tickets_store import (
    SampleTicketsError,
    drop_pending_by_id,
    enqueue_pending,
    ensure_store,
    make_ticket_from_template,
    next_template,
    pending_batch,
    record_submitted,
    save_store,
    update_ticket_status,
)
from client.forms import (
    FieldDef,
    FormDef,
    delete_form,
    get_kv,
    init_tables as init_form_tables,
    list_forms,
    set_kv,
    upsert_form,
    validate_form,
)
from client.rules import (
    TicketGenRule,
    delete_rule,
    init_tables as init_rule_tables,
    list_rules,
    match_rule,
    upsert_rule,
    validate_rule,
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(level: str, module: str, message: str) -> None:
    print(f"[{_now()}] [{level}] [{module}] {message}")


@dataclass(frozen=True)
class ClientSettings:
    api_base: str
    sse_url: str
    cache_path: Path
    sample_tickets_path: Path
    gen_rate_per_min: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--api-base", default="http://127.0.0.1:18000")
    p.add_argument("--cache", default="client/cache.db")
    p.add_argument("--sample-tickets", default="client/sample_tickets.json")
    p.add_argument("--gen-per-min", type=int, default=30)
    return p.parse_args()


def _resolve(path: str) -> Path:
    root = Path(__file__).resolve().parent.parent
    p = Path(path)
    if p.is_absolute():
        return p
    return (root / p).resolve()


def init_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    init_rule_tables(con)
    init_form_tables(con)
    con.commit()
    return con


def post_ticket(api_base: str, payload: dict, timeout_s: float = 2.0) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/tickets",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        resp.read()


def sse_listen(sse_url: str, out_q: queue.Queue[dict], stop: threading.Event) -> None:
    try:
        req = urllib.request.Request(sse_url, headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            for raw in resp:
                if stop.is_set():
                    return
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                out_q.put_nowait({"line": data})
    except Exception as e:
        out_q.put({"line": f"[{_now()}] [WARN] [client] sse_disconnected: {type(e).__name__}"})


def _parse_line(line: str) -> tuple[str, str, str, str]:
    try:
        if not line.startswith("["):
            return "", "INFO", "", line
        a = line.find("]")
        ts = line[1:a]
        b = line.find("[", a + 1)
        c = line.find("]", b + 1)
        level = line[b + 1 : c]
        d = line.find("[", c + 1)
        e = line.find("]", d + 1)
        module = line[d + 1 : e]
        msg = line[e + 2 :]
        return ts, level, module, msg
    except Exception:
        return "", "INFO", "", line


def _contains_cjk(text: str) -> bool:
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF:
            return True
    return False


def _sanitize_message(message: str) -> str:
    if not _contains_cjk(message):
        return message

    parts: list[str] = []
    for key in ("status", "route", "topic", "risk", "conf"):
        token = key + "="
        if token in message:
            try:
                v = message.split(token, 1)[1].split(";", 1)[0].strip()
                if v:
                    parts.append(f"{key}={v}")
            except Exception:
                continue

    if parts:
        return "Event received: " + "; ".join(parts)
    return "Event received"


def _safe_format(template: str, ctx: dict) -> str:
    try:
        return template.format_map(ctx)
    except Exception:
        return template


def apply_rules(rules: list[TicketGenRule], payload: dict) -> dict:
    channel = str(payload.get("channel", "desktop_client"))
    text = str(payload.get("description", ""))

    enabled_rules = [r for r in rules if r.enabled]
    enabled_rules.sort(key=lambda r: r.priority, reverse=True)
    chosen = None
    for r in enabled_rules:
        if match_rule(r, text, channel):
            chosen = r
            break

    if chosen is None:
        return payload

    ctx = {
        "sample": text,
        "topic": str(payload.get("topic_hint", "")),
        "ts": _now(),
        "rand": uuid.uuid4().hex[:6],
    }
    out = dict(payload)
    out["submitter"] = chosen.submitter.strip() or str(payload.get("submitter", "simulator"))
    out["title"] = _safe_format(chosen.title_template, ctx) or str(payload.get("title", "Ticket"))
    desc = _safe_format(chosen.description_template, ctx) or text
    if chosen.route_hint.strip():
        desc = desc + f"\nroute_hint={chosen.route_hint.strip()}"
    out["description"] = desc
    return out


class App:
    def __init__(self, settings: ClientSettings):
        self._settings = settings
        self._cache = init_cache(settings.cache_path)
        self._cache_lock = threading.Lock()
        self._store_path = settings.sample_tickets_path
        self._store_lock = threading.Lock()
        self._store = {}
        self._stop = threading.Event()
        self._auto_enabled = threading.Event()
        self._events: queue.Queue[dict] = queue.Queue()

        self._load_store()

        self._root = Tk()
        self._root.title("Ticket Desktop Client")
        self._root.geometry("1120x640")

        self._label_submitted = StringVar(value="0")
        self._label_cache = StringVar(value="0")
        self._label_resolved = StringVar(value="0")
        self._label_pending = StringVar(value="0")
        self._label_auto_status = StringVar(value="stopped")

        self._build_ui()
        self._ensure_default_assets()
        self._start_threads()
        self._root.after(200, self._tick_ui)

    def _load_store(self) -> None:
        try:
            with self._store_lock:
                self._store = ensure_store(self._store_path)
        except SampleTicketsError as e:
            with self._store_lock:
                self._store = {"version": 1, "templates": [], "tickets": [], "pending": [], "stats": {"submitted": 0}}
            self._events.put({"line": f"[{_now()}] [ERROR] [client] sample_tickets_load_failed: error={str(e)}"})

    def _save_store(self) -> None:
        with self._store_lock:
            self._save_store_locked()

    def _save_store_locked(self) -> None:
        try:
            save_store(self._store_path, self._store)
        except Exception as e:
            self._events.put({"line": f"[{_now()}] [ERROR] [client] sample_tickets_save_failed: error={type(e).__name__}"})

    def _build_ui(self) -> None:
        self._tabs = ttk.Notebook(self._root)
        self._tabs.pack(fill=BOTH, expand=True)

        self._tab_control = ttk.Frame(self._tabs)
        self._tab_rules = ttk.Frame(self._tabs)
        self._tab_form = ttk.Frame(self._tabs)
        self._tab_logs = ttk.Frame(self._tabs)
        self._tabs.add(self._tab_control, text="Control")
        self._tabs.add(self._tab_rules, text="Rules")
        self._tabs.add(self._tab_form, text="Forms")
        self._tabs.add(self._tab_logs, text="Logs")

        self._build_control_tab()
        self._build_rules_tab()
        self._build_form_tab()
        self._build_logs_tab()

    def _build_control_tab(self) -> None:
        top = ttk.Frame(self._tab_control)
        top.pack(fill=BOTH, padx=10, pady=10)

        ttk.Label(top, text="Submitted").pack(side=LEFT)
        ttk.Label(top, textvariable=self._label_submitted, width=6).pack(side=LEFT, padx=8)
        ttk.Label(top, text="Pending upload").pack(side=LEFT)
        ttk.Label(top, textvariable=self._label_cache, width=6).pack(side=LEFT, padx=8)
        ttk.Label(top, text="Auto resolved").pack(side=LEFT)
        ttk.Label(top, textvariable=self._label_resolved, width=6).pack(side=LEFT, padx=8)
        ttk.Label(top, text="Escalated").pack(side=LEFT)
        ttk.Label(top, textvariable=self._label_pending, width=6).pack(side=LEFT, padx=8)
        ttk.Label(top, text="Auto generation").pack(side=LEFT, padx=10)
        ttk.Label(top, textvariable=self._label_auto_status, width=10).pack(side=LEFT)

        bottom = ttk.Frame(self._tab_control)
        bottom.pack(fill=BOTH, padx=10, pady=6)
        ttk.Button(bottom, text="Start", command=self._start_auto).pack(side=LEFT)
        ttk.Button(bottom, text="Stop", command=self._stop_auto).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Reset", command=self._reset_status).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Manual ticket", command=self._open_manual_form).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Generate 1", command=self._generate_once).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Exit", command=self.close).pack(side=RIGHT)

    def _build_logs_tab(self) -> None:
        self._log_box = ttk.Treeview(self._tab_logs, columns=("ts", "level", "module", "message"), show="headings")
        self._log_box.heading("ts", text="Timestamp")
        self._log_box.heading("level", text="Level")
        self._log_box.heading("module", text="Module")
        self._log_box.heading("message", text="Message")
        self._log_box.column("ts", width=180)
        self._log_box.column("level", width=60)
        self._log_box.column("module", width=120)
        self._log_box.column("message", width=780)
        self._log_box.pack(fill=BOTH, expand=True, padx=10, pady=10)

    def _build_rules_tab(self) -> None:
        top = ttk.Frame(self._tab_rules)
        top.pack(fill=BOTH, padx=10, pady=10)

        self._rules_view = ttk.Treeview(
            top,
            columns=("id", "enabled", "priority", "channel", "keywords", "submitter", "route_hint"),
            show="headings",
        )
        for k, t, w in [
            ("id", "ID", 50),
            ("enabled", "Enabled", 80),
            ("priority", "Priority", 80),
            ("channel", "Channel", 120),
            ("keywords", "Keywords", 280),
            ("submitter", "Submitter", 120),
            ("route_hint", "Route hint", 180),
        ]:
            self._rules_view.heading(k, text=t)
            self._rules_view.column(k, width=w)
        self._rules_view.pack(fill=BOTH, expand=True)

        bottom = ttk.Frame(self._tab_rules)
        bottom.pack(fill=BOTH, padx=10, pady=6)
        ttk.Button(bottom, text="Add rule", command=self._add_rule).pack(side=LEFT)
        ttk.Button(bottom, text="Edit rule", command=self._edit_rule).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Delete rule", command=self._delete_rule).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Refresh", command=self._refresh_rules).pack(side=LEFT, padx=8)

        self._refresh_rules()

    def _build_form_tab(self) -> None:
        top = ttk.Frame(self._tab_form)
        top.pack(fill=BOTH, padx=10, pady=10)

        self._forms_view = ttk.Treeview(top, columns=("id", "name", "fields"), show="headings")
        self._forms_view.heading("id", text="ID")
        self._forms_view.heading("name", text="Name")
        self._forms_view.heading("fields", text="Fields")
        self._forms_view.column("id", width=60)
        self._forms_view.column("name", width=360)
        self._forms_view.column("fields", width=80)
        self._forms_view.pack(fill=BOTH, expand=True)

        bottom = ttk.Frame(self._tab_form)
        bottom.pack(fill=BOTH, padx=10, pady=6)
        ttk.Button(bottom, text="Add form", command=self._add_form).pack(side=LEFT)
        ttk.Button(bottom, text="Edit form", command=self._edit_form).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Delete form", command=self._delete_form).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Set default", command=self._set_default_form).pack(side=LEFT, padx=8)
        ttk.Button(bottom, text="Refresh", command=self._refresh_forms).pack(side=LEFT, padx=8)

        self._refresh_forms()

    def _start_threads(self) -> None:
        threading.Thread(target=self._generator_loop, daemon=True).start()
        threading.Thread(target=self._flush_loop, daemon=True).start()
        threading.Thread(target=sse_listen, args=(self._settings.sse_url, self._events, self._stop), daemon=True).start()

    def _generate_once(self) -> None:
        payload = self._make_payload()
        self._submit_or_cache(payload)

    def _generator_loop(self) -> None:
        interval = 60.0 / max(1, self._settings.gen_rate_per_min)
        while not self._stop.is_set():
            if not self._auto_enabled.is_set():
                time.sleep(0.2)
                continue
            try:
                payload = self._make_payload()
                self._submit_or_cache(payload)
            except Exception as e:
                self._events.put({"line": f"[{_now()}] [ERROR] [client] auto_generate_failed: error={type(e).__name__}"})
                time.sleep(0.2)
            time.sleep(interval)

    def _start_auto(self) -> None:
        self._auto_enabled.set()
        self._label_auto_status.set("running")
        self._events.put({"line": f"[{_now()}] [INFO] [client] auto_start"})

    def _stop_auto(self) -> None:
        self._auto_enabled.clear()
        self._label_auto_status.set("stopped")
        self._events.put({"line": f"[{_now()}] [INFO] [client] auto_stop"})

    def _reset_status(self) -> None:
        with self._store_lock:
            self._store["tickets"] = []
            self._store["pending"] = []
            self._store.setdefault("stats", {})["submitted"] = 0
            self._store.setdefault("stats", {})["template_cursor"] = 0
            self._save_store_locked()
        with self._cache_lock:
            self._cache.commit()
        self._events.put({"line": f"[{_now()}] [INFO] [client] status_reset"})

    def _make_payload(self) -> dict:
        with self._cache_lock:
            rules = list_rules(self._cache)
        with self._store_lock:
            tpl = next_template(self._store)
            payload = make_ticket_from_template(tpl)
            self._save_store_locked()
        return apply_rules(rules, payload)

    def _submit_or_cache(self, payload: dict) -> None:
        try:
            post_ticket(self._settings.api_base, payload)
            with self._store_lock:
                record_submitted(self._store, payload)
                self._save_store_locked()
            self._events.put({"line": f"[{_now()}] [INFO] [client] submit_ok: ticket_id={payload['ticket_id']}"})
        except (urllib.error.URLError, TimeoutError) as e:
            with self._store_lock:
                enqueue_pending(self._store, payload, error=type(e).__name__)
                self._save_store_locked()
            self._events.put({"line": f"[{_now()}] [WARN] [client] submit_cached: ticket_id={payload['ticket_id']}; error={type(e).__name__}"})
        except Exception as e:
            with self._store_lock:
                enqueue_pending(self._store, payload, error=type(e).__name__)
                self._save_store_locked()
            self._events.put({"line": f"[{_now()}] [ERROR] [client] submit_cached_unexpected: ticket_id={payload['ticket_id']}; error={type(e).__name__}"})

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            with self._store_lock:
                batch = pending_batch(self._store, limit=20)
            if not batch:
                time.sleep(0.5)
                continue
            for item in batch:
                try:
                    payload = dict(item.get("payload") or {})
                    post_ticket(self._settings.api_base, payload)
                    ticket_id = str(payload.get("ticket_id", ""))
                    with self._store_lock:
                        drop_pending_by_id(self._store, ticket_id)
                        record_submitted(self._store, payload)
                        self._save_store_locked()
                    self._events.put({"line": f"[{_now()}] [INFO] [client] flush_ok: ticket_id={payload['ticket_id']}"})
                except (urllib.error.URLError, TimeoutError) as e:
                    self._events.put(
                        {
                            "line": f"[{_now()}] [WARN] [client] flush_retry_later: ticket_id={item.get('ticket_id')}; error={type(e).__name__}"
                        }
                    )
                    time.sleep(0.2)
                    break
                except Exception as e:
                    self._events.put(
                        {"line": f"[{_now()}] [ERROR] [client] flush_failed: ticket_id={item.get('ticket_id')}; error={type(e).__name__}"}
                    )
                    time.sleep(0.2)
                    break

    def _tick_ui(self) -> None:
        try:
            while True:
                ev = self._events.get_nowait()
                self._append_log(ev)
        except queue.Empty:
            pass

        with self._store_lock:
            cached = len(self._store.get("pending") or [])
            submitted = int(self._store.get("stats", {}).get("submitted", 0))
            resolved = sum(1 for t in (self._store.get("tickets") or []) if t.get("status") == "RESOLVED")
            pending = sum(1 for t in (self._store.get("tickets") or []) if t.get("status") == "PENDING_REVIEW")
        self._label_submitted.set(str(submitted))
        self._label_cache.set(str(cached))
        self._label_resolved.set(str(resolved))
        self._label_pending.set(str(pending))
        self._root.after(200, self._tick_ui)

    def _append_log(self, ev: dict) -> None:
        line = str(ev.get("line", ""))
        ts, level, module, message = _parse_line(line)
        raw_message = message
        display_message = _sanitize_message(message)
        self._log_box.insert("", END, values=(ts, level, module, display_message))
        self._log_box.yview_moveto(1)

        if "ticket_id=" in raw_message and "status=" in raw_message:
            try:
                ticket_id = raw_message.split("ticket_id=", 1)[1].strip()
                status_part = raw_message.split("status=", 1)[1]
                status = status_part.split(";", 1)[0].strip()
                with self._store_lock:
                    update_ticket_status(self._store, ticket_id, status)
                    self._save_store_locked()
            except Exception:
                pass

    def _ensure_default_assets(self) -> None:
        with self._cache_lock:
            rules = list_rules(self._cache)
            forms = list_forms(self._cache)
            default_form_id = get_kv(self._cache, "default_form_id")

        if not rules:
            r = TicketGenRule(
                rule_id=None,
                name="Payment high-risk routing",
                enabled=True,
                priority=100,
                channel="desktop_client",
                keywords=["charged", "refund", "payment", "charge"],
                topic_hint="",
                submitter="simulator",
                title_template="Payment issue ticket",
                description_template="User report: {sample}\nPlease route to the payment specialist.",
                route_hint="ROUTE_TO_HUMAN_PAYMENT",
            )
            with self._cache_lock:
                upsert_rule(self._cache, r)

        if not forms:
            f = FormDef(
                form_id=None,
                name="Default ticket form",
                fields=[
                    FieldDef(field_id="submitter", label="Submitter", field_type="text", required=False),
                    FieldDef(field_id="title", label="Title", field_type="text", required=True),
                    FieldDef(field_id="description", label="Description", field_type="text", required=True),
                    FieldDef(
                        field_id="channel",
                        label="Channel",
                        field_type="select",
                        required=True,
                        options=["desktop_client", "web", "app"],
                    ),
                    FieldDef(field_id="attachment", label="Attachment", field_type="file", required=False),
                ],
            )
            with self._cache_lock:
                fid = upsert_form(self._cache, f)
                set_kv(self._cache, "default_form_id", str(fid))
        elif default_form_id is None:
            with self._cache_lock:
                set_kv(self._cache, "default_form_id", str(forms[0].form_id))

        self._refresh_rules()
        self._refresh_forms()

    def _refresh_rules(self) -> None:
        for i in self._rules_view.get_children():
            self._rules_view.delete(i)
        with self._cache_lock:
            rules = list_rules(self._cache)
        for r in rules:
            self._rules_view.insert(
                "",
                END,
                iid=str(r.rule_id),
                values=(
                    r.rule_id,
                    "Y" if r.enabled else "N",
                    r.priority,
                    r.channel,
                    ",".join(r.keywords),
                    r.submitter,
                    r.route_hint,
                ),
            )

    def _selected_rule_id(self) -> int | None:
        sel = self._rules_view.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _add_rule(self) -> None:
        self._open_rule_editor(None)

    def _edit_rule(self) -> None:
        rid = self._selected_rule_id()
        if rid is None:
            self._events.put({"line": f"[{_now()}] [WARN] [client] rule_edit: no_selection"})
            return
        self._open_rule_editor(rid)

    def _delete_rule(self) -> None:
        rid = self._selected_rule_id()
        if rid is None:
            return
        with self._cache_lock:
            delete_rule(self._cache, rid)
        self._refresh_rules()
        self._events.put({"line": f"[{_now()}] [INFO] [client] rule_deleted: rule_id={rid}"})

    def _open_rule_editor(self, rule_id: int | None) -> None:
        with self._cache_lock:
            rules = list_rules(self._cache)
        current = None
        for r in rules:
            if r.rule_id == rule_id:
                current = r
                break
        if current is None:
            current = TicketGenRule(
                rule_id=None,
                name="New rule",
                enabled=True,
                priority=10,
                channel="desktop_client",
                keywords=[""],
                topic_hint="",
                submitter="simulator",
                title_template="Ticket",
                description_template="{sample}",
                route_hint="",
            )

        win = Toplevel(self._root)
        win.title("Rule editor")
        win.geometry("720x360")

        v_name = StringVar(value=current.name)
        v_enabled = StringVar(value="Y" if current.enabled else "N")
        v_priority = StringVar(value=str(current.priority))
        v_channel = StringVar(value=current.channel)
        v_keywords = StringVar(value=",".join(current.keywords))
        v_topic_hint = StringVar(value=current.topic_hint)
        v_submitter = StringVar(value=current.submitter)
        v_title_tpl = StringVar(value=current.title_template)
        v_desc_tpl = StringVar(value=current.description_template)
        v_route_hint = StringVar(value=current.route_hint)

        frm = ttk.Frame(win)
        frm.pack(fill=BOTH, expand=True, padx=10, pady=10)

        def row(label: str, var: StringVar, r: int) -> None:
            ttk.Label(frm, text=label, width=18).grid(row=r, column=0, sticky="w")
            ttk.Entry(frm, textvariable=var, width=70).grid(row=r, column=1, sticky="w")

        row("Name", v_name, 0)
        row("Enabled (Y/N)", v_enabled, 1)
        row("Priority", v_priority, 2)
        row("Channel", v_channel, 3)
        row("Keywords (comma)", v_keywords, 4)
        row("Topic hint (optional)", v_topic_hint, 5)
        row("Submitter", v_submitter, 6)
        row("Title template", v_title_tpl, 7)
        row("Description template", v_desc_tpl, 8)
        row("Route hint (optional)", v_route_hint, 9)

        def on_save() -> None:
            enabled = v_enabled.get().strip().upper() == "Y"
            try:
                pr = int(v_priority.get().strip())
            except Exception:
                self._events.put({"line": f"[{_now()}] [ERROR] [client] rule_save_failed: invalid_priority"})
                return

            rule = TicketGenRule(
                rule_id=rule_id,
                name=v_name.get(),
                enabled=enabled,
                priority=pr,
                channel=v_channel.get(),
                keywords=[k.strip() for k in v_keywords.get().split(",") if k.strip()],
                topic_hint=v_topic_hint.get(),
                submitter=v_submitter.get(),
                title_template=v_title_tpl.get(),
                description_template=v_desc_tpl.get(),
                route_hint=v_route_hint.get(),
            )
            errs = validate_rule(rule)
            if errs:
                self._events.put({"line": f"[{_now()}] [ERROR] [client] rule_validation_failed: {errs[0]}"})
                return
            with self._cache_lock:
                rid = upsert_rule(self._cache, rule)
            self._refresh_rules()
            self._events.put({"line": f"[{_now()}] [INFO] [client] rule_saved: rule_id={rid}"})
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(fill=BOTH, padx=10, pady=6)
        ttk.Button(btns, text="Save", command=on_save).pack(side=LEFT)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=LEFT, padx=8)

    def _refresh_forms(self) -> None:
        for i in self._forms_view.get_children():
            self._forms_view.delete(i)
        with self._cache_lock:
            forms = list_forms(self._cache)
            default_form_id = get_kv(self._cache, "default_form_id")
        for f in forms:
            name = f.name
            if default_form_id and f.form_id and str(f.form_id) == str(default_form_id):
                name = name + " (default)"
            self._forms_view.insert("", END, iid=str(f.form_id), values=(f.form_id, name, len(f.fields)))

    def _selected_form_id(self) -> int | None:
        sel = self._forms_view.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _add_form(self) -> None:
        self._open_form_editor(None)

    def _edit_form(self) -> None:
        fid = self._selected_form_id()
        if fid is None:
            return
        self._open_form_editor(fid)

    def _delete_form(self) -> None:
        fid = self._selected_form_id()
        if fid is None:
            return
        with self._cache_lock:
            delete_form(self._cache, fid)
        self._refresh_forms()
        self._events.put({"line": f"[{_now()}] [INFO] [client] form_deleted: form_id={fid}"})

    def _set_default_form(self) -> None:
        fid = self._selected_form_id()
        if fid is None:
            return
        with self._cache_lock:
            set_kv(self._cache, "default_form_id", str(fid))
        self._refresh_forms()
        self._events.put({"line": f"[{_now()}] [INFO] [client] form_set_default: form_id={fid}"})

    def _open_form_editor(self, form_id: int | None) -> None:
        with self._cache_lock:
            forms = list_forms(self._cache)
        current = None
        for f in forms:
            if f.form_id == form_id:
                current = f
                break
        if current is None:
            current = FormDef(
                form_id=None,
                name="New form",
                fields=[FieldDef(field_id="title", label="Title", field_type="text", required=True)],
            )

        win = Toplevel(self._root)
        win.title("Form editor")
        win.geometry("820x420")

        v_name = StringVar(value=current.name)

        top = ttk.Frame(win)
        top.pack(fill=BOTH, padx=10, pady=8)
        ttk.Label(top, text="Form name", width=12).pack(side=LEFT)
        ttk.Entry(top, textvariable=v_name, width=40).pack(side=LEFT)

        view = ttk.Treeview(
            win,
            columns=("field_id", "label", "type", "required", "options", "pattern"),
            show="headings",
        )
        for k, t, w in [
            ("field_id", "Field ID", 120),
            ("label", "Label", 160),
            ("type", "Type", 120),
            ("required", "Required", 80),
            ("options", "Options", 220),
            ("pattern", "Pattern", 220),
        ]:
            view.heading(k, text=t)
            view.column(k, width=w)
        view.pack(fill=BOTH, expand=True, padx=10, pady=8)

        def refresh_fields(fields: list[FieldDef]) -> None:
            for i in view.get_children():
                view.delete(i)
            for f in fields:
                view.insert(
                    "",
                    END,
                    iid=f.field_id,
                    values=(
                        f.field_id,
                        f.label,
                        f.field_type,
                        "Y" if f.required else "N",
                        ",".join(f.options or []),
                        f.pattern or "",
                    ),
                )

        fields = list(current.fields)
        refresh_fields(fields)

        def selected_field_id() -> str | None:
            sel = view.selection()
            if not sel:
                return None
            return str(sel[0])

        def open_field_editor(existing: FieldDef | None) -> None:
            fwin = Toplevel(win)
            fwin.title("Field editor")
            fwin.geometry("520x260")

            v_id = StringVar(value=existing.field_id if existing else f"f_{uuid.uuid4().hex[:6]}")
            v_label = StringVar(value=existing.label if existing else "Field")
            v_type = StringVar(value=existing.field_type if existing else "text")
            v_required = StringVar(value="Y" if (existing.required if existing else False) else "N")
            v_options = StringVar(value=",".join(existing.options or []) if existing else "")
            v_pattern = StringVar(value=existing.pattern or "" if existing else "")

            frm = ttk.Frame(fwin)
            frm.pack(fill=BOTH, expand=True, padx=10, pady=10)

            def r(label: str, var: StringVar, row_idx: int) -> None:
                ttk.Label(frm, text=label, width=12).grid(row=row_idx, column=0, sticky="w")
                entry = ttk.Entry(frm, textvariable=var, width=46)
                if existing is not None and label == "Field ID":
                    entry.configure(state="disabled")
                entry.grid(row=row_idx, column=1, sticky="w")

            r("Field ID", v_id, 0)
            r("Label", v_label, 1)
            r("Type (text/select/date/file)", v_type, 2)
            r("Required (Y/N)", v_required, 3)
            r("Options (comma)", v_options, 4)
            r("Pattern (regex)", v_pattern, 5)

            def on_ok() -> None:
                nonlocal fields
                fid = existing.field_id if existing is not None else v_id.get().strip()
                ftype = v_type.get().strip()
                required = v_required.get().strip().upper() == "Y"
                opts = [o.strip() for o in v_options.get().split(",") if o.strip()]
                fd = FieldDef(
                    field_id=fid,
                    label=v_label.get().strip(),
                    field_type=ftype,
                    required=required,
                    options=opts or None,
                    pattern=v_pattern.get().strip() or None,
                )
                if existing is None:
                    new_fields = fields + [fd]
                else:
                    new_fields = [fd if x.field_id == existing.field_id else x for x in fields]
                tmp = FormDef(form_id=form_id, name=v_name.get(), fields=new_fields)
                errs = validate_form(tmp)
                if errs:
                    self._events.put({"line": f"[{_now()}] [ERROR] [client] form_field_invalid: {errs[0]}"})
                    return
                fields = new_fields
                refresh_fields(fields)
                fwin.destroy()

            btns = ttk.Frame(fwin)
            btns.pack(fill=BOTH, padx=10, pady=6)
            ttk.Button(btns, text="OK", command=on_ok).pack(side=LEFT)
            ttk.Button(btns, text="Cancel", command=fwin.destroy).pack(side=LEFT, padx=8)

        def add_field() -> None:
            open_field_editor(None)

        def edit_field() -> None:
            fid = selected_field_id()
            if fid is None:
                return
            ex = next((x for x in fields if x.field_id == fid), None)
            if ex is None:
                return
            open_field_editor(ex)

        def delete_field() -> None:
            nonlocal fields
            fid = selected_field_id()
            if fid is None:
                return
            fields = [x for x in fields if x.field_id != fid]
            refresh_fields(fields)

        def save_form() -> None:
            form = FormDef(form_id=form_id, name=v_name.get().strip(), fields=fields)
            errs = validate_form(form)
            if errs:
                self._events.put({"line": f"[{_now()}] [ERROR] [client] form_validation_failed: {errs[0]}"})
                return
            with self._cache_lock:
                fid = upsert_form(self._cache, form)
                if get_kv(self._cache, "default_form_id") is None:
                    set_kv(self._cache, "default_form_id", str(fid))
            self._refresh_forms()
            self._events.put({"line": f"[{_now()}] [INFO] [client] form_saved: form_id={fid}"})
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(fill=BOTH, padx=10, pady=6)
        ttk.Button(btns, text="Add field", command=add_field).pack(side=LEFT)
        ttk.Button(btns, text="Edit field", command=edit_field).pack(side=LEFT, padx=8)
        ttk.Button(btns, text="Delete field", command=delete_field).pack(side=LEFT, padx=8)
        ttk.Button(btns, text="Save", command=save_form).pack(side=RIGHT)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=RIGHT, padx=8)

    def _open_manual_form(self) -> None:
        with self._cache_lock:
            forms = list_forms(self._cache)
            default_form_id = get_kv(self._cache, "default_form_id")
        form = None
        for f in forms:
            if default_form_id and f.form_id and str(f.form_id) == str(default_form_id):
                form = f
                break
        if form is None and forms:
            form = forms[0]
        if form is None:
            self._events.put({"line": f"[{_now()}] [ERROR] [client] manual_form_missing"})
            return

        win = Toplevel(self._root)
        win.title("Create ticket")
        win.geometry("720x520")
        frm = ttk.Frame(win)
        frm.pack(fill=BOTH, expand=True, padx=10, pady=10)

        values: dict[str, StringVar] = {}

        def add_row(i: int, field: FieldDef) -> None:
            ttk.Label(frm, text=field.label, width=18).grid(row=i, column=0, sticky="w", pady=4)
            v = StringVar(value="")
            values[field.field_id] = v
            if field.field_type == "select":
                cb = ttk.Combobox(frm, textvariable=v, values=field.options or [], width=46)
                cb.grid(row=i, column=1, sticky="w")
                if field.options:
                    v.set(field.options[0])
                return
            if field.field_type == "file":
                e = ttk.Entry(frm, textvariable=v, width=46)
                e.grid(row=i, column=1, sticky="w")

                def pick() -> None:
                    p = filedialog.askopenfilename()
                    if p:
                        v.set(p)

                ttk.Button(frm, text="Browse", command=pick).grid(row=i, column=2, sticky="w", padx=6)
                return
            ttk.Entry(frm, textvariable=v, width=46).grid(row=i, column=1, sticky="w")

        for idx, f in enumerate(form.fields):
            add_row(idx, f)

        def validate_and_build() -> dict | None:
            payload = {
                "ticket_id": "MAN-" + uuid.uuid4().hex[:10],
                "channel": "desktop_client",
                "submitter": "user",
                "title": "Manual ticket",
                "description": "",
            }
            desc_parts = []
            for f in form.fields:
                v = values.get(f.field_id)
                val = v.get().strip() if v else ""
                if f.required and not val:
                    self._events.put({"line": f"[{_now()}] [ERROR] [client] form_submit_failed: missing={f.field_id}"})
                    return None
                if f.field_type == "date" and val:
                    if len(val) != 10 or val[4] != "-" or val[7] != "-":
                        self._events.put({"line": f"[{_now()}] [ERROR] [client] form_submit_failed: invalid_date={f.field_id}"})
                        return None
                if f.pattern and val:
                    import re

                    if re.fullmatch(f.pattern, val) is None:
                        self._events.put({"line": f"[{_now()}] [ERROR] [client] form_submit_failed: pattern={f.field_id}"})
                        return None

                if f.field_id == "title" and val:
                    payload["title"] = val
                elif f.field_id == "description" and val:
                    payload["description"] = val
                elif f.field_id == "submitter" and val:
                    payload["submitter"] = val
                elif f.field_id == "channel" and val:
                    payload["channel"] = val
                else:
                    if val:
                        desc_parts.append(f"{f.label}={val}")

            if desc_parts:
                payload["description"] = (payload["description"] + "\n" + "\n".join(desc_parts)).strip()
            return payload

        def on_submit() -> None:
            payload = validate_and_build()
            if payload is None:
                return
            self._submit_or_cache(payload)
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(fill=BOTH, padx=10, pady=8)
        ttk.Button(btns, text="Submit", command=on_submit).pack(side=LEFT)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=LEFT, padx=8)

    def run(self) -> None:
        self._root.mainloop()

    def close(self) -> None:
        self._stop.set()
        try:
            self._cache.close()
        except Exception:
            pass
        self._root.destroy()


def main() -> None:
    args = parse_args()
    settings = ClientSettings(
        api_base=args.api_base,
        sse_url=args.api_base.rstrip("/") + "/events",
        cache_path=_resolve(args.cache),
        sample_tickets_path=_resolve(args.sample_tickets),
        gen_rate_per_min=max(1, args.gen_per_min),
    )
    _log("INFO", "client", f"Client started: api_base={settings.api_base}; gen_per_min={settings.gen_rate_per_min}")
    App(settings).run()


if __name__ == "__main__":
    main()
