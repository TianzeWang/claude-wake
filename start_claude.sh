#!/usr/bin/env bash
# Start interactive Claude inside a tmux session (called by start.bat, or run
# manually: `bash start_claude.sh`).
# The session name and working directory are read from config.json in this folder.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
CFG="$DIR/config.json"

SESS=$(python3 -c "import json,sys; print(json.load(open('$CFG')).get('tmux_session','claude-work'))" 2>/dev/null || echo claude-work)
WD=$(python3 -c "import json,sys; print(json.load(open('$CFG')).get('work_dir',''))" 2>/dev/null || echo "")
ARGS=$(python3 -c "import json,sys; print(json.load(open('$CFG')).get('claude_launch_args',''))" 2>/dev/null || echo "")

[ -n "$WD" ] && [ -d "$WD" ] && cd "$WD"

echo "Starting Claude in tmux session [$SESS] (working dir: ${WD:-$PWD})"
[ -n "$ARGS" ] && echo "Launch args: claude $ARGS (if a list pops up, use arrow keys to pick the conversation, then Enter)"
echo "You can close this window after dispatching work; the console will resume it when the quota resets."
# -A: attach if it exists, otherwise create; the command is handed to tmux as one string (run via sh -c)
exec tmux new -A -s "$SESS" "claude $ARGS"
