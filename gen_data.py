#!/usr/bin/env python3
"""
Regenerate data/students.json from the register transcription
(tools/jocomfy_data.py).  Run this only when the paper register changes:

    python3 gen_data.py

Rules encoded here (as agreed with the school office):
  * class ranges: Creche/Nursery 420, KG1 430, KG2/P1/P2 450,
    P3-P5 470, P6/JHS1 500, JHS2 600
  * graduation fee = range + 50
  * arrears = typed "Fee Remaining"; current charge = pen - typed
    (fall back to the pen figure alone when pen - typed < range)
  * current charge - range >= 50  ->  OWES graduation fee
  * students with no reg # (handwritten additions not yet in eskooly)
    are omitted - they cannot be queried by index
"""

import json
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "tools"))
from jocomfy_data import R, OVERRIDE  # noqa: E402

RANGE = {
    "Creche": 420, "Nursery 2": 420,
    "KG 1": 430,
    "KG 2": 450, "Primary 1": 450, "Primary 2": 450,
    "Primary 3": 470, "Primary 4": 470, "Primary 5": 470,
    "Primary 6": 500, "JHS 1": 500,
    "JHS 2": 600,
}
GRAD = 50

# students struck off / stopped on the paper register
STOPPED = {
    "JCS-0175": "pen 'Stop'",
    "JCS-0277": "pen 'Stopp'",
    "JCS-0396": "pen 'Stopped'",
    "JCS-437": "row struck through",
    "JCS-0331": "row struck through",
    "JCS-416": "row struck through",
    "JCS-0344": "pen 'Stopped'",
    "JCS-0452": "row struck through",
    "JCS-0406": "row struck through (duplicate of JCS-0352)",
    "JCS-0163": "row struck through",
    "JCS-0275": "row struck through",
    "JCS-0086": "pen 'Stopped'",
    "JCS-0080": "pen 'Stopped'",
    "JCS-0394": "row struck through",
    "JCS-0403": "row struck through",
    "JCS-0323": "row struck through",
    "JCS-0024": "pen 'Stopped'",
    "JCS-0025": "pen 'Stopped'",
    "JCS-0513": "row struck through",
}


def classify(pen, typed, rng):
    a = typed or 0
    c = pen - a if pen - a >= rng else pen
    diff = c - rng
    owes = diff >= GRAD
    if owes:
        if diff == GRAD:
            detail = f"{c} = {rng} class range + {GRAD} graduation fee"
        else:
            detail = (f"{c} = {rng} class range + {GRAD} graduation fee "
                      f"+ {diff - GRAD} earlier arrears")
        if a:
            detail += f"  (pen {pen} - typed arrears {a})"
    elif diff > 0:
        detail = f"{c} = {rng} class range + {diff} arrears; graduation fee paid"
    elif diff == 0:
        detail = f"{c} = {rng} class range exactly; graduation fee paid"
        if a:
            detail += f"  (pen {pen} - typed arrears {a})"
    else:
        detail = f"{c} is below the {rng} class range (part payment?); graduation fee not charged"
    if rng + GRAD > c > rng and False:  # kept explicit above
        pass
    return owes, detail


# reg #s where two register rows are the SAME child (office-confirmed),
# so their pen figures belong to one student
SAME_CHILD = {"JCS-0297", "JCS-0495"}


def main():
    groups, order = {}, []
    for reg, name, cls, pen, typed in R:
        if not reg or reg == "JCS-?":
            continue  # no index -> cannot be queried, omitted on purpose
        groups.setdefault(reg, []).append((name, cls, pen, typed))
        if reg not in order:
            order.append(reg)

    merged = {}
    for reg in order:
        rows = groups[reg]
        pens = [p for _, _, p, _ in rows if p is not None]
        same_child = len(pens) <= 1 or reg in SAME_CHILD
        if same_child:
            merged[reg] = [rows]                      # one student entry
        else:
            merged[reg] = [[r] for r in rows]         # distinct children, shared reg #

    students = []
    for reg in order:
      for e_rows in merged[reg]:
        e = {"reg": reg, "name": e_rows[0][0], "class": e_rows[0][1],
             "pens": [], "typed": None, "shared": len(merged[reg]) > 1}
        for name, cls, pen, typed in e_rows:
            if pen is not None:
                e["pens"].append(pen)
                e["class"] = cls      # class of the row carrying the live pen figure
                e["name"] = name
            if typed is not None:
                e["typed"] = typed
        rng = RANGE[e["class"]]
        status = "stopped" if reg in STOPPED else "active"
        pens = e["pens"]
        pen = pens[-1] if pens else None          # last written figure wins
        if pen is None:
            owes, detail = False, "no pen balance recorded on the register"
            if status == "stopped":
                detail = "struck off / stopped on the register"
        else:
            owes, detail = classify(pen, e["typed"], rng)
            if reg in OVERRIDE and OVERRIDE[reg] is False and owes:
                owes = False
                detail += "  |  office override: pen workings show range + arrears only"
        note = ""
        if e.get("shared"):
            note = ("index shared with another student on the register - "
                    "confirm at the office")
        if len(pens) > 1 and len(set(pens)) > 1:
            note = ("register carries two pen balances for this index ("
                    + " / ".join(str(p) for p in pens)
                    + ") - confirm the live one at the office")
        students.append({
            "reg": reg,
            "name": e["name"],
            "class": e["class"],
            "pen": pens[-1] if pens else None,
            "pen_all": pens if len(pens) > 1 else None,
            "typed": e["typed"],
            "range": rng,
            "owes": bool(owes),
            "status": status,
            "status_reason": STOPPED.get(reg, ""),
            "detail": detail,
            "note": note,
        })

    # fold in anything the bot marked paid (data/payments.json ledger)
    pay_file = BASE / "data" / "payments.json"
    paid = json.loads(pay_file.read_text(encoding="utf-8")) if pay_file.exists() else {}
    applied = 0
    for s in students:
        if s["reg"] in paid and s["owes"]:
            s["owes"] = False
            s["paid_mark"] = True
            s["detail"] += f"  |  marked PAID via bot on {paid[s['reg']].get('at', '?')[:10]}"
            applied += 1
    if applied:
        print(f"applied {applied} paid-mark(s) from data/payments.json")

    out = {
        "meta": {
            "school": "JOCOMFY SCHOOL",
            "generated": str(date.today()),
            "graduation_fee": GRAD,
            "ranges": RANGE,
            "source": "paper register (pen figures), transcribed 2026-09",
        },
        "students": students,
    }
    dest = BASE / "data" / "students.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    owers = [s for s in students if s["owes"] and s["status"] == "active"]
    print(f"wrote {dest}: {len(students)} students, {len(owers)} owe graduation")


if __name__ == "__main__":
    main()
