"""Extras for the alerts: trading sessions, lot size, high-impact news. Standard library only."""
import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone

NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Contract sizes are NOT guessed any more: they come from markets.json (see markets.py).

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


def lot_text(balance, risk_percent, setup, spec, assumed_contract=None):
    """Lot line for an alert. Uses the verified broker specification; without it, it says so
    (or uses your own CONTRACT_<SYMBOL> assumption and labels it as an assumption)."""
    if not balance:
        return ""
    risk = abs(setup["entry"] - setup["sl"])
    if spec is not None and spec.verified():
        import markets
        pct = spec.risk_percent if spec.risk_percent else risk_percent
        lot, money, reason = markets.lot_for_risk(spec, balance, pct, setup["entry"], setup["sl"])
        if lot <= 0:
            return f"Lot: none ({reason})\n"
        return f"Lot: {lot:.2f} (risk about {money:.2f} = {pct}% of {balance:g}; broker specification)\n"
    if assumed_contract:
        return lot_line(balance, risk_percent, risk, assumed_contract).replace(
            "assumes", "YOUR ASSUMPTION:")
    return "Lot: not calculated, broker specification not verified (run: python markets.py)\n"


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


# ===== Multi-timeframe trend =====

TF_ORDER = ["1d", "4h", "1h", "15m", "5m", "1m"]


def trend_line(cache, name, tf, side):
    """e.g. 'Trends: 1d up | 4h up | 1h flat | 5m down' plus whether higher timeframes agree.
    cache: {(name, tf): 'up'|'down'|'flat'}."""
    parts = [(t, cache.get((name, t))) for t in TF_ORDER if cache.get((name, t))]
    if not parts:
        return ""
    line = "Trends: " + " | ".join(f"{t} {v}" for t, v in parts) + "\n"
    if side and tf in TF_ORDER:
        higher = [v for t, v in parts if TF_ORDER.index(t) < TF_ORDER.index(tf)]
        want = "up" if side == "buy" else "down"
        if higher:
            agree = sum(1 for v in higher if v == want)
            against = sum(1 for v in higher if v not in (want, "flat"))
            if against:
                line += f"Higher timeframes: {against} against this {side.upper()} (counter-trend)\n"
            elif agree:
                line += f"Higher timeframes: {agree} agree with this {side.upper()}\n"
    return line


# ===== US market open (09:30 New York) =====

US_BEFORE = 30 * 60   # the window starts 30 minutes before the open
US_AFTER = 120 * 60   # and lasts 2 hours after it


def us_open_ts(ts):
    """Timestamp of the 09:30 New York open on the New York date of `ts`; None on weekends."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = dt.astimezone(ZoneInfo("America/New_York"))
        if local.weekday() >= 5:
            return None
        return int(local.replace(hour=9, minute=30, second=0, microsecond=0).timestamp())
    except Exception:  # no timezone database: use winter time (UTC-5)
        local = dt - timedelta(hours=5)
        if local.weekday() >= 5:
            return None
        base = local.replace(hour=9, minute=30, second=0, microsecond=0)
        return int((base + timedelta(hours=5)).replace(tzinfo=timezone.utc).timestamp())


def us_open_state(ts):
    """None outside the window, else minutes relative to the open (negative = before)."""
    open_ts = us_open_ts(ts)
    if open_ts is None:
        return None
    delta = ts - open_ts
    if -US_BEFORE <= delta <= US_AFTER:
        return round(delta / 60)
    return None


def us_open_line(ts):
    m = us_open_state(ts)
    if m is None:
        return ""
    when = f"opens in {-m} min" if m < 0 else f"opened {m} min ago"
    return f"US OPEN window: US stocks {when}. Expect fast moves and wider spreads.\n"
