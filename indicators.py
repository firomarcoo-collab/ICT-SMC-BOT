"""Session VWAP and ADX(14) for the signal quality engine. Confirmation only: neither one creates a signal.

VWAP uses the real candle volume when the feed supplies it (Binance does, Yahoo mostly not for forex/indices);
without volume it is the equal-weighted average of the typical price and is labelled that way.
"""
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:      # very old Python: fixed winter offsets are used instead
    ZoneInfo = None

# market -> (time zone, local hour, local minute) at which the VWAP session starts
SESSION_ANCHOR = {
    "BTCUSD": ("UTC", 0, 0),                      # crypto: calendar day UTC
    "NAS100": ("America/New_York", 9, 30), "US100": ("America/New_York", 9, 30),
    "US30": ("America/New_York", 9, 30),          # US cash open
    "DAX40": ("Europe/Berlin", 9, 0),             # Xetra open
    "XAUUSD": ("America/New_York", 17, 0), "EURUSD": ("America/New_York", 17, 0),
    "GBPJPY": ("America/New_York", 17, 0), "JPYGBP": ("America/New_York", 17, 0),   # FX day starts 17:00 New York
}
_WINTER = {"UTC": 0, "America/New_York": -5, "Europe/Berlin": 1}


def session_start(market, now_ts):
    """Unix time of the most recent session start for this market (DST aware)."""
    zone, hh, mm = SESSION_ANCHOR.get(market, ("UTC", 0, 0))
    now = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    if ZoneInfo is not None:
        try:
            local = now.astimezone(ZoneInfo(zone))
            start = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if start > local:
                start -= timedelta(days=1)
            return int(start.timestamp())
        except Exception:
            pass
    local = now + timedelta(hours=_WINTER.get(zone, 0))
    start = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if start > local:
        start -= timedelta(days=1)
    return int((start - timedelta(hours=_WINTER.get(zone, 0))).replace(tzinfo=timezone.utc).timestamp())


def vwap(bars, start_ts):
    """(value, weighted) for the closed bars at or after start_ts; value is None when there are no bars.
    weighted=False means the feed had no volume (equal weights)."""
    rows = [b for b in bars if int(b["time"].timestamp()) >= start_ts]
    if not rows:
        return None, False
    vol = [float(b.get("volume") or 0) for b in rows]
    weighted = sum(vol) > 0
    w = vol if weighted else [1.0] * len(rows)
    typ = [(b["high"] + b["low"] + b["close"]) / 3 for b in rows]
    return sum(t * x for t, x in zip(typ, w)) / sum(w), weighted


def vwap_view(bars, market, now_ts, price, side):
    """Dict for the signal: value, weighted flag, whether price sits on the trade's side, short text."""
    val, weighted = vwap(bars, session_start(market, now_ts))
    if val is None:
        return {"value": None, "agree": None, "text": "VWAP unavailable (no candles in this session yet)"}
    above = price > val
    agree = above if side == "buy" else not above
    note = "" if weighted else " (equal weights: the feed has no volume)"
    return {"value": val, "agree": agree, "weighted": weighted,
            "text": f"{val:.5g}, price {'above' if above else 'below'} VWAP "
                    f"({'agrees with' if agree else 'against'} the {side.upper()}){note}"}


def adx(bars, period=14):
    """Wilder ADX. Returns (adx, plus_di, minus_di) or None when there are fewer than 2*period+1 bars."""
    if len(bars) < 2 * period + 1:
        return None
    tr, pdm, mdm = [], [], []
    for p, c in zip(bars, bars[1:]):
        up, dn = c["high"] - p["high"], p["low"] - c["low"]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"])))
    atr, p_s, m_s = sum(tr[:period]), sum(pdm[:period]), sum(mdm[:period])
    dxs = []
    for i in range(period, len(tr) + 1):
        if i > period:
            atr = atr - atr / period + tr[i - 1]
            p_s = p_s - p_s / period + pdm[i - 1]
            m_s = m_s - m_s / period + mdm[i - 1]
        if atr == 0:
            dxs.append(0.0)
            continue
        pdi, mdi = 100 * p_s / atr, 100 * m_s / atr
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    if len(dxs) < period:
        return None
    a = sum(dxs[:period]) / period
    for d in dxs[period:]:
        a = (a * (period - 1) + d) / period
    return a, pdi, mdi


def adx_view(bars, side, period=14):
    """Dict for the signal: adx value, DI direction, whether the trend strength supports the trade."""
    r = adx(bars, period)
    if r is None:
        return {"value": None, "agree": None, "text": "ADX unavailable (not enough candles)"}
    a, pdi, mdi = r
    strong = a >= 20
    di_agree = (pdi > mdi) == (side == "buy")
    kind = "trending" if a >= 25 else "developing" if a >= 20 else "weak / ranging"
    return {"value": a, "agree": strong and di_agree, "strong": strong, "text":
            f"ADX {a:.1f} ({kind}), +DI {pdi:.1f} / -DI {mdi:.1f}, "
            f"{'agrees with' if di_agree else 'against'} the {side.upper()}"}


def context_points(vw, ax):
    """Quality points from VWAP and ADX: 3 + 3 at most, added after the base score (capped at 100).
    The two are only context, so they never remove points and never replace a mandatory ICT condition."""
    return (3 if vw.get("agree") else 0) + (3 if ax.get("agree") else 0)
