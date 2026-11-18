"""Offline checks for market_state.py and the IFVG / mitigation-block helpers in structure.py (synthetic candles)."""
import math

import market_state as ms
import structure as st
from test_structure import bars_from_closes, raw

FILL = [(100, 100.5, 99.5, 100)] * 20
GAP = FILL + [(100, 100.5, 99.5, 100.2), (100.2, 103, 100.1, 102.8), (102.8, 103.5, 102, 103)]   # bull FVG 100.5-102


def osc(n, amp=1.0, base=100.0, wick=0.4):
    rows, prev = [], base
    for i in range(n):
        c = base + amp * math.sin(i / 2.0)
        rows.append((prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return rows


def test_ifvg_is_a_gap_that_price_closed_through_and_it_can_be_invalidated():
    assert st.ifvgs(raw(GAP)) == []                                          # an intact gap is not inverse
    through = raw(GAP + [(103, 103.2, 98.5, 100)])                           # a close below the bull gap's low
    g = st.ifvgs(through)
    assert g[0]["kind"] == "IFVG" and g[0]["side"] == "bear" and (g[0]["low"], g[0]["high"]) == (100.5, 102) and g[0]["status"] == "valid"
    tested = st.ifvgs(raw(GAP + [(103, 103.2, 98.5, 100), (100, 101, 99.9, 100.3)]))
    assert tested[0]["status"] == "tested"
    back = st.ifvgs(raw(GAP + [(103, 103.2, 98.5, 100), (100, 101, 99.9, 100.3), (100.3, 102.8, 100.2, 102.5)]))
    assert back[0]["status"] == "invalid"                                    # price closed back through it


def test_mitigation_block_needs_a_failed_swing_and_a_displaced_break():
    closes = [100] * 20 + [101, 102, 103, 104, 103, 102, 101, 100.6, 101.5, 102.5, 103.4, 102.5, 101.5, 100.5, 98.5, 98.4, 98.3]
    mb = st.mitigation_blocks(bars_from_closes(closes))
    assert len(mb) == 1 and mb[0]["side"] == "bear" and mb[0]["kind"] == "MITIGATION" and mb[0]["status"] == "valid"
    assert mb[0]["high"] == 103.5 and mb[0]["idx"] == 30                     # the failed lower high (103.5 < 104.1)
    mirror = [200 - c for c in closes]                                       # the same picture upside down
    mm = st.mitigation_blocks(bars_from_closes(mirror))
    assert len(mm) == 1 and mm[0]["side"] == "bull"
    no_break = closes[:-3] + [100.6, 100.7, 100.8]                           # no close below the swing low: nothing to mitigate
    assert st.mitigation_blocks(bars_from_closes(no_break)) == []
    higher_high = [100] * 20 + [101, 102, 103, 104, 103, 102, 101, 100.6, 102, 103, 104.8, 103, 102, 100.5, 98.5]
    assert all(b["high"] != 104.9 for b in st.mitigation_blocks(bars_from_closes(higher_high)))   # a higher high is not a failed swing


def test_state_needs_enough_candles_and_is_never_a_signal():
    r = ms.classify(raw(osc(30)))
    assert r["state"] == "WAITING FOR CONFIRMATION" and "closed candles" in r["detail"]
    assert set(ms.STATES) >= {"TRENDING", "RANGING", "ACCUMULATION", "DISTRIBUTION", "LIQUIDITY SWEEP", "DISPLACEMENT", "RETEST",
                              "REVERSAL", "WAITING FOR CONFIRMATION"}
    assert "BUY" not in ms.line(r) and "SELL" not in ms.line(r)


def test_ranging_trending_displacement_sweep_and_reversal():
    assert ms.classify(raw(osc(80)))["state"] == "RANGING"
    rows, prev = [], 100.0
    for i in range(90):                                                    # a staircase: 5 up bars, 2 pullback bars
        c = prev + 0.5 if i % 7 < 5 else prev - 0.45
        rows.append((prev, max(prev, c) + 0.7, min(prev, c) - 0.7, c))
        prev = c
    t = ms.classify(raw(rows))
    assert t["state"] == "TRENDING" and t["trend"] == "up" and t["adx"] >= 25
    base = osc(80)
    last = base[-1][3]
    assert ms.classify(raw(base + [(last, last + 3, last - 0.1, last + 2.9)]))["state"] == "DISPLACEMENT"
    hi = max(r[1] for r in base)
    swept = base + [(last, hi + 0.5, last - 0.2, last + 0.1)]
    assert ms.classify(raw(swept))["state"] == "LIQUIDITY SWEEP"           # a sweep alone is not a reversal
    rev = ms.classify(raw(swept + [(last + 0.1, last + 0.2, last - 3.7, last - 3.5)]))
    assert rev["state"] == "REVERSAL" and "structure break" in rev["detail"]


def test_retest_when_price_is_back_in_a_fresh_gap():
    rows = [(100, 100.5, 99.5, 100)] * 60 + [(100, 100.5, 99.5, 100.2), (100.2, 103, 100.1, 102.8), (102.8, 103.5, 102, 103)]
    rows += [(103, 103.1, 102.9, 103)] * 6                                  # a quiet pause above the gap
    p = 103.0
    for _ in range(10):                                                     # a slow drift back down into the gap 100.5-102
        rows.append((p, p + 0.05, p - 0.25, p - 0.2))
        p -= 0.2
    r = ms.classify(raw(rows))
    assert r["state"] == "RETEST" and r["zone"]["kind"] == "FVG" and "100.5-102" in r["detail"]
    assert ms.classify(raw(rows[:-6]))["state"] != "RETEST"                 # still above the gap: nothing to retest yet
