#!/usr/bin/env bash
# Starts (or restarts) the graduation-fee bot in the background.
# Usage:  ./run.sh     log: bot.log
set -u
cd "$(dirname "$0")"
pkill -f "python3 bot.py" 2>/dev/null
sleep 1
nohup python3 bot.py >> bot.log 2>&1 &
sleep 2
if pgrep -f "python3 bot.py" >/dev/null; then
    echo "bot is RUNNING (pid $(pgrep -f 'python3 bot.py' | head -1)) - log: bot.log"
    tail -n 5 bot.log
else
    echo "bot did NOT stay up - last log lines:"
    tail -n 15 bot.log
fi
