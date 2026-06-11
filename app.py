#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Wake -- auto-resume Claude Code after the 5-hour usage limit resets.

What it does:
  When your interactive Claude Code session hits the 5-hour usage limit, this tool
    1) reads the reset time straight out of the session transcript,
    2) sleeps precisely until that moment,
    3) injects "continue" into your live tmux session so Claude picks up where it
       left off,
  and stops (with a notification) when it reaches a stop boundary: your chosen
  cutoff time, an ALL_DONE marker in Claude's reply, or a max-rounds limit.

Design notes:
  - Pure Python standard library, no pip installs required.
  - Injects into the live session via `tmux send-keys`; one session, no conflicts.
  - A tiny local web dashboard for control (start/stop/countdown/status).

Just run:  python3 app.py   then open http://localhost:<port> in a browser.
Usually launched in one click by start.bat / start.sh in the same folder.
"""

import datetime as _dt
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "1.0.0"

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DASHBOARD_PATH = os.path.join(HERE, "dashboard.html")
LOG_DIR = os.path.join(HERE, "logs")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "port": 8770,
    "tmux_session": "claude-work",     # name of the tmux session running interactive Claude
    "work_dir": "",                    # Claude's working dir (used to locate the transcript); empty = scan all projects, newest wins
    "claude_launch_args": "",          # extra args appended to `claude` when launched by start_claude.sh
    "continue_text": "Continue the unfinished work. If everything is already done, output a single line at the end of your reply: ALL_DONE",
    "done_marker": "ALL_DONE",
    "poll_sec": 30,                    # polling interval
    "buffer_sec": 60,                  # extra seconds to wait past the reset moment (safety margin)
    "telegram_bot_token": "",          # set both to enable Telegram notifications
    "telegram_chat_id": "",
    "default_until": "08:00",          # default value of the dashboard "stop at" control
    "default_max_rounds": 0,           # 0 = unlimited rounds
    "lang": "en",                      # notification language: "en" or "zh"
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[warn] failed to read config.json, using defaults: {e}")
    else:
        # First run: materialize a default config so a fresh clone works zero-config.
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            print("[info] no config.json found; wrote default config.json")
        except Exception as e:
            print(f"[warn] failed to write default config.json: {e}")
    return cfg


# ---------------------------------------------------------------------------
# Internationalized notification strings
# ---------------------------------------------------------------------------
STRINGS = {
    "en": {
        "notify_stopped_title": "Claude Wake stopped",
        "notify_stopped_body": "{reason} (resumed {rounds} round(s))",
        "test_notify_title": "Claude Wake test notification",
        "test_notify_body": "If you can see this, the notification path works.",
        "reason_until": "Reached stop time {t}",
        "reason_rounds": "Resumed {n} round(s), limit reached",
        "reason_done": "Detected done marker {m}, work complete",
        "reason_manual": "Stopped manually",
    },
    "zh": {
        "notify_stopped_title": "Claude Wake 已停止",
        "notify_stopped_body": "{reason}（共续 {rounds} 轮）",
        "test_notify_title": "Claude Wake 测试通知",
        "test_notify_body": "如果你看到这条，说明通知通路正常。",
        "reason_until": "到达停点 {t}",
        "reason_rounds": "已续够 {n} 轮",
        "reason_done": "检测到完成标记 {m}，工作完成",
        "reason_manual": "手动停止",
    },
}


def L(cfg, key, **kw):
    """Look up a localized string by cfg['lang'], falling back to English."""
    lang = (cfg.get("lang") or "en").lower()
    table = STRINGS.get(lang, STRINGS["en"])
    s = table.get(key) or STRINGS["en"].get(key, key)
    try:
        return s.format(**kw)
    except Exception:
        return s


# ---------------------------------------------------------------------------
# Transcript location and parsing
# ---------------------------------------------------------------------------
def encode_project_dirname(work_dir):
    """Encode a working dir into its ~/.claude/projects dir name: each non-alphanumeric char -> '-'."""
    return re.sub(r"[^a-zA-Z0-9]", "-", work_dir)


def projects_root():
    return os.path.expanduser("~/.claude/projects")


def candidate_transcript_dirs(work_dir):
    """Return likely transcript dirs (the work_dir's own dir first, then every project dir)."""
    root = projects_root()
    dirs = []
    if work_dir:
        d = os.path.join(root, encode_project_dirname(work_dir))
        if os.path.isdir(d):
            dirs.append(d)
    # Fallback: every project dir (used when work_dir is unset or does not match)
    if os.path.isdir(root):
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(p) and p not in dirs:
                dirs.append(p)
    return dirs


def newest_transcript(work_dir):
    """Active transcript = the newest-mtime .jsonl among the candidate dirs."""
    newest, newest_mtime = None, -1.0
    for d in candidate_transcript_dirs(work_dir):
        try:
            for name in os.listdir(d):
                if not name.endswith(".jsonl"):
                    continue
                p = os.path.join(d, name)
                m = os.path.getmtime(p)
                if m > newest_mtime:
                    newest, newest_mtime = p, m
        except Exception:
            continue
        # If work_dir hit its own dedicated dir, take the newest only from there (more precise), not mixing other projects
        if work_dir and d == os.path.join(projects_root(), encode_project_dirname(work_dir)):
            if newest is not None:
                return newest
    return newest


def tail_text(path, nbytes=12000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


_EPOCH_RE = re.compile(r"usage limit reached\|(\d{9,})")
_HUMAN_RE = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.IGNORECASE)
_LIMIT_HINT_RE = re.compile(r"usage limit reached|5-?hour limit|limit reached", re.IGNORECASE)


def parse_limit_epoch(text):
    """Preferred: read the exact reset time from 'usage limit reached|<unix seconds>'. Returns int or None."""
    matches = _EPOCH_RE.findall(text or "")
    if matches:
        return int(matches[-1])
    return None


def parse_human_reset(text, now=None):
    """Fallback: turn 'resets 3pm' / 'resets 1:40am' into the nearest today/tomorrow reset epoch."""
    if now is None:
        now = _dt.datetime.now()
    m = None
    for m in _HUMAN_RE.finditer(text or ""):
        pass  # keep the last match
    if not m:
        return None
    hour = int(m.group(1)) % 12
    minute = int(m.group(2)) if m.group(2) else 0
    if m.group(3).lower() == "pm":
        hour += 12
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand <= now:
        cand += _dt.timedelta(days=1)
    return int(cand.timestamp())


def assistant_last_text(tail):
    """Return the plain text of the last assistant message at the tail of the transcript.
    The done marker is only checked inside assistant replies, so that an 'ALL_DONE' echoed
    back inside the continue instruction (a user message) cannot trigger a false stop."""
    last = ""
    for line in (tail or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue  # the first tail line may be truncated; just skip it
        if rec.get("type") != "assistant":
            continue
        content = rec.get("message", {}).get("content", [])
        text = ""
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    text += c.get("text", "")
        elif isinstance(content, str):
            text = content
        if text.strip():
            last = text
    return last


def limit_is_current(combined_text, pane_text):
    """Decide whether we are 'currently stuck at the usage limit', avoiding false triggers from past hits.
    Strongest signal: the live screen (capture-pane) is showing the limit message right now.
    Next strongest: the last limit hint in the transcript tail has no newer user message after it."""
    if pane_text and _LIMIT_HINT_RE.search(pane_text):
        return True
    t = combined_text or ""
    last_limit = -1
    for mt in _LIMIT_HINT_RE.finditer(t):
        last_limit = mt.start()
    if last_limit < 0:
        return False
    # If a new user message appears after the limit (the human kept typing), we are no longer stuck
    last_user = t.rfind('"role":"user"')
    return last_limit > last_user


# ---------------------------------------------------------------------------
# tmux interaction
# ---------------------------------------------------------------------------
def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        class _R:  # fabricate a failed result
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _R()


def tmux_session_exists(name):
    r = _run(["tmux", "has-session", "-t", name])
    return r.returncode == 0


def tmux_capture(name):
    r = _run(["tmux", "capture-pane", "-p", "-t", name])
    return r.stdout if r.returncode == 0 else ""


def send_continue(name, text):
    """Type the continue text into the tmux session and press Enter (text and Enter sent
    as two separate send-keys to avoid escaping pitfalls)."""
    if not tmux_session_exists(name):
        return False, f"tmux session '{name}' not found"
    r1 = _run(["tmux", "send-keys", "-t", name, "--", text])
    time.sleep(0.3)
    r2 = _run(["tmux", "send-keys", "-t", name, "Enter"])
    ok = r1.returncode == 0 and r2.returncode == 0
    return ok, ("" if ok else (r1.stderr or r2.stderr or "send-keys failed"))


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def notify(cfg, title, msg):
    full = f"{title}\n{msg}"
    # 1) Telegram
    token = cfg.get("telegram_bot_token", "").strip()
    chat = cfg.get("telegram_chat_id", "").strip()
    if token and chat:
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": full}).encode()
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
            return
        except Exception as e:
            print(f"[warn] Telegram notification failed: {e}")
    # 2) Linux desktop notification
    if _run(["which", "notify-send"]).returncode == 0:
        _run(["notify-send", title, msg])
        return
    # 3) macOS notification (osascript)
    if _run(["which", "osascript"]).returncode == 0:
        safe_msg = msg.replace('"', "'")
        safe_title = title.replace('"', "'")
        _run(["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}"'])
        return
    # 4) Windows toast (via powershell.exe)
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType=WindowsRuntime] | Out-Null; "
        "Write-Host '" + full.replace("'", " ") + "'"
    )
    _run(["powershell.exe", "-NoProfile", "-Command", ps])


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------
class Watcher:
    def __init__(self, cfg):
        self.cfg = cfg
        self._thread = None
        self._stop = threading.Event()      # set => stop requested
        self._wake = threading.Event()      # used for interruptible sleep
        self.lock = threading.Lock()
        self.reset_state()

    def reset_state(self):
        self.state = "stopped"
        self.detail = {"code": "not_started"}
        self.rounds = 0
        self.reset_epoch = 0                 # >0 means waiting for reset; counting down to this epoch
        self.reset_total_sec = 0             # total wait seconds set when entering waiting_reset; 0 otherwise
        self.until = ""
        self.max_rounds = 0
        self.drive = False
        self.do_notify = True
        self.started_at = 0
        self.last_event = ""

    # ---- Public status (for the web UI) ----
    def status(self):
        with self.lock:
            eta = max(0, int(self.reset_epoch - time.time())) if self.reset_epoch else 0
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "state": self.state,
                "detail": self.detail,
                "rounds": self.rounds,
                "reset_eta_sec": eta,
                "reset_total_sec": self.reset_total_sec,
                "until": self.until,
                "max_rounds": self.max_rounds,
                "drive": self.drive,
                "last_event": self.last_event,
                "tmux_session": self.cfg.get("tmux_session", ""),
                "tmux_alive": tmux_session_exists(self.cfg.get("tmux_session", "")),
                "work_dir": self.cfg.get("work_dir", ""),
            }

    def _set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _log(self, msg):
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        with self.lock:
            self.last_event = f"{_dt.datetime.now().strftime('%H:%M:%S')} {msg}"
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            day = _dt.datetime.now().strftime("%Y%m%d")
            with open(os.path.join(LOG_DIR, f"run-{day}.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    # ---- Interruptible sleep ----
    def _sleep(self, seconds):
        """Sleep `seconds`, but wake immediately if stop is requested. Returns False if interrupted."""
        self._wake.clear()
        woke = self._wake.wait(timeout=max(0, seconds))
        return not woke  # not woken = slept the full duration

    # ---- Time boundary ----
    def _until_epoch(self):
        u = (self.until or "").strip()
        if not u:
            return 0
        m = re.match(r"^(\d{1,2}):(\d{2})$", u)
        if not m:
            return 0
        now = _dt.datetime.now()
        cand = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if cand <= now:
            cand += _dt.timedelta(days=1)
        return int(cand.timestamp())

    # ---- Main loop ----
    def _loop(self):
        cfg = self.cfg
        sess = cfg.get("tmux_session", "claude-work")
        work_dir = cfg.get("work_dir", "")
        poll = int(cfg.get("poll_sec", 30))
        buffer_sec = int(cfg.get("buffer_sec", 60))
        cont_text = cfg.get("continue_text", "continue")
        done_marker = cfg.get("done_marker", "ALL_DONE")
        until_epoch = self._until_epoch()

        self._set(state="running", detail={"code": "watching"})
        self._log(f"watchdog started | tmux={sess} until={self.until or 'none'} "
                  f"max_rounds={self.max_rounds or 'unlimited'} drive={self.drive}")

        def stop_with(detail, reason_text):
            self._set(state="stopped", detail=detail, reset_epoch=0, reset_total_sec=0)
            self._log(f"stopped: {reason_text}")
            if self.do_notify:
                notify(cfg, L(cfg, "notify_stopped_title"),
                       L(cfg, "notify_stopped_body", reason=reason_text, rounds=self.rounds))

        while not self._stop.is_set():
            # -- boundary checks --
            if until_epoch and time.time() >= until_epoch:
                stop_with({"code": "stopped_until", "t": self.until},
                          L(cfg, "reason_until", t=self.until))
                return
            if self.max_rounds and self.rounds >= self.max_rounds:
                stop_with({"code": "stopped_rounds", "n": self.max_rounds},
                          L(cfg, "reason_rounds", n=self.max_rounds))
                return

            tx = newest_transcript(work_dir)
            tail = tail_text(tx) if tx else ""
            pane = tmux_capture(sess)
            combined = tail + "\n" + pane

            # -- (1) done marker --
            # Only checked in Claude's most recent assistant reply, to exclude an ALL_DONE echoed in the instruction (user msg)
            if done_marker and done_marker in assistant_last_text(tail):
                stop_with({"code": "stopped_done", "m": done_marker},
                          L(cfg, "reason_done", m=done_marker))
                return

            # -- (2) hit the limit? parse the reset time --
            epoch = parse_limit_epoch(combined)
            if epoch is None:
                epoch = parse_human_reset(combined)
            if epoch and limit_is_current(tail, pane):
                wait = epoch - int(time.time()) + buffer_sec
                if wait < buffer_sec:
                    wait = buffer_sec
                eta_str = _dt.datetime.fromtimestamp(epoch).strftime("%m-%d %H:%M")
                self._set(state="waiting_reset", detail={"code": "reset_at", "t": eta_str},
                          reset_epoch=epoch + buffer_sec, reset_total_sec=wait)
                self._log(f"usage limit reached, reset at {eta_str}, waiting {wait}s")
                if not self._sleep(wait):
                    self._set(reset_epoch=0, reset_total_sec=0)
                    break  # interrupted by stop
                if self._stop.is_set():
                    break
                ok, err = send_continue(sess, cont_text)
                self._set(reset_epoch=0, reset_total_sec=0)
                if ok:
                    self.rounds += 1
                    self._set(state="running", detail={"code": "continue_sent"})
                    self._log(f"sent 'continue' (round {self.rounds})")
                else:
                    self._set(state="running", detail={"code": "continue_failed", "err": err})
                    self._log(f"failed to send 'continue': {err}")
                # brief wait after sending so we do not immediately re-read the same limit record
                self._sleep(poll)
                continue

            # -- (3) drive mode: idle and not at the limit -> nudge with continue to push more rounds --
            if self.drive and self._looks_idle(pane):
                ok, err = send_continue(sess, cont_text)
                if ok:
                    self.rounds += 1
                    self._log(f"drive continue (round {self.rounds})")
                else:
                    self._log(f"drive send-continue failed: {err}")

            self._sleep(poll)

        # loop exited due to stop
        if self.state != "stopped":
            self._set(state="stopped", detail={"code": "stopped_manual"}, reset_epoch=0, reset_total_sec=0)
            self._log("stopped manually")
            if self.do_notify:
                notify(cfg, L(cfg, "notify_stopped_title"),
                       L(cfg, "notify_stopped_body", reason=L(cfg, "reason_manual"), rounds=self.rounds))

    def _looks_idle(self, pane):
        """Used by drive mode: if the screen shows no Claude work indicator (Braille spinner U+2800-U+28FF), treat as idle."""
        if not pane:
            return False
        if any("⠀" <= ch <= "⣿" for ch in pane):
            return False
        return True

    # ---- Control ----
    def start(self, until, max_rounds, drive, do_notify):
        if self._thread and self._thread.is_alive():
            return False, "already running"
        self._stop.clear()
        self._wake.clear()
        with self.lock:
            self.rounds = 0
            self.reset_epoch = 0
            self.reset_total_sec = 0
            self.until = until or ""
            self.max_rounds = int(max_rounds or 0)
            self.drive = bool(drive)
            self.do_notify = bool(do_notify)
            self.started_at = int(time.time())
            self.last_event = ""
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True, "started"

    def stop(self):
        self._stop.set()
        self._wake.set()
        return True, "stop requested"


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
WATCHER = None
CFG = None

# Fields a client may set via POST /config.
CONFIG_WHITELIST = {
    "tmux_session", "work_dir", "claude_launch_args", "continue_text", "done_marker",
    "poll_sec", "buffer_sec", "telegram_bot_token", "telegram_chat_id",
    "default_until", "default_max_rounds", "lang", "port",
}
CONFIG_INT_FIELDS = {"poll_sec", "buffer_sec", "default_max_rounds", "port"}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # silence the default access log

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html", "/dashboard.html"):
            try:
                with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
                    html = f.read()
            except Exception as e:
                html = f"<h1>failed to read dashboard.html: {e}</h1>"
            self._send(200, html, "text/html; charset=utf-8")
        elif path == "/status":
            self._send(200, json.dumps(WATCHER.status(), ensure_ascii=False))
        elif path == "/config":
            safe = dict(CFG)
            safe["telegram_bot_token"] = "***" if safe.get("telegram_bot_token") else ""
            safe["version"] = __version__
            self._send(200, json.dumps(safe, ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        params = {}
        if raw:
            try:
                params = json.loads(raw)
            except Exception:
                params = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

        if path == "/start":
            ok, msg = WATCHER.start(
                until=str(params.get("until", "")).strip(),
                max_rounds=params.get("max_rounds", 0),
                drive=params.get("drive", False) in (True, "true", "1", "on", 1),
                do_notify=params.get("notify", True) not in (False, "false", "0", "off", 0),
            )
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
        elif path == "/stop":
            ok, msg = WATCHER.stop()
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
        elif path == "/config":
            self._handle_config_post(params)
        elif path == "/test-continue":
            # debug: immediately inject the continue text once, to verify the injection path works
            ok, err = send_continue(CFG.get("tmux_session", ""), CFG.get("continue_text", "continue"))
            self._send(200, json.dumps({"ok": ok, "msg": err or "sent"}, ensure_ascii=False))
        elif path == "/test-notify":
            notify(CFG, L(CFG, "test_notify_title"), L(CFG, "test_notify_body"))
            self._send(200, json.dumps({"ok": True, "msg": "test notification sent"}, ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _handle_config_post(self, params):
        if not isinstance(params, dict):
            self._send(400, json.dumps({"ok": False, "msg": "expected a JSON object"}))
            return
        old_port = int(CFG.get("port", 8770))
        for key in CONFIG_WHITELIST:
            if key not in params:
                continue
            val = params[key]
            # token == "***" means keep the existing value (the UI masks it)
            if key == "telegram_bot_token" and val == "***":
                continue
            if key in CONFIG_INT_FIELDS:
                try:
                    val = int(val)
                except Exception:
                    continue
            CFG[key] = val  # mutate CFG in place so the Watcher (which shares the ref) sees it
        try:
            save_config(CFG)
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "msg": f"failed to write config.json: {e}"}))
            return
        restart_required = int(CFG.get("port", 8770)) != old_port
        self._send(200, json.dumps({"ok": True, "restart_required": restart_required}, ensure_ascii=False))


def main():
    global WATCHER, CFG
    CFG = load_config()
    WATCHER = Watcher(CFG)
    os.makedirs(LOG_DIR, exist_ok=True)
    port = int(CFG.get("port", 8770))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("=" * 56)
    print(f"  Claude Wake v{__version__} -- dashboard is up")
    print(f"  Open in your browser:  http://localhost:{port}")
    print(f"  Watching tmux session: {CFG.get('tmux_session')}")
    print(f"  Working directory:     {CFG.get('work_dir') or '(scanning all projects)'}")
    print("  Close this window or press Ctrl-C to stop the backend.")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBackend stopped.")


if __name__ == "__main__":
    main()
