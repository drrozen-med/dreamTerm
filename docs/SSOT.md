# dreamTerm — Single Source of Truth (SSOT)

> **Status:** Phases 0–3 shipped and live on VM 187.124.181.251. Phase 4 next.
> **Owner:** Dr. Rozen.
> **This file is canonical.** When code, README, or SKILL.md disagree with this document, this document wins.

---

## 1. Vision

dreamTerm gives **every AI agent its own browser** and gives **Dr. Rozen one window into all of them.**

Agents without a built-in browser (Pi, OpenCode, HERMES) are blind on front-end work. Cursor and Codex have native browser/vision loops; the rest do not. dreamTerm closes that gap by running **one central Playwright instance** that any agent reaches with a single HTTP call — no Playwright, no CDP, no deps on the agent side. The agent edits code → `canvas shot` → reads a real PNG → fixes. Front-end debugging master.

For Dr. Rozen: one dashboard showing every agent's terminal and visual output side by side, with the ability to inject visual feedback into any agent's context.

North star: **Lovable's feel** — code changes appear in a live preview instantly, no flicker, no manual steps. dreamTerm generalizes that to any visual: dev server, diagram, PNG, SVG, HTML chart, arbitrary URL.

---

## 2. Core architectural insight

**The browser is centralized and multiplexed. The agent stays dependency-free.**

```
   Agent (Pi)  ────┐
   Agent (HERMES) ─┤  dreamTerm server :4000
   Agent (OpenCode)┤  ── ONE Playwright pool ──► screenshots / DOM
   Agent (Codex) ──┘
         ▲
         │ single HTTP call: curl 127.0.0.1:4000/api/screenshot/<session>?sync=1
         │ or: canvas shot / canvas open <url>
         │ NO playwright, NO chromium, NO CDP on agent side
```

---

## 3. The three loops

| # | Name | Trigger | Fidelity | Purpose |
|---|------|---------|----------|---------|
| **1** | Human→Agent (chafa) | Dr. Rozen clicks 📸 | Lossy ANSI blocks as text | Vision-less LLM gets gist of UI |
| **2** | Agent→Self (real PNG) | Agent calls `canvas shot` | Full pixels via native image input | Vision model debugs front-end autonomously |
| **3** | Lovable mode | Agent runs `canvas reload` | Live rendered app | Dr. Rozen watches UI evolve in real time |

**Critical distinction:** Loop 1 sends chafa (colored Unicode blocks read as text — gist only). Loop 2 sends the real PNG. Never send chafa to a vision-capable model when the real artifact is available. The tool always saves the real PNG first; chafa is an opt-in add-on (`--chafa` or `--both`).

---

## 4. System architecture

```
PUBLIC PLANE (API key or Firebase)     TRUSTED PLANE (loopback only)
Browser ──► nginx :80 ──► server.py :4000 ◄── agents via curl/canvas CLI
                           │
              ┌────────────┼──────────────┐
              ▼            ▼              ▼
         Fleet registry  Playwright   SSE event bus
         (fleet.json)    pool         (dtlog.BUS)
              │                          │
              ▼                          ▼
        /api/sessions             /api/events/stream
        /api/fleet                canvas.reload events
```

**Two security planes:**
- **Public** — API key (`?key=`) or Firebase Bearer token. Key stored in `localStorage` after first visit.
- **Trusted** — loopback (`127.0.0.1`) only. Agents call from inside the VM. No token required. Path-jailed to `/home/claude-agent/canvas/<session>/`.

---

## 5. Single Source of Truth: `fleet.json`

One file defines the entire fleet. `server.py`, `canvas` CLI, the dashboard (`/api/fleet`), and `scripts/sync_fleet.sh` all read it. Nothing else maintains port/URL mappings.

**Schema per session:**
```json
{ "name": "redit", "ttyd_port": 7682, "canvas_port": 8089, "canvas_mode": "lovable", "label": "Reddit" }
```

**Adding an agent:** edit `fleet.json` → create ttyd systemd unit + nginx route on VM → `POST /api/fleet/reload` → run `scripts/sync_fleet.sh` to verify.

---

## 6. Agent tool surface (`canvas` CLI)

Installed at `/usr/local/bin/canvas`. Auto-detects session from `$TMUX`. Zero agent-side deps.

| Command | What it does |
|---------|--------------|
| `canvas shot` | Screenshot own canvas port → real PNG path (vision model reads it) |
| `canvas shot --chafa` | + ANSI art in terminal (vision-less fallback) |
| `canvas shot --both` | Both |
| `canvas open <url>` | Screenshot any URL → real PNG path (general browser) |
| `canvas reload` | Signal dashboard Canvas panel to refresh (Loop 3) |

**Canonical agent loop:**
```bash
# edit code...
canvas shot          # → /home/claude-agent/canvas/<s>/shots/<corr>.png
# Read that PNG natively (full pixels), spot the bug, fix, repeat
canvas reload        # → Dr. Rozen's Canvas updates live
```

---

## 7. HTTP API contract

| Method | Path | Plane | What |
|--------|------|-------|------|
| GET | `/` | public (no auth) | Dashboard HTML |
| POST | `/api/auth/verify` | public | Firebase token verify |
| GET | `/api/sessions` | public | Live fleet: tmux + agent types + CPU/mem |
| GET | `/api/fleet` | public | Declared fleet (fleet.json) |
| GET | `/api/screenshot/<s>?sync=1&mode=vision` | both | Screenshot; loopback=agent, authed=human |
| GET | `/api/inject/<s>` | public | Inject canvas onboarding into agent terminal |
| POST | `/api/canvas/<s>/reload` | both | Emit SSE reload event for session |
| POST | `/api/fleet/reload` | trusted | Re-read fleet.json |
| GET | `/api/events/stream` | public | SSE: live events + reload signals |

**Push transport:** Server-Sent Events (SSE) on `/api/events/stream`. Zero new deps on the VM (long-lived `text/event-stream` GET on `ThreadingHTTPServer`).

---

## 8. Dashboard GUI

**Panels:**
- **Sidebar** — sessions with agent type badge, CPU%, live/idle dot. No commands here.
- **Terminal panel** — ttyd iframe for selected session. Header: `📋 Canvas tool` (injects onboarding with correct port) + `↗ New tab`.
- **Canvas panel** — agent's dev server or any visual. Auto-reloads on `canvas.reload` SSE event with a green pulse. Header: `📸 Canvas` (human-triggered screenshot + chafa) + `↗ Tab`.
- **Activity panel** — collapsible bottom-right. SSE event tail, newest-first, color-coded by event type.

**Auth:** API key in `localStorage` (persists forever). First visit with `?key=` stores it. Subsequent visits to bare `/` go straight to dashboard. Firebase still works for Google sign-in when no key is present.

**Friendly errors:** both Terminal and Canvas panels show a clean message instead of raw 404/502 when the route isn't available.

---

## 9. `📋 Canvas tool` button

In the **terminal panel header** (not the sidebar). When clicked:
1. Calls `GET /api/inject/<session>`
2. Server runs `tmux send-keys` with a formatted onboarding message
3. Message includes the correct canvas port for that session from fleet.json
4. Agent reads it and knows exactly how to use the canvas tool

---

## 10. Logging

**Format:** JSONL, one event per line.
**Sinks:** `/var/log/dreamterm/server.jsonl` (rotating 10MB×5) + `/var/log/dreamterm/sessions/<name>.jsonl` per agent + stderr compact line for journalctl.
**Correlation:** every loop iteration gets a `corr_id` (e.g. `c_abc123ef`), threaded through `screenshot.request → screenshot.ok → canvas.reload`. Single grep reconstructs the story.
**Tokens redacted** to `tok_…last4`.

**Event taxonomy:** `server.start`, `http.request`, `auth.verify`, `fleet.scan`, `fleet.reload`, `tool.invoke`, `screenshot.request`, `screenshot.ok`, `screenshot.fail`, `canvas.reload`, `canvas.publish`, `canvas.bind`, `sse.connect`, `sse.close`, `error`.

---

## 11. Security model

- **Two planes** enforced at the handler level (not just nginx): public requires key/token; trusted requires loopback source address.
- **API key** — 32-char random, stored in systemd `Environment=DREAMTERM_API_KEY=...`, never in the repo.
- **localStorage persistence** — key survives tab/browser restart. To revoke: rotate the key in the service and restart.
- **Path jail** — artifact publish restricted to `/home/claude-agent/canvas/<session>/`, validated server-side.
- **CORS** — `Access-Control-Allow-Origin: self` (bug: should be the dashboard origin explicitly — Phase 5 fix).
- **Future hardening:** IP allowlist in nginx for when Dr. Rozen has a stable IP or VPN.

---

## 12. Build phases — status

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **0** | `fleet.json` SSOT — kill triplicated maps in server.py | ✅ Shipped |
| **1** | Structured logging + SSE skeleton + Activity panel in GUI | ✅ Shipped |
| **2** | `canvas` CLI + dual-plane screenshot + loopback security + Playwright install | ✅ Shipped |
| **3** | `canvas reload` + SSE reload bus + Canvas auto-refresh in GUI | ✅ Shipped |
| **4** | `canvas show <file>` (static artifacts) + `canvas open` improvements | 🔲 Next |
| **5** | GUI polish: fleet-driven sidebar, Activity filter, CORS fix, full test suite | 🔲 Pending |

---

## 13. Open items / known issues

- **Unregistered live sessions** — sessions in `tmux ls` but not in `fleet.json` (e.g. `exam_coach`, `0`) show with no terminal/canvas. Phase 5: flag them in the sidebar.
- **`canvas show <file>`** — agent pushes a static PNG/SVG/HTML to the Canvas panel. Phase 4.
- **Canvas mode badge** — Canvas header should show the current mode (lovable/artifact/browser). Phase 5.
- **CORS header** — currently `"self"` (invalid value). Phase 5 fix.
- **Activity panel filter** — per-session filter not yet built. Phase 5.
- **IP allowlist** — when Dr. Rozen has a stable IP or uses a VPN, add to nginx for defense-in-depth.
- **"Leave site?" dialog** — ttyd terminals trigger the browser's unload dialog on navigation. Minor UX annoyance; can be suppressed with `beforeunload` handler in the dashboard.
