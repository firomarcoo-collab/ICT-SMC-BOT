"""Telegram message for a SELECTIVE signal in the KENAN TRADER AI layout, and the plain sender. Information only: the bot never
trades. Every field comes from the engine's own result; a missing value is shown as '-', never invented."""
from __future__ import annotations

import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SIGNAL_SUFFIXES = ("SIGNAL", "CONFIRMED")
PREP_NOTE = "ℹ️ Preparatory / information alert: this is NOT an entry signal."


def enabled():
    return bool(os.environ.get("TELEGRAM_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def is_signal_class(cls):
    return cls.endswith(SIGNAL_SUFFIXES)


def _v(x):
    return "-" if x in (None, "", []) else str(x)


def _num(v, digits):
    return "-" if v is None else f"{v:.{digits}f}"


def format_signal(market, setup, res, session_text="", news_text="", digits=2, spread_text=""):
    rep = (res or {}).get("report") or {}
    tr = setup.get("trends") or {}
    zones = rep.get("zones") or setup.get("zones") or []
    t = datetime.fromtimestamp(setup["bar_time"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if setup.get("bar_time") else "-"
    risk = abs(setup["entry"] - setup["sl"])
    gross = abs(setup["tp"] - setup["entry"]) / risk if risk else 0.0
    lines = [
        "KENAN TRADER AI", "",
        f"Market: {market}",
        f"Signal: {setup['side'].upper()}",
        f"Strategy: SELECTIVE ({str(setup.get('profile', '-')).upper()})",
        f"Session: {_v(session_text)}",
        f"Quality Score: {_v(setup.get('score100'))}/100", "",
        "Higher Timeframes:",
        f"H4: {_v(tr.get('h4'))}", f"H1: {_v(tr.get('h1'))}", f"M15: {_v(tr.get('m15'))}", "",
        f"Liquidity Sweep: {_v(rep.get('liquidity'))}",
        f"MSS / CHoCH: {_v(rep.get('structure'))}",
        f"Displacement: {_v((setup.get('volume') or {}).get('text') or rep.get('reversal'))}",
        f"FVG / OB Zone: {_v('; '.join(map(str, zones)) if zones else None)}",
        f"Retest: {_v(rep.get('retest'))}",
        f"Confirmation Candle: {_v(rep.get('confirm'))}", "",
        f"Entry Zone: {_num(setup['entry'], digits)}",
        f"Stop Loss: {_num(setup['sl'], digits)}",
        f"TP1: {_num(setup.get('tp1'), digits)} (1R, then move the stop to break-even by hand)",
        f"TP2: {_num(setup['tp'], digits)}",
        f"Risk/Reward: 1:{gross:.1f} before costs" + (f" | {setup['cost_note']}" if setup.get("cost_note") else ""),
        f"Spread: {_v(spread_text or setup.get('cost'))}",
        f"News Status: {_v(news_text)}", "",
        f"Signal Time: {t}",
        f"Invalidation Condition: a full M15 candle closing beyond {_num(setup.get('early_exit', setup.get('invalidation')), digits)}",
        f"Reason for Signal: {_v(rep.get('mode'))}",
        "", "Signal only: place the trade yourself in MT5. No win rate is guaranteed.",
    ]
    return "\n".join(lines)


def send(text, silent=False):
    """Plain Telegram send. Returns True when it was sent. Never raises."""
    if not enabled():
        return False
    try:
        fields = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}
        if silent:
            fields["disable_notification"] = "true"
        urllib.request.urlopen(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
                               data=urllib.parse.urlencode(fields).encode(), timeout=20)
        return True
    except Exception:                                              # noqa: BLE001
        return False


def send_class(cls, text):
    """Alert classes: a signal class is sent as it is; every other class is labelled as preparatory (silent)."""
    if not enabled() or os.environ.get("TELEGRAM_CLASSES", "1") == "0":
        return False
    if is_signal_class(cls):
        return send(text)
    return send(PREP_NOTE + "\n\n" + text, silent=True)
