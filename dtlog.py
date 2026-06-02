#!/usr/bin/env python3
"""
dtlog.py — structured logging + event bus for dreamTerm (SSOT docs/SSOT.md §10).

- Every event is a JSON line written to server.jsonl (rotating) and, if it has a
  session, to sessions/<name>.jsonl. A compact human line goes to stderr.
- Every event also fans out to connected SSE clients via BUS, so the dashboard
  can watch the fleet live.
- A corr_id threads one agent loop (tool.invoke -> screenshot.* -> canvas.reload)
  end to end, so a single grep reconstructs the whole story.
"""
import json
import logging
import os
import queue
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Event bus (pub/sub for SSE) ─────────────────────────────────────────────────
class EventBus:
    def __init__(self):
        self._subs = set()
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=2000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self, event):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # slow client — drop rather than block the server

    def count(self):
        with self._lock:
            return len(self._subs)


BUS = EventBus()

# ── Module state ────────────────────────────────────────────────────────────────
_file_logger = None
_session_loggers = {}
_session_lock = threading.Lock()
_log_root = None


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _make_logger(name, path):
    lg = logging.getLogger(name)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        h = RotatingFileHandler(str(path), maxBytes=10 * 1024 * 1024, backupCount=5)
        h.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(h)
    return lg


def setup(log_root):
    """Initialize logging. Falls back to a temp dir if log_root isn't writable
    (e.g. local dev on macOS where /var/log/dreamterm needs root). Returns the
    directory actually used."""
    global _file_logger, _log_root
    root = Path(log_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / ".write_test").write_text("ok")
        (root / ".write_test").unlink()
    except Exception:
        root = Path(tempfile.gettempdir()) / "dreamterm-logs"
        root.mkdir(parents=True, exist_ok=True)
    (root / "sessions").mkdir(exist_ok=True)
    _log_root = root
    _file_logger = _make_logger("dreamterm", root / "server.jsonl")
    return root


def _session_logger(session):
    with _session_lock:
        lg = _session_loggers.get(session)
        if lg is None:
            safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in session)
            lg = _make_logger(
                "dreamterm.session." + safe,
                _log_root / "sessions" / (safe + ".jsonl"),
            )
            _session_loggers[session] = lg
        return lg


def new_corr():
    """Mint a correlation id for one agent loop iteration."""
    return "c_" + uuid.uuid4().hex[:12]


def redact_token(tok):
    if not tok:
        return None
    return "tok_…" + tok[-4:] if len(tok) > 4 else "tok_…"


def _compact(rec):
    bits = [rec["ts"][11:23], rec["level"][:4].upper(), rec["event"]]
    if rec.get("session"):
        bits.append(rec["session"])
    extra = {k: v for k, v in rec.items()
             if k not in ("ts", "level", "event", "actor", "session")}
    if extra:
        bits.append(json.dumps(extra, ensure_ascii=False))
    return "  ".join(str(b) for b in bits)


def emit(event, level="info", actor="system", session=None, corr_id=None,
         stream=True, **fields):
    """Write one structured event everywhere it needs to go."""
    rec = {"ts": _iso_now(), "level": level, "event": event, "actor": actor}
    if session:
        rec["session"] = session
    if corr_id:
        rec["corr_id"] = corr_id
    rec.update(fields)

    line = json.dumps(rec, ensure_ascii=False)
    if _file_logger:
        _file_logger.info(line)
        if session:
            _session_logger(session).info(line)
    try:
        sys.stderr.write(_compact(rec) + "\n")
    except Exception:
        pass
    if stream:
        BUS.publish(rec)
    return rec
