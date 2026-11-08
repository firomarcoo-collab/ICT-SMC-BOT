"""Offline checks for the signal logic. Run: python test_logic.py"""
from datetime import datetime, timedelta, timezone

from ict_smc_bot import Config, calc_lot, find_setup, to_series

cfg = Config()


def make_candles(mirror=False):
    """Synthetic M5 candles (oldest first) that form a textbook buy setup, or its mirror for a sell."""
    start = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
    rows = [(100.4, 101.0, 100.0, 100.6)] * 40  # flat base, range 1.0
    rows += [
        (100.6, 101.0, 100.0, 100.6),  # r[3]: old bar, defines the gap edge
        (101.5, 101.6, 99.8, 100.2),   # r[2]: sweeps the low, bearish order block
        (101.2, 103.0, 101.1, 102.8),  # r[1]: displacement up, leaves a gap above r[3]
        (102.8, 103.0, 102.5, 102.9),  # r[0]: forming bar
    ]
    out = []
    for n, (o, h, l, c) in enumerate(rows):
        if mirror:
            o, h, l, c = 200 - o, 200 - l, 200 - h, 200 - c
        out.append({"time": start + timedelta(minutes=5 * n),
                    "open": o, "high": h, "low": l, "close": c})
    return out


def test_buy():
    r = to_series(make_candles())
    s = find_setup(r, cfg, ask=102.9, bid=102.88)
    assert s and s["side"] == "buy", s
    assert s["sl"] < s["entry"] < s["tp"]
    assert abs((s["tp"] - s["entry"]) - cfg.rr * (s["entry"] - s["sl"])) < 1e-4


def test_sell():
    r = to_series(make_candles(mirror=True))
    s = find_setup(r, cfg, ask=97.12, bid=97.1)
    assert s and s["side"] == "sell", s
    assert s["tp"] < s["entry"] < s["sl"]


def test_no_signal_on_flat_market():
    flat = [{"time": datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(minutes=5 * n),
             "open": 100.4, "high": 101.0, "low": 100.0, "close": 100.6} for n in range(60)]
    assert find_setup(to_series(flat), cfg, ask=100.6, bid=100.58) is None


def test_not_enough_candles():
    assert find_setup(to_series(make_candles())[:10], cfg, ask=102.9, bid=102.88) is None


def test_lot():
    # risk 50, loss per lot 100 -> 0.5 lot, step 0.01
    assert calc_lot(50, 100, 0.01, 0.01, 100) == 0.5
    # below the minimum lot -> 0
    assert calc_lot(0.5, 100, 0.01, 0.01, 100) == 0.0
    # capped at the max lot
    assert calc_lot(10000, 1, 0.01, 0.01, 5) == 5.0


def test_live_orders_cannot_be_switched_on():
    import inspect
    import os
    import ict_smc_bot
    src = inspect.getsource(ict_smc_bot)
    assert "create_limit" not in src and "create_market" not in src  # no order call exists
    captured = {}
    saved_run, saved_env = ict_smc_bot.run, os.environ.get("DRY_RUN")
    ict_smc_bot.run = lambda cfg: captured.setdefault("cfg", cfg)
    ict_smc_bot.asyncio.run = lambda x: None
    os.environ["DRY_RUN"] = "0"  # the old switch is ignored now
    try:
        ict_smc_bot.main()
    finally:
        ict_smc_bot.run = saved_run
        if saved_env is None:
            os.environ.pop("DRY_RUN", None)
        else:
            os.environ["DRY_RUN"] = saved_env
    assert captured["cfg"].dry_run is True


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
