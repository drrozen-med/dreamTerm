#!/usr/bin/env python3
"""
Agent Dashboard API server — runs as claude-agent, with Firebase Auth.
Serves:
  GET  /                → index.html (no auth required)
  GET  /api/sessions    → tmux session list + agent detection (auth required)
  POST /api/auth/verify → verify Firebase ID token, check allowlist
"""
import json, subprocess, os, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 4000
STATIC_DIR = Path(__file__).parent / "public"

# ── Allowlist ──────────────────────────────────────────────────────────────────
ALLOWED_EMAILS = {
    "drrozen@gmail.com",
    "urirozen@gmail.com",
    "dr.rozen@concise-med.com",
}
# Set True for quick testing (any valid Firebase token is accepted)
ALLOW_ALL = False

# ── Firebase Admin SDK ────────────────────────────────────────────────────────
FIREBASE_CREDS_PATH = "/home/claude-agent/NurseBridge-prep/credentials/service_accounts/nursebridge-prep-firebase-adminsdk-fbsvc-38884db962.json"

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
PORT_MAP = {
    "alex_nursgebride_funnel": 7681,
    "redit":                   7682,
    "hermes_daily_cherish":  7683,
    "hermes_fb_page_admin":  7684,
    "hermes_solids":        7685,
    "youtube":               7686,
    "obyx_code":             7687,
    "sleepy_sounds":         7688,
    "yakov_gcp":             7689,
}
PREVIEW_PORT_MAP = {
    "alex_nursgebride_funnel":  8088,
    "redit":                    8089,
    "hermes_daily_cherish":     8085,
    "hermes_fb_page_admin":     8086,
    "hermes_solids":            8087,
    "youtube":                  8081,
    "obyx_code":                8082,
    "sleepy_sounds":            8083,
    "yakov_gcp":                8084,
}

PREVIEW_MAP = {
    # Per-agent canvas (dev server) — agent should run: npx next dev --port XXXX
    "alex_nursgebride_funnel":  "/preview/alex_nursgebride_funnel/",
    "redit":                    "/preview/redit/",
    "hermes_daily_cherish":     "/preview/hermes_daily_cherish/",
    "hermes_fb_page_admin":     "/preview/hermes_fb_page_admin/",
    "hermes_solids":            "/preview/hermes_solids/",
    "youtube":                  "/preview/youtube/",
    "obyx_code":                "/preview/obyx_code/",
    "sleepy_sounds":            "/preview/sleepy_sounds/",
    "yakov_gcp":                "/preview/yakov_gcp/",
}

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

def get_sessions():
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
            "terminal_port": PORT_MAP.get(name),
            "terminal_url": "/terminal/{}/".format(name) if name in PORT_MAP else None,
            "preview_url": PREVIEW_MAP.get(name),
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
    return sessions

# ── HTTP Handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def require_auth(self):
        """Check Bearer token. Returns (email, None) or (None, error_str)."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None, "Missing or invalid Authorization header"
        id_token = auth[7:]
        email, err = verify_token(id_token)
        if err:
            return None, err
        if not ALLOW_ALL:
            allowed = {e.lower() for e in ALLOWED_EMAILS}
            if email.lower() not in allowed and not email.startswith("offline-"):
                return None, "Access denied: {} not in allowlist".format(email)
        return email, None

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "self")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/sessions":
            email, err = self.require_auth()
            if err:
                self.send_json({"error": err}, 401)
                return
            self.send_json(get_sessions())


        elif path.startswith('/api/screenshot/'):
            session = path[len('/api/screenshot/'):].strip('/')
            email, err = self.require_auth()
            if err:
                self.send_json({'error': err}, 401)
                return
            # Run screenshot in background so we don't block the response
            import threading
            def background():
                from pathlib import Path
                screenshot_dir = Path('/home/claude-agent/screenshots')
                screenshot_dir.mkdir(exist_ok=True)
                img_path = screenshot_dir / session / 'latest.png'
                img_path.parent.mkdir(exist_ok=True)
                port = PREVIEW_PORT_MAP.get(session, 8080)
                url = 'http://127.0.0.1:{}'.format(port)
                try:
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True)
                        page = browser.new_page(viewport={'width': 1280, 'height': 800})
                        page.goto(url, timeout=15000, wait_until='domcontentloaded')
                        page.wait_for_timeout(2000)
                        page.screenshot(path=str(img_path), full_page=False)
                        browser.close()
                    # Send chafa command to agent's tmux so they see it
                    subprocess.run(
                        ['tmux', 'send-keys', '-t', session,
                         "echo '''=== Canvas screenshot ==='''", 'Enter'],
                        timeout=5
                    )
                    subprocess.run(
                        ['tmux', 'send-keys', '-t', session,
                         'chafa {}'.format(img_path), 'Enter'],
                        timeout=10
                    )
                except Exception:
                    pass  # silent — best-effort
            t = threading.Thread(target=background, daemon=True)
            t.start()
            self.send_json({'ok': True, 'session': session, 'message': 'Screenshot captured, sending to agent...'})

        elif path == "/" or path == "/index.html":
            f = STATIC_DIR / "index.html"
            if f.exists():
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        else:
            self.send_error(404)

    def do_POST(self):
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

        else:
            self.send_error(404)

if __name__ == "__main__":
    print("Agent Dashboard on http://0.0.0.0:{}".format(PORT))
    print("Allowed: {}".format("ALL" if ALLOW_ALL else ", ".join(sorted(ALLOWED_EMAILS))))
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
