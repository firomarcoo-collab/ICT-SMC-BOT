"""Your decision on an alert: ENTER, WAIT or IGNORE (the bot never trades; it only records what YOU chose).

The notification of a confirmed alert carries three buttons. Each runs `python decide.py <id> enter|wait|ignore`:
  enter  prints the levels (entry, stop, targets, R:R) and records that you took it,
  wait   prints the exact condition that will decide it (the next candle / retest),
  ignore records it and the bot stops alerting about that setup.
The bot and decide.py never write the same file: decide.py writes decisions.csv, the bot only reads it."""
import csv
import os
import shlex
import sys
import time

FILE = os.environ.get("DECISIONS_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions.csv")
COLUMNS = ["time_utc", "id", "market", "side", "choice", "entry", "sl", "targets", "note"]
CHOICES = ("enter", "wait", "ignore")
LABELS = {"enter": "ENTER", "wait": "WAIT", "ignore": "IGNORE"}


def record(sid, choice, plan=None, now=None, path=None):
    """Appends one decision row. Returns the row, or None for an unknown choice."""
    if choice not in CHOICES:
        return None
    path = path or FILE
    plan = plan or {}
    row = {"time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now if now is not None else time.time())), "id": sid,
           "market": plan.get("market", ""), "side": plan.get("side", ""), "choice": choice, "entry": plan.get("entry", ""),
           "sl": plan.get("sl", ""), "targets": " / ".join(str(t) for t in plan.get("tps", [])), "note": plan.get("note", "")}
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COLUMNS)
        w.writerow([row.get(c, "") for c in COLUMNS])
    return row


def load(path=None):
    try:
        with open(path or FILE, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def ignored(path=None):
    """Ids the user chose to ignore (the latest choice per id wins)."""
    last = {}
    for r in load(path):
        last[r["id"]] = r["choice"]
    return {sid for sid, c in last.items() if c == "ignore"}


def button_args(sid, python=None, script=None):
    """termux-notification arguments for the three buttons (empty list when there is no id)."""
    if not sid:
        return []
    python = python or sys.executable or "python"
    script = script or os.path.join(os.path.dirname(os.path.abspath(__file__)), "decide.py")
    args = []
    for i, ch in enumerate(CHOICES, 1):
        args += [f"--button{i}", LABELS[ch], f"--button{i}-action",
                 f"{shlex.quote(python)} {shlex.quote(script)} {shlex.quote(sid)} {ch}"]
    return args


def options_text(sid, lang="en"):
    """The three choices, as text at the end of a confirmed alert."""
    if lang == "ar":
        return ("🧭 القرار لك: 1) الدخول  2) الانتظار  3) التجاهل (أزرار الإشعار، أو الأمر: "
                f"python decide.py {sid} enter|wait|ignore)")
    return ("🧭 Your decision: 1) ENTER  2) WAIT  3) IGNORE (notification buttons, or: "
            f"python decide.py {sid} enter|wait|ignore)")


def answer(choice, plan, lang="en"):
    """What decide.py prints for each choice."""
    ar = lang == "ar"
    if choice == "enter" and plan.get("entry") is None:
        return (("لا دخول بعد (لم يكتمل التأكيد): " if ar else "NOT YET (no confirmation yet): ")
                + (plan.get("wait") or "wait for the confirmation candle"))
    if choice == "enter":
        tps = " / ".join(str(t) for t in plan.get("tps", [])) or "n/a"
        rr = plan.get("rr")
        return (("الدخول (أنت تنفّذ يدوياً؛ البوت لا ينفذ صفقات): " if ar else "ENTER (you place the trade yourself; the bot never trades): ")
                + f"{plan.get('market', '')} {str(plan.get('side', '')).upper()} | entry {plan.get('entry')} | stop {plan.get('sl')} | "
                  f"targets {tps}" + (f" | R:R about 1:{rr}" if rr else "") + (f" | {plan['invalid']}" if plan.get("invalid") else ""))
    if choice == "wait":
        return ("الانتظار: " if ar else "WAIT: ") + (plan.get("wait") or "no further condition recorded; wait for the next closed candle")
    return ("تجاهل: لن يُرسل البوت تنبيهات أخرى عن هذا الإعداد." if ar else
            "IGNORE: the bot will not alert about this setup again.")
