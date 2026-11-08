"""Extras for the alerts: trading sessions, lot size, high-impact news. Standard library only."""
import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone

NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Price units per 1.00 lot. Brokers differ: check "Contract size" in the MT5 symbol
# specification and override with CONTRACT_<SYMBOL> if it is not the same.
CONTRACT = {"BTCUSD": 1.0, "XAUUSD": 100.0, "US100": 1.0, "US30": 1.0, "EURUSD": 100000.0}

# Which news currencies matter for each symbol.
CURRENCIES = {"BTCUSD": {"USD"}, "XAUUSD": {"USD"}, "US100": {"USD"},
              "US30": {"USD"}, "EURUSD": {"USD", "EUR"}}


# ===== Sessions =====

def _local_hour(ts, zone, utc_fallback_offset):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo(zone)).hour
    except Exception:  # no timezone database on the phone: use winter hours
        return (dt + timedelta(hours=utc_fallback_offset)).hour


def active_sessions(ts):
    """London and New York cash hours 08:00-17:00 local time. Empty on the weekend."""
    if datetime.fromtimestamp(ts, tz=timezone.utc).weekday() >= 5:
        return []
    out = []
    if 8 <= _local_hour(ts, "Europe/London", 0) < 17:
        out.append("London")
    if 8 <= _local_hour(ts, "America/New_York", -5) < 17:
        out.append("New York")
    return out


def session_text(ts):
    s = active_sessions(ts)
    if not s:
        return "outside London / New York hours"
    return " + ".join(s) + (" (overlap)" if len(s) == 2 else "")


# ===== Lot size =====

def lot_size(balance, risk_percent, risk_distance, contract):
    """Lot so that hitting the stop loses about balance * risk_percent / 100.
    Rounded DOWN to 0.01. Returns (lot, money_at_risk)."""
    if balance <= 0 or risk_distance <= 0 or contract <= 0:
        return 0.0, 0.0
    money = balance * risk_percent / 100.0
    lot = math.floor(money / (risk_distance * contract) * 100) / 100
    return lot, money


def lot_line(balance, risk_percent, risk_distance, contract):
    if not balance:
        return ""
    lot, money = lot_size(balance, risk_percent, risk_distance, contract)
    if lot < 0.01:
        return (f"Lot: below 0.01 for {risk_percent}% risk (${money:.2f}); "
                f"the stop is too wide for your balance\n")
    return (f"Lot: {lot:.2f} (risk about ${money:.2f} = {risk_percent}% of ${balance:g}; "
            f"assumes 1 lot = {contract:g} units)\n")


# ===== News =====

def parse_news(data):
    """Keep only high-impact events: [{'ts', 'title', 'currency'}]."""
    out = []
    for e in data:
        if str(e.get("impact", "")).lower() != "high":
            continue
        try:
            ts = int(datetime.fromisoformat(e["date"]).timestamp())
        except Exception:
            continue
        out.append({"ts": ts, "title": e.get("title", "?"), "currency": e.get("country", "")})
    return out


def fetch_news():
    req = urllib.request.Request(NEWS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return parse_news(json.loads(resp.read().decode()))


def near_news(events, name, now_ts, before=30 * 60, after=15 * 60):
    """Events for this symbol that start within `before` seconds, or began less than `after` ago."""
    wanted = CURRENCIES.get(name, {"USD"})
    return [e for e in events
            if e["currency"] in wanted and -after <= e["ts"] - now_ts <= before]


def news_line(events, name, now_ts):
    near = near_news(events, name, now_ts)
    if not near:
        return ""
    parts = []
    for e in sorted(near, key=lambda e: e["ts"]):
        mins = round((e["ts"] - now_ts) / 60)
        when = f"in {mins} min" if mins >= 0 else f"{-mins} min ago"
        parts.append(f"{e['currency']} {e['title']} {when}")
    return "WARNING high-impact news: " + "; ".join(parts) + "\n"
