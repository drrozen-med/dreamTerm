import subprocess

TMUX_CMD = ['su', '-', 'claude-agent', '-s', '/bin/bash', '-c']

def tmux_cmd(cmd):
    r = subprocess.run(TMUX_CMD + [cmd], capture_output=True, text=True)
    return [l for l in r.stdout.strip().splitlines() if l]

def get_agent_type(session_name):
    r = subprocess.run(TMUX_CMD + ['tmux list-panes -t "' + session_name + '" -F "#{pane_pid}"'], capture_output=True, text=True)
    pane = r.stdout.strip()
    if not pane:
        return 'EMPTY'
    r = subprocess.run(['ps', '--ppid', pane, '-o', 'comm', '--no-headers'], capture_output=True, text=True)
    kids = ' '.join([l.strip() for l in r.stdout.strip().splitlines() if l.strip() and l.strip() != pane])
    if 'hermes' in kids:
        return 'HERMES'
    if kids == 'pi' or ' pi ' in kids:
        return 'PI'
    if 'codex' in kids:
        return 'CODEX'
    if 'etterminal' in kids:
        return 'HUMAN-ET'
    if kids:
        return 'PROCESS:' + kids.split()[0]
    return 'EMPTY'

def get_cpu_mem(session_name):
    r = subprocess.run(TMUX_CMD + ['tmux list-panes -t "' + session_name + '" -F "#{pane_pid}"'], capture_output=True, text=True)
    pane = r.stdout.strip()
    if not pane:
        return 0.0, 0.0
    r = subprocess.run(['ps', '--ppid', pane, '-o', 'pid', '--no-headers'], capture_output=True, text=True)
    pids = [pane] + [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
    if not pids:
        return 0.0, 0.0
    r = subprocess.run(['ps', '-p', ','.join(pids), '-o', '%cpu,rss', '--no-headers'], capture_output=True, text=True)
    total_cpu = 0.0
    total_mem = 0
    for line in r.stdout.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            try:
                total_cpu += float(parts[0])
                total_mem += int(parts[1])
            except ValueError:
                pass
    return round(total_cpu, 1), round(total_mem / 1024, 1)

def run_audit():
    sessions = tmux_cmd('tmux ls -F "#{session_name}|#{session_attached}"')
    print('TOTAL SESSIONS: {}'.format(len(sessions)))
    print()

    agents = {'HERMES': [], 'PI': [], 'CODEX': [], 'HUMAN-ET': [], 'OTHER': [], 'EMPTY': []}
    rows = []

    for line in sessions:
        parts = line.split('|')
        if len(parts) < 2:
            continue
        name = parts[0]
        attached = parts[1] == '1'
        agent = get_agent_type(name)
        cpu, mem = get_cpu_mem(name)

        if agent == 'HERMES':
            agents['HERMES'].append(name)
        elif agent == 'PI':
            agents['PI'].append(name)
        elif agent == 'CODEX':
            agents['CODEX'].append(name)
        elif agent == 'HUMAN-ET':
            agents['HUMAN-ET'].append(name)
        elif agent.startswith('PROCESS:'):
            agents['OTHER'].append(name)
        else:
            agents['EMPTY'].append(name)

        mark = 'ACTIVE' if attached else 'idle '
        rows.append((name, mark, agent, cpu, mem))

    def sort_key(r):
        p = {'HERMES': 0, 'PI': 1, 'CODEX': 2, 'HUMAN-ET': 3, 'OTHER': 4, 'EMPTY': 5}
        return (p.get(r[2], 5), -r[3])

    rows.sort(key=sort_key)

    print('{:<38} {:<8} {:<14} {:>6} {:>8}'.format('Session', 'Status', 'Agent', 'CPU%', 'MEM(MB)'))
    print('-' * 80)
    for name, mark, agent, cpu, mem in rows:
        print('  {:<36} [{}]  {:<14} {:>5.1f}% {:>7.1f} MB'.format(name, mark, agent, cpu, mem))

    print()
    print('=' * 80)
    print('FLEET SUMMARY:')
    for k, v in agents.items():
        if v:
            print('  {}: {} sessions  -> {}'.format(k, len(v), ', '.join(v)))
    active = sum(len(v) for k, v in agents.items() if k not in ['EMPTY', 'OTHER'])
    print('  TOTAL ACTIVE AGENTS: {}'.format(active))

if __name__ == '__main__':
    run_audit()
