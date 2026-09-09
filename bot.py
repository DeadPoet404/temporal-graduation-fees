#!/usr/bin/env python3
"""
JOCOMFY SCHOOL - Graduation Fee Telegram Bot
=============================================
Answers instantly whether a student owes the graduation fee (GHS 50),
looked up by index number (reg #) from the pen-written register.

Usage:
    python3 bot.py                 # run the bot (long polling)
    python3 bot.py --test JCS-0212 # print the reply for an index without Telegram

Configuration (config.json next to this file, or environment variables):
    { "bot_token": "123456:ABC...", "chat_id": "123456789" }
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID

No third-party dependencies - Python 3.8+ standard library only.
"""

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / "data" / "students.json"
CONFIG_FILE = BASE / "config.json"
API = "https://api.telegram.org/bot{token}/{method}"

log = logging.getLogger("grad-bot")

HELP_TEXT = (
    "JOCOMFY SCHOOL - graduation fee checker\n"
    "----------------------------------------\n"
    "Send me a student index number and I will tell you immediately "
    "whether the student owes the graduation fee (GHS 50).\n"
    "Accepted formats: JCS-0212, jcs 0212, 0212, 212\n\n"
    "Commands:\n"
    "/start or /help - this message\n"
    "/stats - summary of the register\n"
    "anything else - treated as an index number"
)


# ---------------------------------------------------------------- config/data
def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.error("bad config.json (%s) - ignoring file", exc)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or str(cfg.get("bot_token", "")).strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID") or str(cfg.get("chat_id", "")).strip()
    return token, chat


def load_students():
    blob = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return blob["meta"], blob["students"]


def norm(reg):
    """JCS-0212 / jcs 0212 / JSC-0212 -> JCS0212"""
    n = re.sub(r"[^A-Z0-9]", "", (reg or "").upper())
    n = re.sub(r"^(JSC|JCS|CS|SC|JC|J)", "JCS", n) if re.match(r"^(JSC|CS|SC|JC|J)\d", n) else n
    return n


def digits(reg):
    return re.sub(r"\D", "", reg or "")


def build_index(students):
    idx = {}
    for s in students:
        keys = {norm(s["reg"]), "JCS" + digits(s["reg"]), digits(s["reg"])}
        for k in keys:
            if k:
                idx.setdefault(k, []).append(s)
    return idx


def lookup(query, students, idx):
    q = norm(query)
    if q in idx:
        hits = idx[q]
        return hits[0] if len(hits) == 1 else {"ambiguous": hits}
    qd = digits(query)
    if qd:
        hits = [s for s in students if digits(s["reg"]).lstrip("0") == qd.lstrip("0")]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return {"ambiguous": hits}
    return None


# ------------------------------------------------------------------- replies
def fmt_money(v):
    return f"GHS {v:,}"


def reply_for(student):
    if student.get("ambiguous"):
        lines = ["More than one student matches that number:"]
        lines += [f"  - {s['reg']}  {s['name']} ({s['class']})" for s in student["ambiguous"]]
        lines.append("Please send the full index (e.g. JCS-0212).")
        return "\n".join(lines)

    head = f"{student['reg']} - {student['name']}\nClass: {student['class']}"
    if student.get("status") in ("stopped", "struck"):
        return (f"{head}\n"
                f"Status: STRUCK OFF / STOPPED on the register "
                f"({student.get('status_reason', '')}).\n"
                f"Not on the active fee list - graduation fee does not apply.")
    pen = student.get("pen")
    if pen is None:
        return (f"{head}\n"
                f"Pen balance: none recorded on the register.\n"
                f"Graduation fee: NO outstanding balance recorded - "
                f"nothing owed per the pen register.")
    pens = pen if isinstance(pen, list) else [pen]
    lines = [head, "Pen balance: " + " / ".join(fmt_money(p) for p in pens)]
    lines.append(student["detail"])
    if student["owes"]:
        lines.append(">> OWES GRADUATION FEE (GHS 50) <<")
    else:
        lines.append(">> Graduation fee PAID / not owed <<")
    if student.get("note"):
        lines.append("Note: " + student["note"])
    return "\n".join(lines)


def stats_text(meta, students):
    active = [s for s in students if s.get("status") == "active" and s.get("pen") is not None]
    owers = [s for s in active if s["owes"]]
    total = sum(max(s["pen"] if isinstance(s["pen"], list) else [s["pen"]]) for s in owers)
    return (
        f"Register summary ({meta.get('generated', '?')})\n"
        f"Indexed students: {len(students)}\n"
        f"With a pen balance: {len(active)}\n"
        f"Owe graduation fee: {len(owers)}\n"
        f"Their pen balances total: {fmt_money(total)}\n"
        f"Graduation fee: GHS {meta.get('graduation_fee', 50)} on top of the class range."
    )


# ----------------------------------------------------------------- telegram
def api(token, method, **params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(API.format(token=token, method=method), data=data)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send(token, chat_id, text):
    try:
        api(token, "sendMessage", chat_id=chat_id, text=text)
    except Exception as exc:  # noqa: BLE001
        log.error("sendMessage failed: %s", exc)


def poll(token, chat_id, meta, students):
    idx = build_index(students)
    offset = 0
    log.info("polling for updates...")
    while True:
        try:
            res = api(token, "getUpdates", offset=offset, timeout=25,
                      allowed_updates='["message"]')
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("network hiccup (%s), retrying in 5s", exc)
            time.sleep(5)
            continue
        for up in res.get("result", []):
            offset = up["update_id"] + 1
            msg = up.get("message") or {}
            chat = msg.get("chat", {})
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            if chat_id and str(chat.get("id")) != chat_id:
                log.info("ignore chat %s (not authorised)", chat.get("id"))
                continue
            cid = chat.get("id")
            if text in ("/start", "/help"):
                send(token, cid, HELP_TEXT)
            elif text == "/stats":
                send(token, cid, stats_text(meta, students))
            else:
                hit = lookup(text, students, idx)
                if hit is None:
                    send(token, cid,
                         f"No student found for \u201c{text}\u201d.\n"
                         f"Send the index like JCS-0212 (or /help).")
                else:
                    send(token, cid, reply_for(hit))
        time.sleep(0.2)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        meta, students = load_students()
        q = " ".join(sys.argv[2:])
        hit = lookup(q, students, build_index(students))
        print(reply_for(hit) if hit else f"No student found for \u201c{q}\u201d.")
        return
    token, chat_id = load_config()
    if not token:
        sys.exit("No bot token yet.\n"
                 "  1. cp config.example.json config.json\n"
                 "  2. paste the token from @BotFather into \"bot_token\"\n"
                 "  3. optionally set \"chat_id\" to restrict who may query\n"
                 "  4. python3 bot.py")
    meta, students = load_students()
    log.info("loaded %d students; chat restriction: %s",
             len(students), chat_id or "NONE (any chat)")
    try:
        me = api(token, "getMe")["result"]
        log.info("connected as @%s", me.get("username"))
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Telegram rejected the token: {exc}")
    while True:
        try:
            poll(token, chat_id, meta, students)
        except KeyboardInterrupt:
            log.info("bye")
            return
        except Exception as exc:  # noqa: BLE001
            log.error("poll crashed (%s), restarting in 5s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
