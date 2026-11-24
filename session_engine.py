"""Session table per market (signals only). Real session clocks with their own time zones, so summer / winter time is handled:
  Asia      09:00-18:00 Asia/Tokyo
  London    08:00-16:30 Europe/London
  New York  08:00-17:00 America/New_York  (US indices: the cash session 09:30-16:00)
Modes per market and session: signals | breakout (the Asia range breakout only) | monitor (information only, no signal).
Extra rules: the first 15 minutes of the US cash open and the last 30 minutes of the session window are avoided.
The London / New York overlap (08:00-12:00 New York) is flagged as the priority window; it never relaxes any entry rule.
Without a time zone database the answer is 'unavailable' and nothing is allowed (never a fixed UTC guess)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:                                           # pragma: no cover
    ZoneInfo = None

SESSIONS = {"asia": ("Asia/Tokyo", 9.0, 18.0), "london": ("Europe/London", 8.0, 16.5), "ny": ("America/New_York", 8.0, 17.0)}
US_CASH = ("America/New_York", 9.5, 16.0)
INDICES = ("US100", "NAS100", "US30")
S, B, M = "signals", "breakout", "monitor"
TABLE = {
    "XAUUSD": {"asia": B, "london": S, "ny": S},
    "BTCUSD": {"asia": B, "london": S, "ny": S},
    "US100": {"asia": M, "london": M, "ny": S},
    "US30": {"asia": M, "london": M, "ny": S},
    "USDJPY": {"asia": M, "london": S, "ny": S},
    "GBPUSD": {"asia": M, "london": S, "ny": S},
}
ALIASES = {"NAS100": "US100"}
PRIORITY = ("ny", "london", "asia")                           # when sessions overlap, the stronger mode wins by this order
AVOID_OPEN_MIN = 15
AVOID_END_MIN = 30


def _hours(tz, dt):
    h = dt.astimezone(ZoneInfo(tz))
    return h.hour + h.minute / 60.0


def status(market, now_ts):
    """dict: session (name or None), mode (signals / breakout / monitor / closed / unavailable), allowed (a normal signal may be issued),
    breakout (an Asia breakout signal may be issued), priority (London / New York overlap), reason."""
    market = ALIASES.get(market.upper(), market.upper())
    out = {"market": market, "session": None, "mode": "closed", "allowed": False, "breakout": False, "priority": False, "reason": ""}
    if market not in TABLE:
        out["mode"] = "unavailable"
        out["reason"] = f"{market} is not one of the six supported markets"
        return out
    if ZoneInfo is None:
        out["mode"] = "unavailable"
        out["reason"] = "no time zone database: session times cannot be trusted (install tzdata)"
        return out
    dt = datetime.fromtimestamp(now_ts, timezone.utc)
    try:
        active = []
        for name in PRIORITY:
            tz, a, b = SESSIONS[name]
            if name == "ny" and market in ("US100", "US30"):
                tz, a, b = US_CASH
            x = _hours(tz, dt)
            if a <= x < b:
                active.append((name, a, b, x))
        out["priority"] = 8.0 <= _hours("America/New_York", dt) < 12.0 and market not in ("US100", "US30")
    except Exception as e:                                     # noqa: BLE001  (a missing zone name)
        out["mode"] = "unavailable"
        out["reason"] = f"time zone lookup failed: {e}"
        return out
    if not active:
        out["reason"] = "between sessions"
        return out
    rank = {S: 0, B: 1, M: 2}
    name, a, b, x = sorted(active, key=lambda s: rank[TABLE[market][s[0]]])[0]
    mode = TABLE[market][name]
    out.update(session=name, mode=mode)
    if name == "ny" and market in ("US100", "US30") and x < a + AVOID_OPEN_MIN / 60.0:
        out.update(mode=M, reason="the first 15 minutes of the US open are avoided")
        return out
    if x >= b - AVOID_END_MIN / 60.0:
        out.update(mode=M, reason=f"the last {AVOID_END_MIN} minutes of the {name} session are avoided")
        return out
    out["allowed"] = mode == S
    out["breakout"] = mode in (B, S)
    if mode == M:
        out["reason"] = f"{market} is monitor-only in the {name} session"
    elif mode == B:
        out["reason"] = f"{market}: only the Asia breakout may signal in the {name} session"
    return out


def gate_enabled():
    return os.environ.get("SESSION_GATE", "1") != "0"


def block_reason(market, now_ts):
    """None when a normal signal is allowed now, else the one-line reason."""
    st = status(market, now_ts)
    if st["allowed"]:
        return None
    return f"session gate: {st['reason'] or st['mode']} ({st['session'] or 'no session'})"
