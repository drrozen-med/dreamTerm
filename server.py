#!/usr/bin/env python3
"""
Agent Dashboard API server — runs as claude-agent, with Firebase Auth.
Serves:
  GET  /                → index.html (no auth required)
  GET  /api/sessions    → tmux session list + agent detection (auth required)
  POST /api/auth/verify → verify Firebase ID token, check allowlist
"""
import json, subprocess, os, time, queue
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import fleet as fleet_mod
import dtlog

# ── Single Source of Truth (fleet.json) ─────────────────────────────────────────
FLEET = fleet_mod.load()

PORT = FLEET.dashboard_port
STATIC_DIR = Path(__file__).resolve().parent / "public"

# DREAMTERM_MOCK=1 runs the server with a fake fleet (no tmux / no Firebase),
# so the full server + GUI can run and be browser-tested on a dev machine.
MOCK = os.environ.get("DREAMTERM_MOCK") == "1"

# ── Structured logging + event bus (docs/SSOT.md §10) ───────────────────────────
_log_root = "/tmp/dreamterm-logs" if MOCK else FLEET.log_root
LOG_DIR = dtlog.setup(_log_root)

# ── Allowlist (from fleet.json) ─────────────────────────────────────────────────
ALLOWED_EMAILS = FLEET.allowed_emails
# Set True for quick testing (any valid Firebase token is accepted).
# Always on in MOCK mode so the dashboard is reachable without Firebase.
ALLOW_ALL = MOCK

# ── API key bypass (SSOT §11 — simple personal access, no Firebase required) ────
# Set DREAMTERM_API_KEY in the systemd environment. Never commit the key.
API_KEY = os.environ.get("DREAMTERM_API_KEY", "")

# ── Firebase Admin SDK ────────────────────────────────────────────────────────
FIREBASE_CREDS_PATH = FLEET.firebase_creds

_firebase_app = None

def get_firebase_app():
    global _firebase_app
    if _firebase_app:
        return _firebase_app
    try:
        import firebase_admin
        from firebase_admin import credentials
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_CREDS_PATH)
            _firebase_app = firebase_admin.initialize_app(cred)
        else:
            _firebase_app = firebase_admin.get_app()
        return _firebase_app
    except Exception as e:
        print("[Firebase] init error: {}".format(e))
        return None

def verify_token(id_token):
    """
    Verify a Firebase ID token and return (email, None) on success,
    or (None, error_message) on failure.
    """
    if not id_token:
        return None, "Missing token"
    app = get_firebase_app()
    if not app:
        # Firebase unavailable — fall back to allowlist-only
        if ALLOW_ALL:
            return ("offline-user", None)
        return None, "Firebase SDK not initialized"

    try:
        import firebase_admin.auth as fb_auth
        decoded = fb_auth.verify_id_token(id_token, app=app)
        email = decoded.get("email") or decoded.get("name", "")
        return (email, None)
    except fb_auth.InvalidIdTokenError as e:
        return None, "Invalid token: " + str(e)
    except Exception as e:
        return None, "Verification failed: " + str(e)

# ── tmux helpers (service runs as claude-agent) ────────────────────────────────
# Port maps are NO LONGER hand-maintained here — they are derived from FLEET
# (fleet.json), the single source of truth. See docs/SSOT.md §5.

def _tmux(fmt):
    r = subprocess.run(
        ["tmux", "ls", "-F", fmt],
        capture_output=True, text=True,
        env={**os.environ, "TMUX_TMPDIR": "/tmp"}
    )
    return r.stdout.strip()

def _tmux_pane(session, fmt):
    r = subprocess.run(
        ["tmux", "list-panes", "-t", session, "-F", fmt],
        capture_output=True, text=True
    )
    return r.stdout.strip()

def _mock_sessions():
    """Fake fleet derived from fleet.json — used by DREAMTERM_MOCK for local
    browser testing without tmux/ps. Deterministic, no Date/random."""
    fake_types = ["HERMES", "PI", "CODEX", "HUMAN-ET", "EMPTY"]
    out = []
    for i, s in enumerate(FLEET.sessions):
        name = s["name"]
        atype = fake_types[i % len(fake_types)]
        out.append({
            "name": name,
            "attached": (i % 3 == 0),
            "windows": 1,
            "terminal_port": FLEET.ttyd_port(name),
            "terminal_url": FLEET.terminal_url(name),
            "preview_url": FLEET.preview_url(name),
            "agent_type": atype,
            "cpu_percent": round((i * 7.3) % 100, 1) if atype != "EMPTY" else 0.0,
            "memory_mb": round((i * 123.4) % 2048, 1) if atype != "EMPTY" else 0.0,
        })
    return out


def get_sessions():
    _scan_t0 = time.time()
    if MOCK:
        out = _mock_sessions()
        dtlog.emit("fleet.scan", n_sessions=len(out),
                   n_agents=sum(1 for s in out if s.get("agent_type") not in (None, "EMPTY")),
                   dur_ms=round((time.time() - _scan_t0) * 1000, 1), mock=True)
        return out
    raw = _tmux("#{session_name}|#{session_attached}|#{session_windows}")
    sessions = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        name = parts[0]
        sessions.append({
            "name": name,
            "attached": parts[1] == "1",
            "windows": int(parts[2]),
            "terminal_port": FLEET.ttyd_port(name),
            "terminal_url": FLEET.terminal_url(name),
            "preview_url": FLEET.preview_url(name),
        })

    for s in sessions:
        name = s["name"]
        pane = _tmux_pane(name, "#{pane_pid}")
        if not pane:
            s["agent_type"] = "EMPTY"
            s["cpu_percent"] = 0.0
            s["memory_mb"] = 0.0
            continue

        r = subprocess.run(
            ["ps", "--ppid", pane, "-o", "comm", "--no-headers"],
            capture_output=True, text=True
        )
        kids = " ".join(
            l.strip() for l in r.stdout.strip().splitlines()
            if l.strip() and l.strip() != pane
        )

        if "hermes" in kids:                    s["agent_type"] = "HERMES"
        elif kids == "pi" or " pi " in kids:   s["agent_type"] = "PI"
        elif "codex" in kids:                   s["agent_type"] = "CODEX"
        elif "etterminal" in kids:              s["agent_type"] = "HUMAN-ET"
        elif kids:                             s["agent_type"] = "PROCESS:" + kids.split()[0]
        else:                                  s["agent_type"] = "EMPTY"

        r = subprocess.run(
            ["ps", "--ppid", pane, "-o", "pid", "--no-headers"],
            capture_output=True, text=True
        )
        pids = [pane] + [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
        if pids:
            r = subprocess.run(
                ["ps", "-p", ",".join(pids), "-o", "%cpu,rss", "--no-headers"],
                capture_output=True, text=True
            )
            total_cpu, total_mem = 0.0, 0
            for line in r.stdout.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        total_cpu += float(parts[0])
                        total_mem += int(parts[1])
                    except ValueError:
                        pass
            s["cpu_percent"] = round(total_cpu, 1)
            s["memory_mb"] = round(total_mem / 1024, 1)
        else:
            s["cpu_percent"] = 0.0
            s["memory_mb"] = 0.0

    agent_order = {"HERMES": 0, "PI": 1, "CODEX": 2, "HUMAN-ET": 3, "EMPTY": 4}
    def sort_key(s):
        extra = s.get("agent_type", "EMPTY")
        if extra.startswith("PROCESS:"): extra = "PROCESS"
        return (agent_order.get(extra, 5), -s.get("cpu_percent", 0), s["name"])
    sessions.sort(key=sort_key)
    dtlog.emit("fleet.scan", n_sessions=len(sessions),
               n_agents=sum(1 for s in sessions if s.get("agent_type") not in (None, "EMPTY")),
               dur_ms=round((time.time() - _scan_t0) * 1000, 1))
    return sessions

# ── HTTP Handler ───────────────────────────────────────────────────────────────
# ── Screenshot / browser capture (shared by dashboard + agent CLI) ──────────────
def _shots_dir(session):
    d = Path(FLEET.canvas_root) / session / "shots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prune_shots(session, keep=50):
    try:
        shots = sorted(_shots_dir(session).glob("c_*.png"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in shots[keep:]:
            p.unlink()
    except Exception:
        pass


def capture(url, img_path, width=1280, height=800, wait_ms=1500):
    """Screenshot a URL to img_path with the shared headless browser.
    Returns (ok, size_bytes, error)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                page.wait_for_timeout(wait_ms)
                page.screenshot(path=str(img_path), full_page=False)
            finally:
                browser.close()
        size = img_path.stat().st_size if Path(img_path).exists() else 0
        return True, size, None
    except Exception as e:
        return False, 0, str(e)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _query_token(self):
        """Extract ?token=... — EventSource cannot send Authorization headers."""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        vals = q.get("token")
        return vals[0] if vals else ""

    def require_auth(self):
        """Authenticate. Returns (email, None) or (None, error_str).
        Token may come from Authorization header, ?token= (SSE), or ?key= (API key bypass).
        MOCK mode bypasses Firebase entirely."""
        if MOCK:
            self._actor = "mock-user"
            return "mock-user", None
        # API key bypass — checked before Firebase so it works in any browser/webview
        if API_KEY:
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            supplied = (q.get("key") or [""])[0]
            if supplied and supplied == API_KEY:
                self._actor = "human:api-key"
                return "api-key-user", None
        auth = self.headers.get("Authorization", "")
        id_token = auth[7:] if auth.startswith("Bearer ") else self._query_token()
        if not id_token:
            return None, "Missing token"
        email, err = verify_token(id_token)
        if err:
            return None, err
        if not ALLOW_ALL:
            allowed = {e.lower() for e in ALLOWED_EMAILS}
            if email.lower() not in allowed and not email.startswith("offline-"):
                return None, "Access denied: {} not in allowlist".format(email)
        self._actor = ("agent" if self.is_loopback() else "human:" + email)
        return email, None

    def is_loopback(self):
        """True if the request originates from the local host (trusted plane)."""
        addr = self.client_address[0] if self.client_address else ""
        return addr in ("127.0.0.1", "::1", "localhost") or addr.startswith("127.")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "self")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)
        self._emit_http(status)

    def _emit_http(self, status):
        t0 = getattr(self, "_t0", None)
        dur = round((time.time() - t0) * 1000, 1) if t0 else None
        dtlog.emit("http.request", level="info",
                   actor=getattr(self, "_actor", "anon"),
                   method=self.command, path=self.path.split("?")[0],
                   status=status, dur_ms=dur)

    # ── SSE helpers ──────────────────────────────────────────────────────────
    def _sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # disable nginx buffering
        self.end_headers()

    def _sse_send(self, obj):
        self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
        self.wfile.flush()

    # ── Screenshot / browser: dual-plane (loopback agent OR authed human) ─────

    def _session_from_tmux(self):
        """Derive the tmux session name from the TMUX env var.
        TMUX=/tmp/tmux-1000/default,169687,0 → session name via tmux display.
        Returns None if we can't determine it."""
        tmux_var = os.environ.get("TMUX", "")
        if not tmux_var:
            return None
        # tmux display -p '#S' gives session name; -F works from any pane
        try:
            r = subprocess.run(
                ["tmux", "display", "-p", "#S"],
                capture_output=True, text=True, timeout=3,
                env={**os.environ, "TMUX": tmux_var}
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return None

    def _handle_screenshot(self, session):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        sync = q.get("sync", ["0"])[0] == "1"
        mode = q.get("mode", ["both"])[0]            # vision | chafa | both
        override_url = q.get("url", [None])[0]       # arbitrary URL ("canvas open")

        # Auth: agents call from loopback (no token); humans must authenticate.
        if self.is_loopback() or MOCK:
            self._actor = "agent:" + session
        else:
            email, err = self.require_auth()
            if err:
                self.send_json({"error": err}, 401)
                return

        corr = dtlog.new_corr()
        if override_url:
            target = override_url
        else:
            target = "http://127.0.0.1:{}".format(FLEET.canvas_port(session) or 8080)
        dtlog.emit("screenshot.request", session=session, actor=self._actor,
                   target=target, mode=mode, sync=sync, corr_id=corr)

        if MOCK:
            png = "/tmp/mock/{}.png".format(session)
            dtlog.emit("screenshot.ok", session=session, actor=self._actor,
                       png_path=png, bytes=12345, dur_ms=0.0, mode=mode, corr_id=corr)
            self.send_json({"ok": True, "session": session, "corr_id": corr,
                            "png": png, "url": target, "mode": mode})
            return

        img_path = _shots_dir(session) / (corr + ".png")

        def do_capture():
            t0 = time.time()
            ok, size, err = capture(target, img_path)
            dur = round((time.time() - t0) * 1000, 1)
            if ok:
                latest = Path(FLEET.canvas_root) / session / "latest.png"
                try:
                    latest.write_bytes(img_path.read_bytes())
                except Exception:
                    pass
                _prune_shots(session)
                dtlog.emit("screenshot.ok", session=session, actor=self._actor,
                           png_path=str(img_path), bytes=size, dur_ms=dur,
                           mode=mode, corr_id=corr)
                # Push model (human 📸): also render into the agent's tmux pane.
                if not sync and mode in ("chafa", "both"):
                    try:
                        subprocess.run(["tmux", "send-keys", "-t", session,
                                        "chafa {}".format(img_path), "Enter"], timeout=10)
                    except Exception:
                        pass
            else:
                dtlog.emit("screenshot.fail", level="error", session=session,
                           actor=self._actor, error=err, dur_ms=dur, corr_id=corr)
            return ok, size, err

        if sync:
            # Agent pull model: block, return the real PNG path to Read.
            ok, size, err = do_capture()
            if ok:
                self.send_json({"ok": True, "session": session, "corr_id": corr,
                                "png": str(img_path), "bytes": size,
                                "url": target, "mode": mode})
            else:
                self.send_json({"ok": False, "session": session, "corr_id": corr,
                                "error": err}, 502)
        else:
            # Human button: fire-and-forget so the dashboard doesn't block.
            import threading
            threading.Thread(target=do_capture, daemon=True).start()
            self.send_json({"ok": True, "session": session, "corr_id": corr,
                            "message": "Screenshot capturing…"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self):
        self._t0 = time.time()
        self._actor = "anon"
        path = self.path.split("?")[0]

        if path == "/api/sessions":
            email, err = self.require_auth()
            if err:
                self.send_json({"error": err}, 401)
                return
            self.send_json(get_sessions())

        elif path == "/api/events/stream":
            # SSE: live event tail + reload signals (docs/SSOT.md §7).
            email, err = self.require_auth()
            if err:
                self.send_json({"error": err}, 401)
                return
            self._sse_open()
            q = dtlog.BUS.subscribe()
            dtlog.emit("sse.connect", actor=self._actor,
                       subscribers=dtlog.BUS.count(), stream=False)
            try:
                self._sse_send({"event": "hello", "ts": dtlog._iso_now()})
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        ev = None
                    if ev is None:
                        # idle keepalive (write raises if the socket is dead)
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    else:
                        self._sse_send(ev)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                dtlog.BUS.unsubscribe(q)
                dtlog.emit("sse.close", actor=self._actor,
                           subscribers=dtlog.BUS.count(), stream=False)
            return

        elif path == "/api/fleet":
            # Declared fleet (SSOT) — what SHOULD exist, vs /api/sessions (live).
            email, err = self.require_auth()
            if err:
                self.send_json({"error": err}, 401)
                return
            self.send_json(FLEET.public_view())

        elif path == "/api/screenshot/self":
            # Agent calls from localhost with no token — auto-detect session name.
            if not (self.is_loopback() or MOCK):
                email, err = self.require_auth()
                if err:
                    self.send_json({"error": err}, 401)
                    return
            session = self._session_from_tmux()
            if not session:
                self.send_json({
                    "error": "Cannot determine session from TMUX env. "
                             "Are you running inside a tmux session?"
                }, 400)
                return
            self._actor = "agent:" + session
            self._handle_screenshot(session)

        elif path.startswith('/api/screenshot/'):
            self._handle_screenshot(path[len('/api/screenshot/'):].strip('/'))

        elif path.startswith('/api/inject/'):
            session = path[len('/api/inject/'):].strip('/')
            email, err = self.require_auth()
            if err:
                self.send_json({'error': err}, 401)
                return
            port = FLEET.canvas_port(session) or '????'
            msg = (
                "printf '\\n\\033[1;36m=== dreamTerm canvas tool ===\\033[0m\\n"
                "canvas shot               # screenshot your dev server → Dr. Rozen sees it\\n"
                "canvas open <url>         # screenshot any URL\\n"
                "canvas shot --chafa       # also render ANSI art in this terminal\\n"
                "Your canvas port: {port}  (run: npx next dev --port {port} --host 0.0.0.0)\\n"
                "\\033[0m\\n'"
            ).format(port=port)
            if MOCK:
                self.send_json({'ok': True, 'session': session, 'mock': True})
                return
            try:
                subprocess.run(['tmux', 'send-keys', '-t', session, msg, 'Enter'],
                               timeout=5, check=True)
                dtlog.emit("tool.invoke", session=session, actor=self._actor,
                           tool="inject_canvas_onboarding", port=port)
                self.send_json({'ok': True, 'session': session})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        elif path == "/" or path == "/index.html":
            f = STATIC_DIR / "index.html"
            if f.exists():
                body = f.read_bytes()
                if MOCK:
                    # Tell the page to skip Firebase and go straight to the
                    # dashboard, so it can be browser-tested without sign-in.
                    body = body.replace(
                        b"<head>",
                        b"<head><script>window.DREAMTERM_MOCK=true;</script>", 1)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                self._emit_http(200)
            else:
                self.send_error(404)

        else:
            self.send_error(404)


    # ── Canvas control plane ────────────────────────────────────────────────────
    def _canvas_session(self, path_prefix):
        """Extract session name from e.g. /api/canvas/youtube/reload."""
        prefix = path_prefix.rstrip("/")
        if not self.path.startswith(prefix + "/"):
            return None
        rest = self.path[len(prefix) + 1:].strip("/")
        # rest is "reload" or "file" or "push"
        return rest.split("/")[0] if rest else None

    def do_GET_canvas(self, subpath):
        # GET /api/canvas/<session>/file → serve the canvas artifact
        if subpath == "file":
            session = self._canvas_session("/api/canvas")
            if not session:
                self.send_error(404)
                return
            root = Path(FLEET.canvas_root) / session
            index = root / "index.html"
            if index.exists():
                body = index.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Canvas is empty. Agent has not pushed content yet.")
            return
        self.send_error(404)

    def do_POST_canvas(self, subpath):
        # POST /api/canvas/<session>/reload → signal iframe to reload
        if subpath == "reload":
            session = self._canvas_session("/api/canvas")
            if not session:
                self.send_error(404)
                return
            # Loopback: agent calling itself; web auth: human clicking reload
            if not (self.is_loopback() or MOCK):
                email, err = self.require_auth()
                if err:
                    self.send_json({"error": err}, 401)
                    return
            dtlog.emit("canvas.reload", session=session, actor=self._actor)
            self.send_json({"ok": True, "session": session})
            return

        # POST /api/canvas/<session>/push → agent pushes HTML/PNG artifact
        if subpath == "push":
            session = self._canvas_session("/api/canvas")
            if not session:
                self.send_error(404)
                return
            # Always allow loopback (agents); require auth from web
            if not (self.is_loopback() or MOCK):
                email, err = self.require_auth()
                if err:
                    self.send_json({"error": err}, 401)
                    return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
            except Exception:
                self.send_json({"ok": False, "error": "Cannot read body"}, 400)
                return
            # Determine content type
            ctype = self.headers.get("Content-Type", "")
            dest = Path(FLEET.canvas_root) / session
            dest.mkdir(parents=True, exist_ok=True)
            if "text/html" in ctype or "html" in ctype.lower():
                out = dest / "index.html"
                out.write_bytes(body)
                dtlog.emit("canvas.push", session=session, actor=self._actor,
                           type="html", size=len(body))
                self.send_json({"ok": True, "session": session,
                                "url": f"/api/canvas/{session}/file"})
            elif "image/" in ctype:
                out = dest / "artifact.png"
                out.write_bytes(body)
                index_html = dest / "index.html"
                index_html.write_text(
                    f'<html><body style="margin:0;background:#111">'
                    f'<img src="artifact.png" style="width:100vw;height:100vh;object-fit:contain"/>'
                    f'</body></html>'
                )
                dtlog.emit("canvas.push", session=session, actor=self._actor,
                           type="image", size=len(body))
                self.send_json({"ok": True, "session": session,
                                "url": f"/api/canvas/{session}/file"})
            else:
                self.send_json({"ok": False,
                                "error": "Content-Type must be text/html or image/*"}, 400)
            return

        self.send_error(404)
    def do_POST(self):
        self._t0 = time.time()
        self._actor = "anon"
        path = self.path.split("?")[0]

        if path == "/api/auth/verify":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body)
            except Exception:
                self.send_json({"ok": False, "error": "Invalid request body"}, 400)
                return

            id_token = data.get("idToken", "")
            email, err = verify_token(id_token)
            if err:
                self.send_json({"ok": False, "error": err}, 401)
                return

            if not ALLOW_ALL:
                allowed = {e.lower() for e in ALLOWED_EMAILS}
                if email.lower() not in allowed and not email.startswith("offline-"):
                    self.send_json({"ok": False, "error": "Access denied"}, 403)
                    return

            self.send_json({"ok": True, "email": email})

        elif path == "/api/fleet/reload":
            # Trusted plane: re-read fleet.json from disk (loopback only).
            if not (self.is_loopback() or MOCK):
                self.send_json({"ok": False, "error": "loopback only"}, 403)
                return
            global FLEET
            try:
                FLEET = fleet_mod.load()
                self.send_json({"ok": True, "sessions": len(FLEET.sessions)})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)

        elif path.startswith("/api/canvas/"):
            subpath = path[len("/api/canvas/"):].strip("/").split("/", 1)[-1]
            self.do_POST_canvas(subpath)
            return
        elif path.startswith("/api/agent/") and path.endswith("/prompt"):
            # POST /api/agent/<session>/prompt — inject canvas-use prompt into tmux
            prefix = "/api/agent/"
            if not self.path.startswith(prefix):
                self.send_error(404); return
            session = self.path[len(prefix):].rstrip("/")
            if session.endswith("/prompt"):
                session = session[:-len("/prompt")]
            if not session:
                self.send_json({"ok": False, "error": "missing session name"}, 400); return
            if not (self.is_loopback() or MOCK):
                email, err = self.require_auth()
                if err: self.send_json({"error": err}, 401); return
            port = FLEET.canvas_port(session)
            port_str = str(port) if port else "???<see fleet.json>???"
            lines = [
                "",
                "\033[1;36m╔════════════════════════════════════════════════════════╗\033[0m",
                "\033[1;36m║  Canvas enabled for your session — Dr. Rozen is watching\033[0m",
                "\033[1;36m╚════════════════════════════════════════════════════════╝\033[0m",
                "",
                "  Your Canvas: \033[1;33mhttp://localhost:" + port_str + "\033[0m  (Dr. Rozen sees this live)",
                "",
                "  \033[1;32mdt shot\033[0m              → see your UI as ASCII art in this terminal",
                "  \033[1;32mdt push file.html\033[0m  → Dr. Rozen's Canvas panel shows it instantly",
                "  \033[1;32mdt push image.png\033[0m   → Dr. Rozen sees the image",
                "  \033[1;32mdt reload\033[0m          → refresh Canvas after pushing",
                "",
                "  Start dev server: \033[1;33mnpx next dev --port " + port_str + " --host 0.0.0.0\033[0m",
                "",
                "  Run \033[1;33mdt help\033[0m for full guide.",
                "",
            ]
            try:
                for line in lines:
                    subprocess.run(["tmux", "send-keys", "-t", session, line], timeout=3)
                subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], timeout=3)
                dtlog.emit("tool.invoke", session=session, actor=self._actor, tool="inject_canvas_prompt")
                self.send_json({"ok": True, "session": session, "port": port_str})
            except subprocess.TimeoutExpired:
                self.send_json({"ok": False, "error": "tmux timeout"}, 500)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        else:
            self.send_error(404)

if __name__ == "__main__":
    print("Agent Dashboard on http://0.0.0.0:{}".format(PORT))
    print("Allowed: {}".format("ALL" if ALLOW_ALL else ", ".join(sorted(ALLOWED_EMAILS))))
    if MOCK:
        print("*** DREAMTERM_MOCK mode — fake fleet, auth bypassed ***")
    dtlog.emit("server.start", port=PORT, mock=MOCK,
               sessions=len(FLEET.sessions), log_dir=str(LOG_DIR), stream=False)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
