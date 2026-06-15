# Mukhlis Telegram Bridge

A production-grade Telegram ↔ Claude Code headless bridge with:

- Per-message **model autorouting** (haiku / sonnet / opus by message complexity)
- **Context usage gauge** shown in every reply footer
- `/compact` to force Claude context compaction
- `/loop on|off|<minutes>` proactive heartbeat mode (AI self-directs work when idle)
- `/model auto|haiku|sonnet|opus` to pin model
- Single-instance lock (TCP port), transcript JSONL logging, state persistence
- BRIDGE_TIER env: `readonly` (read/web only) or `act` (full file/shell/MCP access)

Built by ABUZ8 LLC. Powers the Mukhlis always-on mobile agent.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `TELEGRAM_BOT_TOKEN` — from @BotFather
   - `ALLOWED_TELEGRAM_CHAT_ID` — your Telegram user ID (get it from @userinfobot)
2. Set `BRIDGE_TIER=act` for full hands, `BRIDGE_TIER=readonly` for read-only.
3. Run `START_BRIDGE_ACT_TIER.bat` (Windows) or adapt for your platform.

## Commands

| Command | Description |
|---------|-------------|
| `/ping` | Check if bridge is alive |
| `/status` | Session and offset info |
| `/usage` | Show context window usage |
| `/compact` | Force Claude to compact context |
| `/model auto` | Autoroute by message (default) |
| `/model opus` | Pin to Claude Opus |
| `/loop on` | Arm proactive idle loop |
| `/loop 30` | Set loop interval (minutes) |
| `/new` | Start fresh conversation |

## License

MIT — ABUZ8 LLC
