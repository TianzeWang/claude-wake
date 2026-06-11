#!/usr/bin/env bash
# Claude Wake -- native launcher for Linux / macOS.
# Starts the backend, opens the dashboard (preferring app-mode), and reminds you
# to run Claude inside tmux.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PORT=$(python3 -c "import json; print(json.load(open('config.json')).get('port',8770))" 2>/dev/null || echo 8770)
URL="http://localhost:$PORT"

echo "Starting Claude Wake backend on $URL ..."
python3 app.py &
sleep 1

open_ui() {
  for cmd in google-chrome chromium chromium-browser; do
    if command -v "$cmd" >/dev/null 2>&1; then
      "$cmd" --app="$URL" >/dev/null 2>&1 &
      return 0
    fi
  done
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 & return 0; fi
  if command -v open    >/dev/null 2>&1; then open "$URL"    >/dev/null 2>&1 & return 0; fi
  echo "Could not auto-open a browser. Open $URL manually."
}
open_ui

echo
echo "Run Claude inside tmux:  tmux new -A -s claude-work claude"
echo "(or use ./start_claude.sh which reads your config.json)"
echo "Press Ctrl-C here to stop the backend."
wait
