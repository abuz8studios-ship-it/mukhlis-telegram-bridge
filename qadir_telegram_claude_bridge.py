#!/usr/bin/env python3
"""Two-way Telegram <-> Claude (Qadir) bridge for @QadirCoreM1_bot.

WHY THIS EXISTS (2026-06-11): the old daemons/telegram_video_poller.py routed
the owner's messages to the Q3 backend brain on :8900 (down), so the bot was
receive-dead. the owner's requirement: "I need to be able to speak to YOU (Claude/
Qadir) directly through it." This bridge is now the SINGLE getUpdates owner
for @QadirCoreM1_bot (see E:\\ABU\\API_KEYS\\TELEGRAM_BOTS.md).

Flow:
  the owner (TG: set ALLOWED_TELEGRAM_CHAT_ID env) -> getUpdates long-poll -> claude.cmd -p (headless,
  Qadir identity via E:\\ABU\\CLAUDE.md + IDENTITY.md hook, session continuity
  via --resume) -> sendMessage back to the owner.

Design notes:
  - stdlib only (urllib, subprocess) - zero pip deps, survives env rot.
  - single-instance guard = TCP bind on 127.0.0.1:48931 (no lockfile staleness).
  - allowlist: ONLY the ALLOWED_TELEGRAM_CHAT_ID is processed; everything else logged+dropped.
  - permission tier v2 (2026-06-11): ACTING AGENT via an EXPLICIT tool
    allowlist (ALLOWED_TOOLS), per the owner's authorization the same day
    ("full blown bridge ... my mobile agent"). Deliberately NOT a blanket
    --dangerously-skip-permissions: a remotely-triggered no-gate agent loop
    is exactly what the harness safety layer refuses (it can't verify a
    Telegram message is really the owner). An explicit allowlist is the sanctioned
    middle: the agent can read/write/edit/run-shell + use the qadir toolset,
    but the capability set is declared, not infinite. Rails live in
    PROMPT_HEADER: anti-fraud covenant, no money movement / outbound email /
    irreversible deletes without explicit in-chat confirmation.
  - bridge sessions load the qadir MCP server (mcp_qadir.json): L3 memory,
    telegram, stripe, comfyui - the full Qadir toolset, reachable from phone.
  - every exchange is persisted to transcript.jsonl (Qadir Core owns the
    conversation history; nothing is lost between sessions).
  - bridge-level commands (no Claude call): /ping /new /status /usage
    /compact (passes the raw slash command to claude to force compaction;
    claude also auto-compacts near the context limit on its own)
  - usage gauge (2026-06-11, the owner's ask): every reply gets a one-line footer
    [ctx Nk/200k (P%) | $X turn | Ys] parsed from claude -p JSON usage, so
    token burn is visible from the phone. Last usage cached in bridge_state.
  - MCP intentionally EMPTY (mcp_empty.json) for fast turn startup; Claude
    still has Bash/file tools = full machine access. To give bridge sessions
    the qadir MCP tools, swap mcp_empty.json for a config listing the server
    and accept slower turns.

Run:  "C:\\Program Files\\Python311\\pythonw.exe" qadir_telegram_claude_bridge.py
Logs: bridge.log (same dir). State: bridge_state.json (offset + session_id).
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BRIDGE_DIR, "bridge_state.json")
LOG_FILE = os.path.join(BRIDGE_DIR, "bridge.log")
MCP_EMPTY = os.path.join(BRIDGE_DIR, "mcp_empty.json")  # kept for fast/fallback mode
MCP_QADIR = os.path.join(BRIDGE_DIR, "mcp_qadir.json")  # full toolset (active)
TRANSCRIPT_FILE = os.path.join(BRIDGE_DIR, "transcript.jsonl")
TOKEN_FILES = [
    r"E:\ABU\API_KEYS\telegram_qadir.txt",
    r"E:\ABU\API_KEYS\telegram_qadir_core_m1.txt",
]
ALLOWED_CHAT = int(os.getenv("ALLOWED_TELEGRAM_CHAT_ID", "0"))  # set ALLOWED_TELEGRAM_CHAT_ID in your env
CLAUDE_CMD = r"C:\Users\USER\AppData\Roaming\npm\claude.cmd"
CLAUDE_CWD = r"E:\ABU"
CLAUDE_TIMEOUT = 900  # seconds per turn
LOCK_PORT = 48931
STARTED_AT = time.time()

# Two declared capability tiers. The powerful one is opt-in BY THE HUMAN who
# launches the process (set env BRIDGE_TIER=act), never inferred/auto-enabled.
# This is deliberate: a remotely-triggered headless agent with shell+write and
# no per-action gate is exactly what an automated safety layer refuses to let
# another agent stand up on a vague say-so. Making "act" a launch-time switch
# means the authorization is the owner's own explicit action (double-click the .bat
# / run with BRIDGE_TIER=act), not this session quietly arming it.
ALLOWED_TOOLS_READONLY = "Read,Glob,Grep,WebSearch,WebFetch"
ALLOWED_TOOLS_ACT = "Read,Glob,Grep,Write,Edit,Bash,WebSearch,WebFetch,mcp__qadir"
BRIDGE_TIER = os.getenv("BRIDGE_TIER", "readonly").strip().lower()
if BRIDGE_TIER == "act":
    ALLOWED_TOOLS = ALLOWED_TOOLS_ACT
    ACTIVE_MCP = MCP_QADIR
else:
    BRIDGE_TIER = "readonly"
    ALLOWED_TOOLS = ALLOWED_TOOLS_READONLY
    ACTIVE_MCP = MCP_EMPTY

_HEADER_ACT = (
    "[TELEGRAM BRIDGE - ACT TIER] the owner is messaging you live from his phone "
    "via @QadirCoreM1_bot. You are his mobile agent: you can read, write, and "
    "edit files, run shell commands, search the web, and use the qadir MCP "
    "toolset (memory_write/memory_search, telegram, stripe, comfyui). Act, "
    "then report.\n"
    "HARD RAILS (non-negotiable): anti-fraud covenant applies to anything "
    "public; NEVER move money, send outbound email, post publicly, or delete "
    "irreversibly without the owner's explicit confirmation IN THIS CHAT first. "
    "Verify from disk, never from memory. Store durable facts/decisions via "
    "qadir memory_write so Qadir Core keeps the history.\n"
)
_HEADER_READONLY = (
    "[TELEGRAM BRIDGE - READ-ONLY TIER] the owner is messaging you live from his "
    "phone via @QadirCoreM1_bot. You can Read/Glob/Grep the ABU disk and "
    "search the web, but write/edit/shell/MCP are gated off in this tier. If "
    "he asks for an action you can't take, say so plainly and tell him to "
    "relaunch the bridge in ACT tier (BRIDGE_TIER=act / the act .bat).\n"
)
_HEADER_COMMON = (
    "LINE IDENTITY: on this line you are Qadir al-Mukhlis ('Mukhlis', the "
    "devoted one - named by the owner 2026-06-11) - the always-on mobile line of "
    "Qadir and the managing agent of the desktop fleet. Same soul, same brain "
    "(qadir L3 + vault), same rails.\n"
    "Your final reply text is sent back to Telegram verbatim - concise plain "
    "text, no markdown tables/headers.\n"
    "CONTINUITY: this is NOT a fresh isolated session each message. The bridge "
    "resumes the SAME conversation across messages (via --resume on a stored "
    "session id), so you retain context turn-to-turn until the owner sends /new. "
    "Tell him this accurately if he asks.\n\nOwner says:\n"
)
PROMPT_HEADER = (_HEADER_ACT if BRIDGE_TIER == "act" else _HEADER_READONLY) + _HEADER_COMMON

# Proactive heartbeat (Mukhlis loop, 2026-06-11, the owner's ask: "Hermes-style
# proactive action, persistently working, managing its own memory"). Sent RAW
# (no the owner-says header) when the line has been idle past the interval.
# Default OFF - armed by the owner with /loop on. Every tick costs real tokens.
PROACTIVE_PROMPT = (
    "[MUKHLIS PROACTIVE TICK - no message from the owner; you woke on the bridge "
    "heartbeat] You are Qadir al-Mukhlis, the always-on Telegram line of Qadir. "
    "Do ONE useful unit of work this tick, then stop:\n"
    "1. PERCEIVE: check E:\\ABU\\NEXT_SESSION_START_HERE.md + port probe "
    "(8900/8910/18789/11434) + anything you left pending in this conversation.\n"
    "2. ACT on the single highest-leverage item you can finish safely. "
    "HARD RAILS unchanged: no money movement, no outbound email, no public "
    "posts, no irreversible deletes, no risky service restarts - those WAIT "
    "for the owner. Reading, verifying, organizing, drafting, fixing small broken "
    "things, updating the vault/kanban: yes.\n"
    "3. REMEMBER: memory_write any durable outcome/decision so Qadir Core "
    "keeps it.\n"
    "4. REPORT: if the owner would want a ping (something finished, broken, or "
    "needs his call), reply with 2-5 plain-text lines. If there is genuinely "
    "nothing worth his attention, reply with exactly [SILENT] and nothing "
    "else - the bridge suppresses it.\n"
)


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def transcript(direction: str, text: str, session_id: str | None = None) -> None:
    """Persist every exchange to Qadir Core - the conversation history is owned
    by the system, not by any single session. direction: 'owner' | 'qadir'."""
    try:
        with open(TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "dir": direction,
                "text": text,
                "session": session_id,
            }, ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"transcript write failed: {e}")


def acquire_single_instance_lock() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        log("another bridge instance holds the lock - exiting")
        sys.exit(0)


def load_token() -> str:
    env = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if env:
        return env
    for p in TOKEN_FILES:
        try:
            if os.path.exists(p):
                raw = open(p, encoding="utf-8").read().strip()
                if raw and not raw.startswith("#"):
                    return raw.split()[0]
        except OSError:
            continue
    raise SystemExit("[bridge] no telegram token found")


def api(base: str, method: str, params: dict | None = None, timeout: int = 65):
    url = f"{base}/{method}"
    data = None
    headers = {}
    if params is not None:
        data = json.dumps(params).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def send_message(base: str, chat_id: int, text: str) -> None:
    text = (text or "").strip() or "(empty reply)"
    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
    for chunk in chunks:
        try:
            api(base, "sendMessage", {"chat_id": chat_id, "text": chunk}, timeout=30)
        except Exception as e:
            log(f"sendMessage failed: {e}")


def typing_loop(base: str, chat_id: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            api(base, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=15)
        except Exception:
            pass
        stop.wait(7)


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"offset": 0, "session_id": None}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def clean_env() -> dict:
    # strip the parent Claude session's vars so nested headless runs are clean
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("CLAUDE")}


CONTEXT_WINDOW = 200_000  # claude context window, for the usage gauge

# --- Model autorouter (added 2026-06-11 per the owner: don't burn opus on haiku work) ---
# Modes: "auto" routes per message; or pin with /model haiku|sonnet|opus.
MODELS = ("haiku", "sonnet", "opus")
DEFAULT_MODEL_MODE = "auto"
# Heavy work -> opus: building, selling strategy, multi-step missions, code surgery.
_OPUS_RE = re.compile(
    r"\b(build|implement|refactor|architect|migrate|deploy|ship|github|"
    r"strateg\w*|plan\b|campaign|outreach|sell\w*|sales|market\w*|review|"
    r"audit|security|debug|fix\b|broken|crash|design|rewrite|integrat\w*|"
    r"automat\w*|pipeline|restructur\w*|mission|everything|all the)\b", re.I)
# Pure status/chat -> haiku: cheap acknowledgement-level turns.
_HAIKU_RE = re.compile(
    r"^(hi|hey|yo|salam|ok|okay|thanks|thank you|good|nice|lol|"
    r"(are )?you (there|alive|up)\??|status\??|ping)\W*$", re.I)


def route_model(text: str, mode: str) -> str:
    if mode in MODELS:
        return mode
    if _HAIKU_RE.match(text.strip()) and len(text) < 60:
        return "haiku"
    if _OPUS_RE.search(text) or len(text) > 400:
        return "opus"
    return "sonnet"


def usage_footer(payload: dict, model: str | None = None) -> tuple[str, dict]:
    """Build a one-line usage gauge from a claude -p JSON payload.
    Context size ~= cached prefix + fresh input of the LAST request this turn."""
    u = payload.get("usage") or {}
    ctx = (u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0)
           + u.get("input_tokens", 0))
    cost = payload.get("total_cost_usd")
    dur = payload.get("duration_ms")
    stats = {"ctx_tokens": ctx, "cost_usd": cost,
             "pct": round(100 * ctx / CONTEXT_WINDOW)}
    parts = []
    if model:
        parts.append(model)
        stats["model"] = model
    if ctx:
        parts.append(f"ctx {ctx // 1000}k/{CONTEXT_WINDOW // 1000}k ({stats['pct']}%)")
    if cost is not None:
        parts.append(f"${cost:.2f} turn")
    if dur:
        parts.append(f"{round(dur / 1000)}s")
    return ((" | ".join(parts)) if parts else "", stats)


def ask_claude(prompt: str, session_id: str | None,
               raw: bool = False, model: str | None = None) -> tuple[str, str | None, dict]:
    # Prompt goes via STDIN, never argv: cmd.exe truncates argv at the first
    # newline, which silently drops every flag after the prompt (seen 2026-06-11).
    # raw=True sends the prompt WITHOUT the bridge header (needed for slash
    # commands like /compact - a prefixed header would break command parsing).
    cmd = ["cmd", "/c", CLAUDE_CMD, "-p",
           "--output-format", "json",
           "--allowedTools", ALLOWED_TOOLS,
           "--strict-mcp-config", "--mcp-config", ACTIVE_MCP]
    if model:
        cmd += ["--model", model]
    if session_id:
        cmd += ["--resume", session_id]
    try:
        r = subprocess.run(cmd, cwd=CLAUDE_CWD, env=clean_env(),
                           input=prompt if raw else PROMPT_HEADER + prompt,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ("(Qadir timed out on this one - try again or split the ask)", session_id, {})
    out = (r.stdout or "").strip()
    # claude.cmd sometimes prefixes warnings before the JSON - cut to first '{'
    brace = out.find("{")
    if brace > 0:
        out = out[brace:]
    if r.returncode != 0 and session_id:
        # stale/missing session - retry fresh once
        log(f"claude rc={r.returncode} with resume; retrying fresh. stderr: {(r.stderr or '')[:300]}")
        return ask_claude(prompt, None, raw=raw, model=model)
    try:
        payload = json.loads(out)
        footer, stats = usage_footer(payload, model)
        return (payload.get("result") or "(no text in reply)",
                payload.get("session_id") or session_id,
                {"footer": footer, **stats})
    except ValueError:
        log(f"non-JSON claude output rc={r.returncode}: {out[:300]} stderr: {(r.stderr or '')[:300]}")
        return (out[:3900] or f"(bridge: claude failed rc={r.returncode})", session_id, {})


def handle_command(base: str, state: dict, text: str) -> bool:
    cmd = text.split()[0].lower() if text else ""
    if cmd == "/ping":
        up = int(time.time() - STARTED_AT)
        send_message(base, ALLOWED_CHAT,
                     f"pong - bridge alive {up // 3600}h{(up % 3600) // 60}m, "
                     f"session={state.get('session_id') or 'fresh'}")
        return True
    if cmd == "/new":
        state["session_id"] = None
        save_state(state)
        send_message(base, ALLOWED_CHAT, "fresh conversation started - context cleared.")
        return True
    if cmd == "/status":
        send_message(base, ALLOWED_CHAT,
                     f"bridge: up since {datetime.fromtimestamp(STARTED_AT).strftime('%H:%M')} | "
                     f"offset={state.get('offset')} | session={state.get('session_id') or 'fresh'} | "
                     f"log={LOG_FILE}")
        return True
    if cmd == "/usage":
        u = state.get("last_usage") or {}
        if u.get("ctx_tokens"):
            send_message(base, ALLOWED_CHAT,
                         f"context: {u['ctx_tokens'] // 1000}k/{CONTEXT_WINDOW // 1000}k "
                         f"({u.get('pct', '?')}%) | last turn ${u.get('cost_usd') or 0:.2f}\n"
                         f"claude auto-compacts near the limit; send /compact to force it now.")
        else:
            send_message(base, ALLOWED_CHAT, "no usage data yet - send any message first.")
        return True
    if cmd == "/loop":
        arg = (text.split()[1].lower() if len(text.split()) > 1 else "status")
        if arg == "on":
            state["loop_on"] = True
            save_state(state)
            send_message(base, ALLOWED_CHAT,
                         f"Mukhlis loop ARMED - proactive tick every "
                         f"{state.get('loop_min', 60)}m of idle. /loop off to stop, "
                         f"/loop <minutes> to retune. Each tick costs a real turn.")
        elif arg == "off":
            state["loop_on"] = False
            save_state(state)
            send_message(base, ALLOWED_CHAT, "Mukhlis loop disarmed - reactive only.")
        elif arg.isdigit() and int(arg) >= 5:
            state["loop_min"] = int(arg)
            state["loop_on"] = True
            save_state(state)
            send_message(base, ALLOWED_CHAT, f"Mukhlis loop ARMED at every {arg}m idle.")
        else:
            send_message(base, ALLOWED_CHAT,
                         f"loop: {'ON' if state.get('loop_on') else 'OFF'} | "
                         f"interval {state.get('loop_min', 60)}m idle | "
                         f"usage: /loop on | /loop off | /loop <minutes, min 5>")
        return True
    if cmd == "/model":
        arg = (text.split()[1].lower() if len(text.split()) > 1 else "status")
        if arg in MODELS or arg == "auto":
            state["model_mode"] = arg
            save_state(state)
            send_message(base, ALLOWED_CHAT,
                         f"model router set to {arg}" +
                         (" - haiku for chat, sonnet for routine, opus for heavy work."
                          if arg == "auto" else f" - every turn runs on {arg}."))
        else:
            send_message(base, ALLOWED_CHAT,
                         f"model: {state.get('model_mode', DEFAULT_MODEL_MODE)} | "
                         f"usage: /model auto | /model haiku | /model sonnet | /model opus")
        return True
    if cmd == "/compact":
        sid = state.get("session_id")
        if not sid:
            send_message(base, ALLOWED_CHAT, "session is fresh - nothing to compact.")
            return True
        send_message(base, ALLOWED_CHAT, "compacting context, give me a minute...")
        reply, new_sid, usage = ask_claude("/compact", sid, raw=True)
        state["session_id"] = new_sid
        if usage.get("ctx_tokens"):
            state["last_usage"] = {k: usage[k] for k in ("ctx_tokens", "cost_usd", "pct") if k in usage}
        save_state(state)
        send_message(base, ALLOWED_CHAT,
                     f"compacted. {usage.get('footer') or reply[:300]}")
        return True
    return False


LAST_TURN_AT = [time.time()]  # mutable so the heartbeat thread sees updates
TURN_LOCK = threading.Lock()  # one claude call at a time (tick vs live message)


def heartbeat_loop(base: str, state: dict) -> None:
    """Mukhlis proactive loop: when armed (/loop on) and the line has been idle
    past the interval, run one self-directed work tick. [SILENT] replies are
    suppressed so the owner is only pinged when it matters."""
    while True:
        time.sleep(60)
        try:
            if BRIDGE_TIER != "act" or not state.get("loop_on"):
                continue
            if time.time() - LAST_TURN_AT[0] < state.get("loop_min", 60) * 60:
                continue
            if not TURN_LOCK.acquire(blocking=False):
                continue  # a live turn is running - skip this minute
            try:
                LAST_TURN_AT[0] = time.time()
                log("mukhlis tick: proactive turn starting")
                # proactive ticks run on sonnet: routine perceive/act work,
                # never worth opus rates on an unattended timer
                reply, sid, usage = ask_claude(PROACTIVE_PROMPT,
                                               state.get("session_id"), raw=True,
                                               model="sonnet")
                state["session_id"] = sid
                if usage.get("ctx_tokens"):
                    state["last_usage"] = {k: usage[k] for k in
                                           ("ctx_tokens", "cost_usd", "pct") if k in usage}
                save_state(state)
                txt = (reply or "").strip()
                if txt and not txt.startswith("[SILENT]"):
                    if usage.get("footer"):
                        txt = f"{txt}\n\n[{usage['footer']}]"
                    send_message(base, ALLOWED_CHAT, "[mukhlis loop] " + txt)
                    transcript("qadir", "[loop] " + txt, sid)
                    log(f"mukhlis tick reported: {txt[:100]}")
                else:
                    transcript("qadir", "[loop-silent]", sid)
                    log("mukhlis tick: silent")
            finally:
                TURN_LOCK.release()
        except Exception as e:
            log(f"mukhlis tick error: {e}")


def main() -> None:
    lock = acquire_single_instance_lock()  # noqa: F841 - held for process lifetime
    token = load_token()
    base = f"https://api.telegram.org/bot{token}"
    me = api(base, "getMe", timeout=20)
    log(f"bridge up as @{me.get('result', {}).get('username')} -> claude headless (cwd={CLAUDE_CWD})")
    log(f"TIER={BRIDGE_TIER} allowed_tools=[{ALLOWED_TOOLS}] mcp={os.path.basename(ACTIVE_MCP)}")
    state = load_state()
    threading.Thread(target=heartbeat_loop, args=(base, state), daemon=True).start()
    log(f"mukhlis loop thread up - armed={bool(state.get('loop_on'))} "
        f"interval={state.get('loop_min', 60)}m")

    while True:
        try:
            r = api(base, "getUpdates",
                    {"offset": state["offset"], "timeout": 50}, timeout=65)
            updates = r.get("result", [])
            if not updates:
                continue
            state["offset"] = updates[-1]["update_id"] + 1

            texts = []
            for upd in updates:
                msg = upd.get("message") or upd.get("edited_message") or {}
                text = (msg.get("text") or msg.get("caption") or "").strip()
                chat_id = (msg.get("chat") or {}).get("id")
                if not text or chat_id is None:
                    continue
                if chat_id != ALLOWED_CHAT:
                    log(f"DROPPED non-allowlisted chat {chat_id}: {text[:80]}")
                    continue
                log(f"<- the owner: {text[:120]}")
                transcript("owner", text, state.get("session_id"))
                if handle_command(base, state, text):
                    continue
                texts.append(text)

            if not texts:
                save_state(state)
                continue

            prompt = "\n".join(texts)
            stop = threading.Event()
            t = threading.Thread(target=typing_loop, args=(base, ALLOWED_CHAT, stop), daemon=True)
            t.start()
            try:
                with TURN_LOCK:
                    model = route_model(prompt, state.get("model_mode", DEFAULT_MODEL_MODE))
                    reply, new_sid, usage = ask_claude(prompt, state.get("session_id"),
                                                       model=model)
                    LAST_TURN_AT[0] = time.time()
            finally:
                stop.set()
            state["session_id"] = new_sid
            if usage.get("ctx_tokens"):
                state["last_usage"] = {k: usage[k] for k in ("ctx_tokens", "cost_usd", "pct") if k in usage}
            save_state(state)
            if usage.get("footer"):
                reply = f"{reply}\n\n[{usage['footer']}]"
            send_message(base, ALLOWED_CHAT, reply)
            transcript("qadir", reply, new_sid)
            log(f"-> the owner: {reply[:120]}")
        except KeyboardInterrupt:
            log("stopped by keyboard")
            break
        except Exception as e:
            log(f"loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
