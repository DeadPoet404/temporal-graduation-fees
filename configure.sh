#!/usr/bin/env bash
# Prompts for the Telegram bot token and chat id, then writes config.json.
# Usage:  ./configure.sh
set -euo pipefail
cd "$(dirname "$0")"

read -rsp "Bot token (from @BotFather, input hidden): " TOKEN; echo
if [ -z "$TOKEN" ]; then echo "Empty token - config.json left untouched."; exit 1; fi
read -rp "Allowed chat id (press Enter to allow ANY chat): " CHAT

python3 - "$TOKEN" "$CHAT" <<'PY'
import json, sys, pathlib
token, chat = sys.argv[1], sys.argv[2].strip()
p = pathlib.Path("config.json")
cfg = {}
if p.exists():
    try: cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception: cfg = {}
cfg["bot_token"] = token
cfg["chat_id"] = chat
p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
print("config.json written:", p.resolve())
print("chat restriction:", chat or "NONE (any chat can query)")
PY
