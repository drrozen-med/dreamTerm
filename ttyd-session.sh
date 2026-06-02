#!/bin/bash
# Usage: ttyd-session.sh <port> <session-name>
# Starts ttyd connected to a tmux session.
# Key fix: use 'tmux attach-session -d' to detach all other clients first,
# giving ttyd exclusive input control.
PORT=$1
SESSION=$2

exec /usr/local/bin/ttyd -W --port $PORT \
  -t fontSize=14 \
  -t fontFamily="JetBrains Mono, monospace" \
  -t rendererType=webgl \
  tmux attach-session -d -t "$SESSION"
