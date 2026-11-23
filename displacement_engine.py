"""Institutional Liquidity Sweep & Momentum Displacement Engine. SIGNALS ONLY: it emits JSON alerts, it never trades.
Closed candles only (the last LTF bar passed in is a bar that has just CLOSED), HTF data = previously closed bars only,
no lookahead, idempotent (feeding the same bar twice emits nothing). One finite state machine, one active setup.

  STATE_0_IDLE -> STATE_1_IMPULSE -> STATE_2_RETEST -> STATE_3_COOLDOWN
  events: SETUP, RETEST, CONFIRMED, INVALIDATED, MISSED  (every transition is logged and returned as a dict)

Use:  eng = Engine("XAUUSD", Config());  events = eng.on_bar(ltf_closed_bars, htf_closed_bars)
Bars: dicts with time (tz-aware datetime = bar OPEN), open, high, low, close, volume (oldest first).
Every threshold lives in Config. Not measured on live results; it is a rule set, not a promise."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("displacement")

IDLE, IMPULSE, RETEST, COOLDOWN = "STATE_0_IDLE", "STATE_1_IMPULSE_DETECTED", "STATE_2_RETEST_IN_PROGRESS", "STATE_3_COOLDOWN"


@dataclass(frozen=True)
class Config:
    tz: str = "America/New_York"
    killzones: tuple = ((2.0, 5.0), (8.0, 11.0))     # London and New York hunting windows (hours, New York time)
    confirm_in_killzone: bool = True                  # a setup that outlives the window is MISSED
    ltf_seconds: int = 900                            # M15
    htf_seconds: int = 3600                           # H1
    atr_period: int = 14
    alpha: float = 0.15                               # expansion breakout: close beyond the level by alpha x ATR
    body_ratio: float = 0.60                          # |close-open| / (high-low)
    range_atr: float = 1.2                            # candle range >= this x ATR
    vol_mult: float = 1.3                             # volume >= this x SMA(volume, 20)[1]
    vol_sma: int = 20
    volume_required: bool = True                      # no volume data = no signal (set False for feeds without volume)
    chase_atr: float = 1.2                            # price ran this far from the impulse close without a retest = MISSED
    max_wait_bars: int = 16
    tol_atr: float = 0.10                             # breakout-edge tolerance for the retest
    sl_atr: float = 0.5
    tp_r: tuple = (1.0, 2.0, 3.0)
    invalid_closes: int = 2                           # consecutive LTF closes beyond the invalidation level
    bias_ema: int = 50
    require_bias: bool = True
    swing_k: int = 2
    pool_bars: int = 80
    sweep_lookback: int = 3                           # the wick may belong to the displacement candle or the 2 before it


def ema(values, n):
    k, e = 2.0 / (n + 1), None
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
    return e


def atr_at(bars, n, end=None):
    end = len(bars) if end is None else end
    if end < n + 1:
        return None
    trs = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
           for p, b in zip(bars[end - n - 1:end - 1], bars[end - n:end])]
    return sum(trs) / n


class Engine:
    def __init__(self, symbol: str, cfg: Optional[Config] = None):
        self.symbol, self.cfg = symbol, cfg or Config()
        self.tz = ZoneInfo(self.cfg.tz)
        self.state, self.s = IDLE, {}
        self.last_ts: Optional[datetime] = None
        self.cool_day = None
        self.plan = None                    # the confirmed plan, watched for the early exit
        self.trail: list[dict] = []         # audit trail of every transition

    # ---------- helpers ----------
    def local(self, t):
        return t.astimezone(self.tz)

    def in_killzone(self, t) -> bool:
        h = self.local(t)
        x = h.hour + h.minute / 60.0
        return any(a <= x < b for a, b in self.cfg.killzones)

    def htf_closed(self, htf, now_close):
        sec = self.cfg.htf_seconds
        return [b for b in htf if b["time"] + timedelta(seconds=sec) <= now_close]

    def bias(self, htf):
        if len(htf) < self.cfg.bias_ema:
            return None
        e = ema([b["close"] for b in htf], self.cfg.bias_ema)
        c = htf[-1]["close"]                           # previously closed HTF bar
        return "long" if c > e else "short" if c < e else None

    def pools(self, htf, now):
        """Liquidity pools from CLOSED HTF bars: previous NY day high / low and swing highs / lows."""
        out, k = [], self.cfg.swing_k
        bars = htf[-self.cfg.pool_bars:]
        for i in range(k, len(bars) - k):
            w = bars[i - k:i + k + 1]
            if bars[i]["high"] == max(b["high"] for b in w):
                out.append((bars[i]["high"], "high", "swing high"))
            if bars[i]["low"] == min(b["low"] for b in w):
                out.append((bars[i]["low"], "low", "swing low"))
        today = self.local(now).date()
        days = sorted({self.local(b["time"]).date() for b in htf if self.local(b["time"]).date() < today})
        if days:
            prev = [b for b in htf if self.local(b["time"]).date() == days[-1]]
            out.append((max(b["high"] for b in prev), "high", "previous day high"))
            out.append((min(b["low"] for b in prev), "low", "previous day low"))
        return out

    def emit(self, name, t, **kw):
        ev = {"event": name, "state": self.state, "symbol": self.symbol, "bar_time": t.isoformat(), **kw}
        self.trail.append(ev)
        log.info("%s %s %s %s", self.symbol, name, self.state, kw.get("reason", ""))
        return ev

    @staticmethod
    def to_json(ev) -> str:
        return json.dumps(ev, sort_keys=True, default=str)

    # ---------- detection ----------
    def displacement(self, ltf, atr):
        c, cfg = ltf[-1], self.cfg
        rng = c["high"] - c["low"]
        if rng <= 0 or len(ltf) < 3 or not atr:
            return None
        if abs(c["close"] - c["open"]) / rng < cfg.body_ratio or rng < cfg.range_atr * atr:
            return None
        prev = ltf[-1 - cfg.vol_sma:-1]
        avg = sum(b.get("volume") or 0 for b in prev) / len(prev) if len(prev) == cfg.vol_sma else 0
        if avg > 0:
            if (c.get("volume") or 0) < cfg.vol_mult * avg:
                return None
        elif cfg.volume_required:
            return None
        a, b = ltf[-3], c
        if c["close"] > c["open"] and b["low"] > a["high"]:
            return "long", (a["high"], b["low"])                    # bullish FVG
        if c["close"] < c["open"] and b["high"] < a["low"]:
            return "short", (b["high"], a["low"])                   # bearish FVG
        return None

    def detect(self, ltf, htf, atr):
        d = self.displacement(ltf, atr)
        if not d:
            return None
        direction, zone = d
        c, prev, cfg = ltf[-1], ltf[-2], self.cfg
        pools = self.pools(htf, c["time"])
        bias = self.bias(htf)
        recent = ltf[-cfg.sweep_lookback:]
        if direction == "long":
            lv = [p for p in pools if p[1] == "high" and p[0] < c["close"] and prev["close"] <= p[0] and c["close"] - p[0] >= cfg.alpha * atr]
            if lv and (bias == "long" or (bias is None and not cfg.require_bias)):
                p = max(lv)
                return dict(mode="expansion", direction="long", zone=zone, edge=p[0], invalidation=p[0], pool=p[2], counter_trend=False)
            lw = [p for p in pools if p[1] == "low" and min(b["low"] for b in recent) < p[0] < c["close"]]
            if lw:
                p = max(lw)
                return dict(mode="sweep", direction="long", zone=zone, edge=None, invalidation=min(b["low"] for b in recent),
                            pool=p[2], counter_trend=bias == "short")
        else:
            lv = [p for p in pools if p[1] == "low" and p[0] > c["close"] and prev["close"] >= p[0] and p[0] - c["close"] >= cfg.alpha * atr]
            if lv and (bias == "short" or (bias is None and not cfg.require_bias)):
                p = min(lv)
                return dict(mode="expansion", direction="short", zone=zone, edge=p[0], invalidation=p[0], pool=p[2], counter_trend=False)
            lw = [p for p in pools if p[1] == "high" and max(b["high"] for b in recent) > p[0] > c["close"]]
            if lw:
                p = min(lw)
                return dict(mode="sweep", direction="short", zone=zone, edge=None, invalidation=max(b["high"] for b in recent),
                            pool=p[2], counter_trend=bias == "long")
        return None

    # ---------- the state machine ----------
    def on_bar(self, ltf, htf):
        """Feed after each LTF bar CLOSES. Returns the events of this bar (empty for a bar already processed)."""
        if not ltf:
            return []
        cfg, c = self.cfg, ltf[-1]
        t = c["time"]
        if self.last_ts is not None and t <= self.last_ts:
            return []                                                # idempotent: never re-process a bar
        self.last_ts = t
        close_t = t + timedelta(seconds=cfg.ltf_seconds)
        htfc = self.htf_closed(htf, close_t)
        atr = atr_at(ltf, cfg.atr_period)
        out = []
        day = self.local(t).date()
        if self.state == COOLDOWN and day != self.cool_day:
            self.state, self.s = IDLE, {}                            # new session day: reset
            self.plan = None
        if self.plan:
            out += self._watch_plan(c)
        if atr is None:
            return out
        if self.state == IDLE:
            if self.in_killzone(t):
                d = self.detect(ltf, htfc, atr)
                if d:
                    s = dict(d, impulse_close=c["close"], impulse_time=t, atr=atr, bars=0, bad_closes=0, retest_extreme=None)
                    self.s, self.state = s, IMPULSE
                    out.append(self.emit("SETUP", t, direction=d["direction"], mode=d["mode"], pool=d["pool"],
                                         impulse_close=c["close"], fvg_zone=list(d["zone"]), invalidation=d["invalidation"],
                                         counter_trend=d["counter_trend"]))
            return out
        if self.state in (IMPULSE, RETEST):
            out += self._advance(c, atr, t)
        return out

    def _beyond(self, c):
        s = self.s
        return c["close"] < s["invalidation"] if s["direction"] == "long" else c["close"] > s["invalidation"]

    def _abort(self, name, reason, t):
        ev = self.emit(name, t, direction=self.s["direction"], reason=reason)
        self.state, self.s = IDLE, {}
        return [ev]

    def _advance(self, c, atr, t):
        s, cfg = self.s, self.cfg
        long = s["direction"] == "long"
        s["bars"] += 1
        s["bad_closes"] = s["bad_closes"] + 1 if self._beyond(c) else 0
        if s["bad_closes"] >= cfg.invalid_closes or (self.state == RETEST and self._beyond(c)):
            return self._abort("INVALIDATED", "closed beyond the invalidation level", t)
        if cfg.confirm_in_killzone and not self.in_killzone(t):
            return self._abort("MISSED", "the killzone ended before the confirmation", t)
        if self.state == IMPULSE:
            if s["bars"] > cfg.max_wait_bars:
                return self._abort("MISSED", f"no retest within {cfg.max_wait_bars} bars", t)
            run = (c["close"] - s["impulse_close"]) if long else (s["impulse_close"] - c["close"])
            if run > cfg.chase_atr * s["atr"]:
                return self._abort("MISSED", "price ran away without a retest (anti-chase)", t)
            lo, hi = s["zone"]
            tol = cfg.tol_atr * s["atr"]
            touch = (c["low"] <= hi) if long else (c["high"] >= lo)
            if s["edge"] is not None:
                touch = touch or ((c["low"] <= s["edge"] + tol) if long else (c["high"] >= s["edge"] - tol))
            if touch:
                s["retest_extreme"] = c["low"] if long else c["high"]
                self.state = RETEST
                return [self.emit("RETEST", t, direction=s["direction"], retest_extreme=s["retest_extreme"])]
            return []
        # RETEST: keep the extreme, wait for a closed candle in the direction that reclaims the equilibrium / edge
        s["retest_extreme"] = min(s["retest_extreme"], c["low"]) if long else max(s["retest_extreme"], c["high"])
        lo, hi = s["zone"]
        eq = (lo + hi) / 2.0
        if s["edge"] is not None:
            reclaim = min(eq, s["edge"]) if long else max(eq, s["edge"])
        else:
            reclaim = eq
        ok = (c["close"] > c["open"] and c["close"] > reclaim) if long else (c["close"] < c["open"] and c["close"] < reclaim)
        if not ok:
            return []
        entry = c["close"]
        sl = s["retest_extreme"] - cfg.sl_atr * atr if long else s["retest_extreme"] + cfg.sl_atr * atr
        r = abs(entry - sl)
        if r <= 0 or (long and sl >= entry) or (not long and sl <= entry):
            return self._abort("INVALIDATED", "no valid stop for this entry", t)
        sign = 1 if long else -1
        tps = [entry + sign * m * r for m in cfg.tp_r]
        plan = dict(direction=s["direction"], entry=entry, sl=sl, r=r, tp1=tps[0], tp2=tps[1], tp3=tps[2],
                    early_invalidation=s["invalidation"], mode=s["mode"], counter_trend=s["counter_trend"],
                    risk_scale=0.5 if s["counter_trend"] else 1.0,
                    actions={"tp1": "move the stop to break-even", "tp2": "close 50%", "tp3": "close the rest or the opposing HTF pool"})
        self.plan = dict(plan, done=False, bad=0)
        self.cool_day = self.local(t).date()
        self.state, self.s = COOLDOWN, {}
        return [self.emit("CONFIRMED", t, **plan)]

    def _watch_plan(self, c):
        """After CONFIRMED: one early-invalidation alert if the closes go beyond the level (never a second one)."""
        p = self.plan
        if p["done"]:
            return []
        long = p["direction"] == "long"
        beyond = c["close"] < p["early_invalidation"] if long else c["close"] > p["early_invalidation"]
        p["bad"] = p["bad"] + 1 if beyond else 0
        if p["bad"] >= 1:
            p["done"] = True
            return [self.emit("INVALIDATED", c["time"], direction=p["direction"],
                              reason="closed beyond the invalidation level after entry: early exit")]
        return []
