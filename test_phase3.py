import volatility_engine as ve
import correlation_engine as ce
import unified_engine as ue
from test_unified_engine import NOW, vote_all, main


def bars(n=150, rng=1.0, last=None):
    out, p = [], 100.0
    for i in range(n):
        out.append({"open": p, "high": p + rng / 2, "low": p - rng / 2, "close": p + (0.1 if i % 2 else -0.1)})
        p = out[-1]["close"]
    if last:
        out[-1] = dict(out[-1], high=out[-1]["close"] + last / 2, low=out[-1]["close"] - last / 2)
    return out


def test_normal_regime_is_ok():
    r = ve.assess(bars(), spread=0.05, risk=2.0)
    assert r["ok"] and r["regime"] == "normal"


def test_extreme_regime_vetoes():
    b = bars()
    for i in range(-20, 0):
        b[i] = dict(b[i], high=b[i]["close"] + 4, low=b[i]["close"] - 4)
    r = ve.assess(b)
    assert not r["ok"] and r["regime"] in ("high", "extreme")


def test_abnormal_last_candle_vetoes():
    r = ve.assess(bars(last=8.0))
    assert not r["ok"] and any("abnormal" in p for p in r["problems"])


def test_wide_spread_vetoes_but_unknown_spread_does_not():
    assert not ve.assess(bars(), spread=1.0, risk=2.0)["ok"]
    r = ve.assess(bars(), spread=None, risk=2.0)
    assert r["ok"] and any("unknown" in n for n in r["notes"])


def test_too_few_candles_is_unknown_not_a_veto():
    r = ve.assess(bars(10))
    assert r["ok"] and r["regime"] == "unknown"


def test_dynamic_sl_advice_in_high_regime():
    assert ve.min_sl_distance(2.0, "high") == 3.0


def test_correlated_indices_are_one_risk():
    assert ce.block_reason("US30", "buy", [("US100", "buy")]).startswith("correlated with an active US100 BUY")
    assert ce.block_reason("US30", "sell", [("US100", "buy")]) == ""
    assert ce.note("US30", "sell", [("US100", "buy")])


def test_inverse_pairs_and_independent_markets():
    assert ce.block_reason("USDJPY", "sell", [("EURUSD", "buy")])        # USDJPY sell == EURUSD buy risk
    assert ce.block_reason("USDJPY", "buy", [("EURUSD", "buy")]) == ""
    assert ce.block_reason("BTCUSD", "buy", [("EURUSD", "buy")]) == ""


def test_corr_gate_switch(monkeypatch):
    monkeypatch.setenv("CORR_GATE", "0")
    assert ce.block_reason("US30", "buy", [("US100", "buy")]) == ""


def test_measured_correlation():
    a = [100 + i + (i % 3) for i in range(40)]
    assert ce.measured(a, a) > 0.99
    assert ce.measured([1, 2], [1, 2]) is None


def test_second_correlated_confirmation_is_blocked():
    ue._save(dict(ue._load(), active={"x": {"market": "US100", "side": "buy", "ts": NOW, "expires": NOW + 3600, "status": "ACTIVE",
                                            "sl": 1, "early_exit": None}}))
    vote_all(); main()
    d = ue._load()
    assert ue._corr_block(d, "US30", "buy", NOW)
    assert ue._corr_block(d, "XAUUSD", "buy", NOW) == ""
