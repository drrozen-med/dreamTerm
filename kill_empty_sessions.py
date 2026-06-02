#!/usr/bin/env python3
"""Kill all empty tmux sessions (no running agent inside)."""
import subprocess

TMUX_CMD = ['su', '-', 'claude-agent', '-s', '/bin/bash', '-c']

def run(cmd):
    r = subprocess.run(TMUX_CMD + [cmd], capture_output=True, text=True)
    return r.stdout.strip()

def get_agent_type(name):
    pane = run('tmux list-panes -t "' + name + '" -F "#{pane_pid}"')
    if not pane:
        return 'EMPTY'
    kids = ' '.join(
        l.strip() for l in
        subprocess.run(['ps', '--ppid', pane, '-o', 'comm', '--no-headers'],
                       capture_output=True, text=True).stdout.strip().splitlines()
        if l.strip() and l.strip() != pane
    )
    if 'hermes' in kids:         return 'HERMES'
    if kids == 'pi' or ' pi ' in kids: return 'PI'
    if 'codex' in kids:          return 'CODEX'
    if 'etterminal' in kids:      return 'HUMAN-ET'
    if kids:                      return 'PROCESS'
    return 'EMPTY'

sessions = run('tmux ls -F "#{session_name}"').splitlines()
print(f'Total sessions: {len(sessions)}')
print()

killed = []
skipped = []

for name in sessions:
    agent = get_agent_type(name)
    if agent == 'EMPTY':
        result = subprocess.run(
            TMUX_CMD + ['tmux kill-session -t "' + name + '"'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            killed.append(name)
            print(f'  KILLED: {name}')
        else:
            print(f'  FAILED: {name} — {result.stderr.strip()}')
    else:
        skipped.append((name, agent))
        print(f'  KEPT:   {name} ({agent})')

print()
print(f'Killed {len(killed)} empty sessions')
print(f'Kept {len(skipped)} active sessions')
