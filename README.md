# dreamTerm — Agent Dashboard + Browser

Mission control for the VM agent fleet at `187.124.181.251`. Gives every AI agent a browser. Gives Dr. Rozen one window into all of them.

## Access

```
http://187.124.181.251/
```

First visit: use the key URL once → stored in `localStorage` → never asked again:
```
http://187.124.181.251/?key=<DREAMTERM_API_KEY>
```
Key is set in `/etc/systemd/system/agent-dashboard.service` as `Environment=DREAMTERM_API_KEY=...`

## What's inside

| File | Purpose |
|------|---------|
| `server.py` | Dashboard API server (port 4000) — key auth + Firebase + tmux fleet scan |
| `fleet.py` | Loader for `fleet.json` — the single source of truth for the fleet |
| `fleet.json` | **SSOT** — all session names, ttyd ports, canvas ports. Edit here first. |
| `dtlog.py` | Structured JSONL logging + SSE event bus |
| `public/index.html` | Dashboard UI — terminals, canvas panel, activity log |
| `bin/canvas` | Agent CLI — gives any agent a browser with one command |
| `ttyd-session.sh` | Starts ttyd for a tmux session (has `-W` writable flag) |
| `vm_audit.py` | Audit script — lists tmux sessions, detects agent types |
| `kill_empty_sessions.py` | Kills tmux sessions with no active agent |
| `SKILL.md` | Operations runbook |
| `docs/SSOT.md` | Canonical design document — wins over all other sources |
| `scripts/sync_fleet.sh` | Drift detector: fleet.json vs live VM ttyd units |

## Agent browser tool (`canvas`)

Installed at `/usr/local/bin/canvas` on the VM. Any agent calls it with no deps:

```bash
canvas shot                # screenshot my canvas port → PNG path (vision models read it)
canvas shot --chafa        # + ANSI art in terminal (vision-less fallback)
canvas open <url>          # screenshot any URL → PNG path
canvas reload              # signal dashboard to refresh Canvas panel (Loop 3)
```

Session is auto-detected from `$TMUX`. One HTTP call to `127.0.0.1:4000`, no Playwright, no CDP on the agent side.

## Dashboard features

| Feature | How |
|---|---|
| **Live terminals** | ttyd iframes per session, writable |
| **Canvas panel** | Agent's dev server or any visual, auto-reloads on `canvas reload` |
| **📋 Canvas tool** button | Injects canvas onboarding (with correct port) into any agent's terminal |
| **📸 Canvas** button | Screenshots agent's canvas → sends chafa to agent terminal |
| **Activity panel** | Live SSE event tail — every screenshot/reload with its `corr_id` |
| **Persistent auth** | API key in `localStorage` — never prompts again |

## The three loops

1. **Human → Agent (chafa):** Click 📸 → ANSI art appears in agent's terminal. For vision-less models.
2. **Agent → Self (real PNG):** `canvas shot` → agent reads full-fidelity PNG natively. The real loop.
3. **Lovable mode:** Agent runs `canvas reload` → Canvas panel refreshes in Dr. Rozen's browser instantly.

## Logs

```
/var/log/dreamterm/server.jsonl          # all events, rotating 10MB×5
/var/log/dreamterm/sessions/<name>.jsonl # per-agent stream
```

Each loop iteration gets a `corr_id` — grep it to reconstruct the full story:
```bash
grep c_abc123 /var/log/dreamterm/server.jsonl
```

## Fleet management

The fleet is defined **only** in `fleet.json`. Adding an agent:
1. Edit `fleet.json` — add entry with `name`, `ttyd_port`, `canvas_port`
2. Create systemd ttyd unit + nginx route on the VM
3. `POST http://127.0.0.1:4000/api/fleet/reload` (or restart the service)
4. Run `scripts/sync_fleet.sh` to verify no drift

## Development

```bash
# Local mock server (no VM, no Firebase, fake fleet)
DREAMTERM_MOCK=1 python3 server.py

# Check fleet.json vs live VM
./scripts/sync_fleet.sh

# Sanity check fleet.py
python3 fleet.py
```

## VM operations

```bash
systemctl restart agent-dashboard      # restart dashboard
systemctl restart ttyd-<session>       # restart one terminal
journalctl -u agent-dashboard -f       # follow logs
python3 /root/kill_empty_sessions.py   # kill empty tmux sessions
python3 /root/vm_audit.py              # audit all sessions
```
