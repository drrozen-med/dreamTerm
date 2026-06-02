#!/usr/bin/env python3
"""
fleet.py — loader for fleet.json, the dreamTerm Single Source of Truth.

This module is the ONLY place that reads fleet.json. server.py and the `canvas`
CLI both import it, so the fleet is defined exactly once (see docs/SSOT.md §5).

Usage:
    import fleet
    FLEET = fleet.load()
    FLEET.ttyd_port("redit")      -> 7682
    FLEET.canvas_port("redit")    -> 8089
    FLEET.known("redit")          -> True
"""
import json
import os
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get(
    "DREAMTERM_FLEET",
    str(Path(__file__).resolve().parent / "fleet.json"),
))


class Fleet:
    def __init__(self, data, path):
        self.raw = data
        self.path = Path(path)
        self.vm = data.get("vm", {}) or {}
        self.paths = data.get("paths", {}) or {}
        self.allowed_emails = {e.lower() for e in data.get("allowed_emails", [])}
        self.sessions = data.get("sessions", []) or []
        self._by_name = {s["name"]: s for s in self.sessions}

    # ── VM / runtime config ──────────────────────────────────────────────
    @property
    def dashboard_port(self):
        return int(self.vm.get("dashboard_port", 4000))

    @property
    def firebase_creds(self):
        return self.paths.get("firebase_creds")

    @property
    def canvas_root(self):
        return (os.environ.get("DREAMTERM_CANVAS_ROOT")
                or self.paths.get("canvas_root", "/home/claude-agent/canvas"))

    @property
    def log_root(self):
        return self.paths.get("log_root", "/var/log/dreamterm")

    # ── Per-session lookups ──────────────────────────────────────────────
    def session(self, name):
        return self._by_name.get(name)

    def known(self, name):
        return name in self._by_name

    def ttyd_port(self, name):
        s = self._by_name.get(name)
        return s.get("ttyd_port") if s else None

    def canvas_port(self, name):
        s = self._by_name.get(name)
        return s.get("canvas_port") if s else None

    def terminal_url(self, name):
        return "/terminal/{}/".format(name) if self.known(name) else None

    def preview_url(self, name):
        return "/preview/{}/".format(name) if self.known(name) else None

    def canvas_mode(self, name):
        s = self._by_name.get(name)
        return s.get("canvas_mode", "artifact") if s else None

    def names(self):
        return list(self._by_name.keys())

    # ── Serialization for /api/fleet ─────────────────────────────────────
    def public_view(self):
        """Safe-to-expose subset for the dashboard (no creds paths)."""
        return {
            "vm": {
                "host": self.vm.get("host"),
                "dashboard_port": self.dashboard_port,
            },
            "sessions": [
                {
                    "name": s["name"],
                    "ttyd_port": s.get("ttyd_port"),
                    "canvas_port": s.get("canvas_port"),
                    "canvas_mode": s.get("canvas_mode", "artifact"),
                    "label": s.get("label", s["name"]),
                    "terminal_url": self.terminal_url(s["name"]),
                    "preview_url": self.preview_url(s["name"]),
                }
                for s in self.sessions
            ],
        }


def load(path=DEFAULT_PATH):
    """Load and parse fleet.json. Raises on malformed JSON or missing file."""
    p = Path(path)
    data = json.loads(p.read_text())
    return Fleet(data, p)


if __name__ == "__main__":
    # Quick sanity dump: `python3 fleet.py`
    f = load()
    print("fleet.json:", f.path)
    print("dashboard_port:", f.dashboard_port)
    print("sessions:")
    for s in f.sessions:
        print("  {:28s} ttyd={} canvas={} mode={}".format(
            s["name"], s.get("ttyd_port"), s.get("canvas_port"),
            s.get("canvas_mode")))
