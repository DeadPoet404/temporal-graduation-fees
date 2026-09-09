# temporal-graduation-fees

Telegram bot for **JOCOMFY SCHOOL** that answers one question instantly:

> *"Does this student owe the graduation fee (GHS 50) or not?"*

Send the bot an index number (`JCS-0212`, `jcs 0212`, `0212`, `212` …) and it
replies with the student's name, class, pen-written balance, the range maths
and a clear **OWES / PAID** verdict.

No third-party Python packages — **standard library only** (Python 3.8+), so
it runs on any bare server.

## How the verdict is decided

| class group | range | owes graduation when pen balance reaches |
|---|---|---|
| Crèche, Nursery 2 | 420 | 470 |
| KG 1 | 430 | 480 |
| KG 2, Primary 1, Primary 2 | 450 | 500 |
| Primary 3, 4, 5 | 470 | 520 |
| Primary 6, JHS 1 | 500 | 550 |
| JHS 2 | 600 | 650 |

* arrears = the **typed** "Fee Remaining" on the register;
  current charge = **pen − typed** (pen alone if pen − typed < range)
* current charge − range **≥ 50 → owes graduation** (exactly +50 = graduation
  only; more = graduation + earlier arrears)
* 1–49 over the range = graduation paid, owes arrears only
* on the range exactly = graduation paid
* struck-off / "Stopped" students answer with their status, not a fee verdict

## Setup (before first run)

```bash
cp config.example.json config.json     # config.json is git-ignored
# edit config.json:
#   bot_token : token from @BotFather
#   chat_id   : telegram chat id allowed to query ("" = any chat)
python3 bot.py
```

Environment variables `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` override the
file if you prefer.

Try it without Telegram first:

```bash
python3 bot.py --test JCS-0212
```

## Run as a service (server)

```bash
sudo cp deploy/grad-bot.service /etc/systemd/system/grad-bot.service
# adjust User= and WorkingDirectory= to your checkout path
sudo systemctl daemon-reload
sudo systemctl enable --now grad-bot
journalctl -u grad-bot -f
```

## Bot commands

| message | reply |
|---|---|
| `/start`, `/help` | usage |
| `/stats` | register summary (students, owers, total, paid-marks) |
| `/payments` | audit list of indexes marked paid via the bot |
| anything else | treated as an index number |

When a student **owes**, the reply shows two buttons: **Paid now** (records the
payment in `data/payments.json`, removes the index from the owing list and
edits the message) and **Cancel** (dismisses the buttons, changes nothing).
The ledger survives restarts and `gen_data.py` regenerations, and is
git-ignored - it lives on each machine

## Data

`data/students.json` is generated from the transcribed paper register
(`tools/jocomfy_data.py`) by:

```bash
python3 gen_data.py
```

Known data caveats baked into the dataset:

* 10 handwritten additions have **no index yet** (not in eskooly) and are
  omitted — they cannot be queried by number: HABSA OWUSU, ADJEI IVAN G.,
  SALIM LUCKY, BOATENG LEMUEL, SAMBO DAVID, COLINS MIKORDORME, LUKEMAN AISHA,
  IBRAHIM INAGRA, OFFUL DANIEL KWAKYE, OSINGA JOSHUA MARTIN.
* A few index numbers appear twice on the paper register for **different**
  children (JCS-0367, JCS-0372, JCS-0473, JCS-0493, JCS-423, JCS-424): the bot
  answers "more than one student matches" for those.
* JCS-0297 and JCS-0495 carry two pen balances each (same child, two rows);
  the newest figure wins and the reply carries a confirmation note.
* JCS-0116 is office-overridden to *graduation paid* (pen workings
  150 + 140 + 470 = 760).
