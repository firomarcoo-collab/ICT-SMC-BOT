import copy
from datetime import datetime, timedelta, timezone

import pytest

import trade_analysis as ta
import unified_engine as ue

END = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
NOW = int(END.timestamp())
CHECKS = {"liquidity": True, "bos_mss": True, "fvg_ob": True, "retest": True, "confirm": True}


def path(steps, start):
    p, out = start, []
    for s in steps:
        o, p = p, p + s
        out.append((o, p))
    return out


def bars(pairs, tf, wick=0.2, end=END):
    t0 = end - timedelta(seconds=tf * len(pairs))
    return [{"time": t0 + timedelta(seconds=tf * i), "open": o, "high": max(o, c) + wick, "low": min(o, c) - wick, "close": c, "volume": 1}
            for i, (o, c) in enumerate(pairs)]


def saw(n, up, down, ups=4, downs=2):
    steps = []
    while len(steps) < n:
        steps += [up] * ups + [-down] * downs
    return steps[:n]


def data(mirror=False, h1_bars=260):
    """A clean up-trend: H4 / H1 saw-tooth rises; M15 run-up, dip, impulse, pullback to ~62% of the leg, one bullish bar."""
    h4 = bars(path(saw(120, 6, 3), 1500), 14400, 1)
    h1 = bars(path(saw(h1_bars, 2, 1, 5, 3), 1700), 3600, 0.5)
    steps = saw(60, 0.8, 0.5, 4, 2) + [-1.0] * 6 + [1.0] * 12 + [-1.25] * 6 + [0.4]
    m15 = bars(path(steps, 1990), 900, 0.15)
    if mirror:
        flip = lambda bs: [dict(b, open=4000 - b["open"], close=4000 - b["close"], high=4000 - b["low"], low=4000 - b["high"]) for b in bs]
        return flip(m15), flip(h1), flip(h4)
    return m15, h1, h4


PLAN = {"entry": 2011.0, "sl": 2010.0, "tp1": 2012.0, "tp2": 2013.0, "early_exit": 2009.5}
VOTES = {"smc": {"side": "buy"}, "scalper": {"side": "buy"}, "pulse": {"side": "buy"}}


def run(plan=None, checks=None, votes=None, side="buy", d=None, **kw):
    m15, h1, h4 = d or data()
    return ta.analyze("XAUUSD", side, plan or PLAN, CHECKS if checks is None else checks, VOTES if votes is None else votes, m15, h1, h4, NOW, **kw)


# ---- the analysis and the plan ------------------------------------------------------------------------------------------------------
def test_good_setup_scores_above_80_and_gives_a_full_plan():
    a = run()
    assert a["complete"] and a["final"] and a["score"] >= 80 and a["why_not"] == []
    assert a["score"] == round(sum(a["parts"].values()), 1)
    p = a["plan"]
    assert (p["side"], p["entry"], p["sl"], p["tp1"], p["tp2"]) == ("BUY", 2011.0, 2010.0, 2012.0, 2013.0)
    assert p["rr_tp1"] == pytest.approx(1.0) and p["rr_tp2"] == pytest.approx(2.0) and p["rr_tp3"] == pytest.approx(3.0)
    assert p["tp3"] == pytest.approx(2014.0) and p["tp3_kind"] == "3R extension" and a["reasons"] and a["cancel"]


def test_sell_plan_mirrors_the_buy_plan():
    d = data(mirror=True)
    plan = {"entry": 4000 - 2011.0, "sl": 4000 - 2010.0, "tp1": 4000 - 2012.0, "tp2": 4000 - 2013.0, "early_exit": 4000 - 2009.5}
    a = run(plan=plan, side="sell", d=d, votes={k: {"side": "sell"} for k in VOTES})
    assert a["final"] and a["plan"]["side"] == "SELL"
    assert a["plan"]["tp3"] == pytest.approx(1986.0) and a["plan"]["tp1"] < a["plan"]["entry"] < a["plan"]["sl"]


def test_tp3_is_the_next_resistance_when_one_is_in_range():
    plan = dict(PLAN, sl=2010.5, tp1=2011.5, tp2=2012.0)                       # risk 0.5: TP2 2012, the resistance 2013.15 lies beyond
    a = run(plan=plan)
    assert a["plan"]["tp3_kind"] == "next resistance" and a["plan"]["tp3"] == pytest.approx(2013.15, abs=0.2)


def test_sl_is_kept_never_tightened():
    a = run()
    assert a["plan"]["sl"] == PLAN["sl"] and a["plan"]["entry"] == PLAN["entry"]


def test_weak_confirmation_is_not_final():
    a = run(checks={k: False for k in CHECKS})
    assert a["complete"] and a["score"] < 80 and not a["final"] and "below the minimum" in a["why_not"][0]


def test_rr_below_1_to_2_is_not_final():
    a = run(plan=dict(PLAN, tp2=2012.5))
    assert not a["final"] and any("R:R to TP2" in w for w in a["why_not"])


def test_missing_bars_means_no_verdict():
    a = run(d=data(h1_bars=150))                                               # the H1 SMA / EMA 200 cannot be computed
    assert not a["complete"] and a["score"] is None and not a["final"] and "H1" in a["problems"][0]
    assert "no verdict" in ta.format_report(a)


def test_stale_data_means_no_verdict():
    m15, h1, h4 = data()
    a = ta.analyze("XAUUSD", "buy", PLAN, CHECKS, VOTES, m15, h1, h4, NOW + 3 * 3600)
    assert not a["complete"] and "stale" in a["problems"][0] and not a["final"]


def test_order_flow_is_never_estimated():
    a = run()
    assert "unavailable" in a["indicators"]["order_flow"]
    base = a["score"]
    assert run(order_flow={"real": False, "delta": -50})["score"] == base            # a non-real feed earns / costs nothing
    against = run(order_flow={"real": True, "delta": -50})
    assert not against["final"] and any("order flow" in w for w in against["why_not"])
    assert run(order_flow={"real": True, "delta": 50})["final"]


def test_the_three_strategies_are_rated_and_not_modified():
    votes = {"smc": {"side": "buy", "entry": 1}, "scalper": {"side": "buy"}, "pulse": {"side": "sell"}}
    before, plan_before = copy.deepcopy(votes), copy.deepcopy(PLAN)
    a = run(votes=votes)
    assert votes == before and PLAN == plan_before                                   # nothing is changed in their signals
    assert a["ratings"]["smc"].startswith("ALIGNED") and a["ratings"]["pulse"].startswith("AGAINST")
    assert "Strategies:" in ta.format_report(a)


def test_report_text_has_every_field():
    txt = ta.format_report(run())
    for word in ("Confidence Score", "Plan: BUY", "TP1", "TP2", "TP3", "Risk/Reward", "Why:", "Cancel if:", "Order flow:", "Parts:"):
        assert word in txt


# ---- indicators ----------------------------------------------------------------------------------------------------------------------
def test_indicators():
    bs = bars([(i, i + 1) for i in range(60)], 900)
    assert ta.sma([b["close"] for b in bs], 50) == pytest.approx(sum(range(11, 61)) / 50)
    assert ta.sma([1, 2], 50) is None
    mid, up, lo, pb, w = ta.bollinger(bs)
    assert lo < mid < up and 0.5 < pb <= 1.2 and w > 0
    assert ta.bollinger(bs[:5]) is None
    mv = ta.ma_view(bars([(i, i + 1) for i in range(220)], 3600), "up")
    assert mv["price_ok"] and mv["cross_ok"] and mv["sma200"] is not None
    assert ta.ma_view(bars([(i, i + 1) for i in range(30)], 3600), "up")["cross_ok"] is None


def test_support_resistance_levels_merge_close_pivots():
    m15, _, _ = data()
    levels = ta.sr_levels(m15, 3)
    assert levels and all(levels[i]["price"] <= levels[i + 1]["price"] for i in range(len(levels) - 1))
    assert {lv["kind"] for lv in levels} <= {"support", "resistance"}


# ---- the gate inside the unified engine ------------------------------------------------------------------------------------------
def _confirmed_state(market="XAUUSD"):
    NOWU = 1_800_000_000
    setup = {"side": "buy", "entry": 2011.0, "sl": 2010.0, "tp1": 2012.0, "tp": 2013.0, "score100": 85, "bar_time": NOWU - 1800,
             "early_exit": 2009.5, "score_parts": {"displacement": 12}, "trends": {"h4": "up", "h1": "up"}}
    for n in ("SMC Prime", "ICT Session Scalper", "Order Flow Pulse (estimate)"):
        ue.on_event(market, {"name": n, "direction": "long", "event": "SIGNAL", "entry": 2011.0, "sl": 2010.0, "tp1": 2012.0, "tp2": 2013.0,
                             "zone": (2010, 2012), "bar_time": str(NOWU - 1500), "fib_ok": True}, "up", NOWU - 120)
    ue.record_main(market, setup, {}, NOWU - 300, "sidA", CHECKS, "dec")
    return NOWU


def test_unified_waits_while_the_analysis_says_no_and_confirms_once_it_says_yes():
    NOWU = _confirmed_state()
    assert ue.run_market("XAUUSD", NOWU, gate=lambda r: (False, "", "analysis score 61/100 is below the minimum 80")) is None
    d = ue.decide("XAUUSD", NOWU)
    assert d["decision"] == "CONFIRMED BUY"                                           # the unified rules are met...
    assert ue._load()["decision"]["XAUUSD"]["decision"] == "WAIT"                     # ...but the stored decision is WAIT
    assert "Professional Trade Analysis" in ue._load()["decision"]["XAUUSD"]["reasons"][0]
    assert ue._load()["sent"] == {}                                                   # nothing was marked as sent: it is re-checked next scan
    first = ue.run_market("XAUUSD", NOWU + 60, gate=lambda r: (True, "PROFESSIONAL TRADE ANALYSIS\nConfidence Score: 88/100", ""))
    assert first and "PROFESSIONAL TRADE ANALYSIS" in first["text"] and first["text"].startswith("KENAN TRADER AI - CONFIRMED BUY")
    assert ue.run_market("XAUUSD", NOWU + 120, gate=lambda r: (True, "x", "")) is None  # one signal per opportunity, even after a restart


def test_a_crashing_analysis_gate_means_wait():
    NOWU = _confirmed_state()
    def boom(r):
        raise RuntimeError("feed down")
    assert ue.run_market("XAUUSD", NOWU, gate=boom) is None
    assert "analysis engine error" in ue._load()["decision"]["XAUUSD"]["reasons"][0]


def test_bot_gate_uses_closed_candles_and_the_same_offsets(monkeypatch):
    import ict_alerts as ia
    m15, h1, h4 = data()
    tfs = {"15m": m15, "1h": h1, "4h": h4}
    seen = []
    monkeypatch.setattr(ia, "get_candles", lambda inst, tf="5m": seen.append(tf) or tfs[tf])
    res = {"market": "XAUUSD", "side": "buy", "votes": VOTES,
           "main": {"entry": 2011.0, "sl": 2010.0, "tp1": 2012.0, "tp2": 2013.0, "early_exit": 2009.5, "checks": CHECKS}}
    ok, text, why = ia.analysis_gate("XAUUSD", NOW, {})(res)
    assert ok and "Confidence Score" in text and set(seen) == {"15m", "1h", "4h"}
    ok2, text2, why2 = ia.analysis_gate("XAUUSD", NOW + 4 * 3600, {})(res)             # the same feed, 4 hours later: stale
    assert not ok2 and "stale" in why2


def test_analysis_can_be_switched_off(monkeypatch):
    assert ta.enabled()
    monkeypatch.setenv("ANALYSIS", "0")
    assert not ta.enabled()
