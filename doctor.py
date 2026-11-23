"""Bot doctor: why is something missing? Run: python doctor.py   (reads files only; nothing is changed, no key is printed)."""
import os
import shutil
import subprocess
import sys

WANT = ("BTCUSD", "XAUUSD", "US100", "US30", "EURUSD", "GBPJPY", "GBPUSD", "USDJPY")
FILES = ("~/.shortcuts/ICT-Alerts", "~/.bashrc", "~/.profile")
WATCH = ("SYMBOLS", "ALERT_LANG", "DISPLACEMENT", "US_MODE", "FLIP_IFVG", "ASIA_BREAKOUT", "ALERT_CLASSES")


def check(env, file_texts, tools, commit="", default_symbols=WANT):
    """Pure function: [(level, message)] with level OK / WARN / BAD."""
    out = []
    out.append(("OK", f"code version: {commit or 'unknown'}"))
    sym = [s.strip().upper() for s in (env.get("SYMBOLS") or "").split(",") if s.strip()]
    if sym:
        missing = [s for s in default_symbols if s not in sym]
        out.append(("WARN" if missing else "OK", f"SYMBOLS override is set: {','.join(sym)}" + (f" (missing {','.join(missing)})" if missing else "")))
    else:
        out.append(("OK", "SYMBOLS not set: the defaults are used (" + ",".join(default_symbols) + ")"))
    for name, text in file_texts.items():
        for line in text.splitlines():
            if any(w in line for w in WATCH) and not line.strip().startswith("#"):
                out.append(("WARN", f"{name}: {line.strip()}  <- overrides the bot defaults, remove it if unintended"))
    out.append(("OK" if tools.get("termux-notification") else "BAD",
                "termux-notification found" if tools.get("termux-notification") else
                "termux-notification missing: install the Termux:API app and run: pkg install termux-api"))
    out.append(("OK" if tools.get("termux-tts-speak") else "WARN", "voice available" if tools.get("termux-tts-speak") else "voice not available (optional)"))
    out.append(("OK" if env.get("TWELVEDATA_API_KEY") else "WARN", "Twelve Data key is set (not shown)" if env.get("TWELVEDATA_API_KEY") else
                "no Twelve Data key: US100/US30 use only the free fallbacks"))
    out.append(("OK" if env.get("BALANCE") else "WARN", "BALANCE is set" if env.get("BALANCE") else "BALANCE not set: no lot size in the alerts"))
    return out


def main():
    env = os.environ
    texts = {}
    for f in FILES:
        p = os.path.expanduser(f)
        try:
            with open(p, errors="ignore") as fh:
                texts[f] = fh.read()
        except OSError:
            pass
    try:
        commit = subprocess.run(["git", "log", "--oneline", "-1"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = ""
    tools = {t: bool(shutil.which(t)) for t in ("termux-notification", "termux-tts-speak")}
    icon = {"OK": "✅", "WARN": "⚠️", "BAD": "❌"}
    for lvl, msg in check(env, texts, tools, commit):
        print(f"{icon[lvl]} {msg}")
    print("Next: python check_feeds.py (data sources). Markets closed on weekends: US100/US30/forex/gold show no new bars.")


if __name__ == "__main__":
    sys.exit(main())
