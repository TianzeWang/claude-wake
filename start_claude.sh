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
# Strip any Claude Code session env we may have inherited (when launched from
# inside another Claude session, or when the tmux server itself was first started
# from one). Otherwise the new claude is treated as a nested/child session and its
# usage isn't logged as a normal top-level session, so tools like ccusage can't
# see it. `env -u` silently ignores vars that aren't set, so this is a no-op when
# the environment is already clean.
CLEAN="env -u CLAUDECODE -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH"
# -A: attach if it exists, otherwise create; the command is handed to tmux as one string (run via sh -c)
exec tmux new -A -s "$SESS" "$CLEAN claude $ARGS"
