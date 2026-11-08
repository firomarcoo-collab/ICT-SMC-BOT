"""Offline checks for events.py. Run: python test_events.py"""
from events import find_events
from ict_smc_bot import Config, to_series
from test_logic import make_candles

cfg = Config()


def series(mirror=False, big_gap=True):
    c = make_candles(mirror)
    if big_gap:  # widen the gap: make the push candle's low sit well above r[3] high
        k = len(c) - 2
        if mirror:
            c[k] = dict(c[k], high=98.0)
        else:
            c[k] = dict(c[k], low=102.0)
    return to_series(c)


def test_bullish_events():
    kinds = {(e["kind"], e["side"]) for e in find_events(series(), cfg, 2)}
    assert ("fvg", "buy") in kinds and ("ob", "buy") in kinds, kinds


def test_tiny_gap_is_ignored():
    kinds = {e["kind"] for e in find_events(series(big_gap=False), cfg, 2)}
    assert "fvg" not in kinds and "ob" in kinds


def test_mirror_gives_sell_events():
    kinds = {(e["kind"], e["side"]) for e in find_events(series(mirror=True), cfg, 2)}
    assert ("fvg", "sell") in kinds and ("ob", "sell") in kinds, kinds


def test_kinds_filter_and_short_history():
    r = series()
    only = find_events(r, cfg, 2, kinds=("fvg",))
    assert only and all(e["kind"] == "fvg" for e in only)
    assert find_events(r[:10], cfg, 2) == []
    assert find_events(r, cfg, 2, kinds=()) == []


def test_sweep_on_last_closed_bar():
    c = make_candles()[:-2]            # ends with the bar that sweeps the lows
    c.append(dict(c[-1], time=c[-1]["time"] + (c[-1]["time"] - c[-2]["time"])))  # forming bar
    ev = find_events(to_series(c), cfg, 2, kinds=("sweep",))
    # the example sweep candle also pokes above the highs, so both sides can show up
    assert "buy" in {e["side"] for e in ev}, ev
    mirror = make_candles(True)[:-2]
    mirror.append(dict(mirror[-1], time=mirror[-1]["time"] + (mirror[-1]["time"] - mirror[-2]["time"])))
    assert "sell" in {e["side"] for e in find_events(to_series(mirror), cfg, 2, kinds=("sweep",))}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
