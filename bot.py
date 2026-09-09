#!/usr/bin/env python3
"""
JOCOMFY SCHOOL - Graduation Fee Telegram Bot
=============================================
Answers instantly whether a student owes the graduation fee (GHS 50),
looked up by index number (reg #) from the pen-written register.

If a student OWES, the reply carries two buttons:
    [ Paid now ]  -> marks the index paid (data/payments.json ledger),
                     removes it from the owing list and edits the message
    [ Cancel   ]  -> dismisses the buttons, changes nothing

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
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / "data" / "students.json"
PAY_FILE = BASE / "data" / "payments.json"
CONFIG_FILE = BASE / "config.json"
API = "https://api.telegram.org/bot{token}/{method}"

log = logging.getLogger("grad-bot")

HELP_TEXT = (
    "JOCOMFY SCHOOL - graduation fee checker\n"
    "----------------------------------------\n"
    "Send me a student index number and I will tell you immediately "
    "whether the student owes the graduation fee (GHS 50).\n"
    "Accepted formats: JCS-0212, jcs 0212, 0212, 212\n\n"
    "If a student owes, two buttons appear under my reply:\n"
    "  Paid now - record the payment and remove the index from owing\n"
    "  Cancel   - dismiss the buttons, change nothing\n\n"
    "Commands:\n"
    "/start or /help - this message\n"
    "/stats   - summary of the register\n"
    "/payments - list of indexes marked paid via this bot\n"
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


def load_payments():
    if PAY_FILE.exists():
        try:
            return json.loads(PAY_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.error("bad payments.json (%s) - starting empty", exc)
    return {}


def save_payments(paid):
    tmp = PAY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(paid, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PAY_FILE)


def apply_paid(student, entry):
    student["owes"] = False
    student["paid_mark"] = True
    student["detail"] = (student.get("detail", "")
                         + f"  |  marked PAID via bot on {entry.get('at', '?')[:10]}")


def norm(reg):
    """JCS-0212 / jcs 0212 / JSC-0212 -> JCS0212"""
    n = re.sub(r"[^A-Z0-9]", "", (reg or "").upper())
    if re.match(r"^(JSC|CS|SC|JC|J)\d", n):
        n = re.sub(r"^(JSC|CS|SC|JC|J)", "JCS", n)
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


def keyboard(reg):
    return {"inline_keyboard": [
        [{"text": "\u2705 Paid now", "callback_data": f"paid:{reg}"},
         {"text": "\u2716 Cancel", "callback_data": f"cancel:{reg}"}]
    ]}


def stats_text(meta, students):
    active = [s for s in students if s.get("status") == "active" and s.get("pen") is not None]
    owers = [s for s in active if s["owes"]]
    total = sum(max(s["pen"] if isinstance(s["pen"], list) else [s["pen"]]) for s in owers)
    marked = sum(1 for s in active if s.get("paid_mark"))
    return (
        f"Register summary ({meta.get('generated', '?')})\n"
        f"Indexed students: {len(students)}\n"
        f"With a pen balance: {len(active)}\n"
        f"Owe graduation fee: {len(owers)}\n"
        f"Their pen balances total: {fmt_money(total)}\n"
        f"Marked paid via bot so far: {marked}\n"
        f"Graduation fee: GHS {meta.get('graduation_fee', 50)} on top of the class range."
    )


def payments_text(paid, students):
    if not paid:
        return "No indexes have been marked paid via the bot yet."
    by_reg = {}
    for s in students:
        by_reg.setdefault(s["reg"], s)
    lines = ["Indexes marked PAID via the bot:"]
    for reg, e in sorted(paid.items(), key=lambda kv: kv[1].get("at", "")):
        nm = by_reg.get(reg, {}).get("name", "?")
        lines.append(f"  {reg}  {nm}  -  {e.get('at', '?')[:16].replace('T', ' ')}")
    return "\n".join(lines)


# ----------------------------------------------------------------- telegram
def api(token, method, **params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(API.format(token=token, method=method), data=data)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send(token, chat_id, text, markup=None):
    params = {"chat_id": chat_id, "text": text}
    if markup is not None:
        params["reply_markup"] = json.dumps(markup)
    try:
        api(token, "sendMessage", **params)
    except Exception as exc:  # noqa: BLE001
        log.error("sendMessage failed: %s", exc)


def edit(token, chat_id, message_id, text):
    """Replace a message and strip its buttons."""
    try:
        api(token, "editMessageText", chat_id=chat_id, message_id=message_id,
            text=text, reply_markup=json.dumps({"inline_keyboard": []}))
    except Exception as exc:  # noqa: BLE001
        log.error("editMessageText failed: %s", exc)


def mark_paid(students, paid, reg, by_chat):
    if reg in paid:
        return False
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "by": by_chat}
    paid[reg] = entry
    save_payments(paid)
    for s in students:
        if s["reg"] == reg and s["owes"]:
            apply_paid(s, entry)
    return True


def poll(token, chat_id, meta, students, paid):
    idx = build_index(students)
    offset = 0
    log.info("polling for updates...")
    while True:
        try:
            res = api(token, "getUpdates", offset=offset, timeout=25,
                      allowed_updates='["message","callback_query"]')
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("network hiccup (%s), retrying in 5s", exc)
            time.sleep(5)
            continue
        for up in res.get("result", []):
            offset = up["update_id"] + 1

            # ---------- button presses ----------
            cq = up.get("callback_query")
            if cq:
                msg = cq.get("message") or {}
                chat = msg.get("chat", {})
                try:
                    api(token, "answerCallbackQuery", callback_query_id=cq["id"])
                except Exception:  # noqa: BLE001
                    pass
                if chat_id and str(chat.get("id")) != chat_id:
                    continue
                data = cq.get("data") or ""
                if data.startswith("paid:"):
                    reg = data[5:]
                    first = next((s for s in students if s["reg"] == reg), None)
                    if first is None:
                        continue
                    did = mark_paid(students, paid, reg, chat.get("id"))
                    tail = ("\n\n\u2705 MARKED PAID NOW - removed from the owing list."
                            if did else
                            "\n\n(already marked paid earlier - no change)")
                    edit(token, chat.get("id"), msg.get("message_id"),
                         reply_for(first) + tail)
                    log.info("marked paid: %s by chat %s", reg, chat.get("id"))
                elif data.startswith("cancel:"):
                    edit(token, chat.get("id"), msg.get("message_id"),
                         (msg.get("text") or "") + "\n\n\u2716 Cancelled - no change.")
                continue

            # ---------- plain messages ----------
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
            elif text == "/payments":
                send(token, cid, payments_text(paid, students))
            else:
                hit = lookup(text, students, idx)
                if hit is None:
                    send(token, cid,
                         f"No student found for \u201c{text}\u201d.\n"
                         f"Send the index like JCS-0212 (or /help).")
                else:
                    markup = (keyboard(hit["reg"])
                              if hit["owes"] and hit.get("status") == "active"
                              else None)
                    send(token, cid, reply_for(hit), markup)
        time.sleep(0.2)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        meta, students = load_students()
        paid = load_payments()
        for s in students:
            if s["reg"] in paid and s["owes"]:
                apply_paid(s, paid[s["reg"]])
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
    paid = load_payments()
    for s in students:
        if s["reg"] in paid and s["owes"]:
            apply_paid(s, paid[s["reg"]])
    log.info("loaded %d students (%d paid-marks applied); chat restriction: %s",
             len(students), len(paid), chat_id or "NONE (any chat)")
    try:
        me = api(token, "getMe")["result"]
        log.info("connected as @%s", me.get("username"))
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Telegram rejected the token: {exc}")
    while True:
        try:
            poll(token, chat_id, meta, students, paid)
        except KeyboardInterrupt:
            log.info("bye")
            return
        except Exception as exc:  # noqa: BLE001
            log.error("poll crashed (%s), restarting in 5s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
