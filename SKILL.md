# VM Agent Monitor — Operations Skill

## Quick Status
```bash
ssh root@187.124.181.251 "python3 /root/vm_audit.py"
```

## Architecture
```
Browser → nginx (80) → dashboard server.py (4000)
                     → ttyd terminals     (7681-7689)
                     → canvas/dev servers  (8081-8089)
```

## Per-Agent Canvas System

Each agent has:
- **Terminal**: `/terminal/<session>/` → ttyd on port 7681-7689
- **Canvas**: `/preview/<session>/` → dev server on port 8081-8089
- **Screenshot**: click "📸 Canvas" in dashboard → agent sees chafa render

### Canvas Port Map
| Session                 | ttyd | Canvas |
|------------------------|------|--------|
| alex_nursgebride_funnel | 7681 | 8088  |
| redit                  | 7682 | 8089  |
| hermes_daily_cherish   | 7683 | 8085  |
| hermes_fb_page_admin   | 7684 | 8086  |
| hermes_solids          | 7685 | 8087  |
| youtube                | 7686 | 8081  |
| obyx_code              | 7687 | 8082  |
| sleepy_sounds          | 7688 | 8083  |
| yakov_gcp              | 7689 | 8084  |

### Agent Startup Instructions
Tell each agent at startup:
```
Your canvas is at http://localhost:XXXXX
Start: npx next dev --port XXXXX --host 0.0.0.0
Or:   python3 -m http.server XXXXX --bind 0.0.0.0
When Dr. Rozen clicks Canvas you see a screenshot rendered as ASCII art.
Run chafa directly (do NOT pipe through head or cat).
```

### Screenshot Flow
1. Dashboard: click "📸 Canvas"  
2. `GET /api/screenshot/<session>` → server.py background thread
3. Playwright screenshots `http://127.0.0.1:<port>/`
4. PNG saved to `/home/claude-agent/screenshots/<session>/latest.png`
5. `chafa <path>` sent to agent's tmux pane
6. Agent sees ASCII-art render

## Key Files
| Purpose | Path |
|---------|------|
| Dashboard server | `/home/claude-agent/agent-dashboard/server.py` |
| Dashboard UI | `/home/claude-agent/agent-dashboard/public/index.html` |
| ttyd helper (has -W flag) | `/usr/local/bin/ttyd-session.sh` |
| nginx config | `/etc/nginx/sites-enabled/agent-dashboard` |
| Screenshots | `/home/claude-agent/screenshots/<session>/latest.png` |
| Audit script | `/root/vm_audit.py` |
| Kill empty sessions | `/root/kill_empty_sessions.py` |
| imgcat (iTerm2) | `/usr/local/bin/imgcat` |
| chafa (ANSI art) | `/usr/bin/chafa` (apt install chafa) |

## Common Operations
```bash
# Restart dashboard
systemctl restart agent-dashboard

# Restart ttyd for a session
systemctl restart ttyd-<session_name>.service

# Kill empty sessions
python3 /root/kill_empty_sessions.py

# Add new agent session
# 1. Create ttyd service in /etc/systemd/system/ttyd-<session>.service
# 2. systemctl daemon-reload && systemctl start ttyd-<session>
# 3. Add nginx route in /etc/nginx/sites-enabled/agent-dashboard
# 4. Add to PORT_MAP, PREVIEW_MAP, PREVIEW_PORT_MAP in server.py
# 5. systemctl restart agent-dashboard
# 6. Tell agent their canvas port

# Verify ttyd is writable
grep 'ttyd.*-W' /usr/local/bin/ttyd-session.sh
```

## Agent Types
- HERMES: `hermes` process in tmux (python3)
- PI: `pi` process in tmux (terminal agent)
- CODEX: `codex` process
- HUMAN-ET: `etterminal` process
