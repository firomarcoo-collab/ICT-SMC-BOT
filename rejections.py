"""Why was an opportunity not taken? One JSON line per change of reason, per market and side, in rejections.log (UTC time).
Only a CHANGE is written (the same reason is not repeated every five minutes) and the file is capped (rotated at 512 KB).
Read it with:  python why.py XAUUSD"""
import json
import os
import time

FILE = os.environ.get("REJECTIONS_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "rejections.log")
MAX_BYTES = 512 * 1024
_last = {}


def _short(reasons):
    return [str(r)[:200] for r in (reasons or [])][:6]


def log(market, side, stage, reasons, now=None, path=None):
    """Appends one line when (market, side)'s stage or reasons changed. Returns True when a line was written."""
    path = path or FILE
    reasons = _short(reasons)
    key = (path, market, side or "-")
    sig = (stage, tuple(reasons))
    if _last.get(key) == sig:
        return False
    _last[key] = sig
    row = {"time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now if now is not None else time.time())),
           "market": market, "side": side or "-", "stage": stage, "reasons": reasons}
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False                                       # a read-only folder must never stop the scan


def read(market=None, n=10, path=None):
    """The last n lines (newest last), optionally for one market."""
    path = path or FILE
    rows = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if market is None or r.get("market", "").upper() == market.upper():
                    rows.append(r)
    except OSError:
        return []
    return rows[-n:]


def text(rows):
    if not rows:
        return "No rejection reasons logged yet."
    return "\n".join(f"{r['time_utc']} UTC | {r['market']} {r['side'].upper()} | {r['stage']} | " + "; ".join(r["reasons"]) for r in rows)
