"""ICT SMC bot: Python port of ICT_SMC_AutoTrader.mq5, runs through MetaApi.

The signal logic (find_setup) is pure Python with no network access, so it can
be tested offline. Only run() talks to MetaApi.

Environment variables:
    METAAPI_TOKEN        MetaApi API token (required)
    METAAPI_ACCOUNT_ID   MetaApi account id of your MT5 account (required)
    SYMBOL               symbol name exactly as your broker writes it (default EURUSD)
    DRY_RUN              1 = only log signals, 0 = send pending orders (default 1)
"""
import asyncio
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("ict_smc_bot")


@dataclass
class Config:
    risk_percent: float = 0.5
    rr: float = 2.0
    lookback: int = 10
    atr_period: int = 14
    displacement_atr: float = 0.8
    sl_buffer_atr: float = 0.15
    magic: int = 6112026
    symbol: str = "EURUSD"
    timeframe: str = "5m"
    timeframe_seconds: int = 300
    poll_seconds: int = 10
    dry_run: bool = True
    # classical confirmations (used by ict_alerts.py and backtest.py through classic.py)
    ema_fast: int = 50
    ema_slow: int = 200
    fib_shallow: float = 0.5
    fib_deep: float = 0.786
    sr_pivot: int = 3          # bars on each side of a swing high/low
    sr_lookback: int = 120     # how far back swing levels are collected
    sr_tol_atr: float = 0.5    # "near a level" means within this many ATR
    leg_lookback: int = 60     # how far back the classical setup looks for the swing
    leg_span: int = 40         # bars before the swing used to find the leg's start
    leg_min_atr: float = 2.0   # smallest leg worth trading, in ATR
    min_score: int = 0         # ICT alerts need at least this many classical confirmations
    classic_enabled: bool = True


# ===== Signal logic (same rules as the MQL5 EA) =====

def to_series(candles):
    """Candles sorted newest first, like ArraySetAsSeries(r, true): r[0] is the forming bar."""
    return sorted(candles, key=lambda c: c["time"].timestamp(), reverse=True)


def get_atr(r, period):
    total = 0.0
    for i in range(1, period + 1):
        tr = max(
            r[i]["high"] - r[i]["low"],
            abs(r[i]["high"] - r[i + 1]["close"]),
            abs(r[i]["low"] - r[i + 1]["close"]),
        )
        total += tr
    return total / period


def find_setup(r, cfg, ask, bid, digits=5):
    """Return {'side','entry','sl','tp'} or None. r is newest first."""
    need = cfg.lookback + cfg.atr_period + 5
    if len(r) < need:
        return None

    atr = get_atr(r, cfg.atr_period)
    if atr <= 0:
        return None

    old_low = r[3]["low"]
    old_high = r[3]["high"]
    for i in range(4, cfg.lookback + 3):
        old_low = min(old_low, r[i]["low"])
        old_high = max(old_high, r[i]["high"])

    # Liquidity sweep
    sweep_low = r[2]["low"] < old_low and r[2]["close"] > old_low
    sweep_high = r[2]["high"] > old_high and r[2]["close"] < old_high

    # Displacement and structure break
    bullish = (
        r[1]["close"] > r[1]["open"]
        and r[1]["close"] > r[2]["high"]
        and r[1]["close"] - r[1]["open"] >= atr * cfg.displacement_atr
    )
    bearish = (
        r[1]["close"] < r[1]["open"]
        and r[1]["close"] < r[2]["low"]
        and r[1]["open"] - r[1]["close"] >= atr * cfg.displacement_atr
    )

    # Fair Value Gap
    bull_fvg = r[1]["low"] > r[3]["high"]
    bear_fvg = r[1]["high"] < r[3]["low"]

    # Order Block candidate
    bear_ob = r[2]["close"] < r[2]["open"]
    bull_ob = r[2]["close"] > r[2]["open"]

    if sweep_low and bullish and bull_fvg and bear_ob:
        zone_low = max(r[3]["high"], r[2]["low"])
        zone_high = min(r[1]["low"], r[2]["open"])
        if zone_low < zone_high:
            entry = (zone_low + zone_high) / 2
            sl = r[2]["low"] - atr * cfg.sl_buffer_atr
            tp = entry + (entry - sl) * cfg.rr
            if entry < ask and sl < entry:
                return {"side": "buy", "entry": round(entry, digits),
                        "sl": round(sl, digits), "tp": round(tp, digits)}

    if sweep_high and bearish and bear_fvg and bull_ob:
        zone_low = max(r[1]["high"], r[2]["open"])
        zone_high = min(r[3]["low"], r[2]["high"])
        if zone_low < zone_high:
            entry = (zone_low + zone_high) / 2
            sl = r[2]["high"] + atr * cfg.sl_buffer_atr
            tp = entry - (sl - entry) * cfg.rr
            if entry > bid and sl > entry:
                return {"side": "sell", "entry": round(entry, digits),
                        "sl": round(sl, digits), "tp": round(tp, digits)}

    return None


def calc_lot(risk_money, loss_per_lot, step, min_lot, max_lot):
    """Same rounding rules as GetLot: floor to the volume step, 0 if below min lot."""
    if loss_per_lot <= 0 or step <= 0:
        return 0.0
    lot = int((risk_money / loss_per_lot) / step) * step
    if lot < min_lot:
        return 0.0
    return round(min(lot, max_lot), 4)


# ===== MetaApi side =====

async def loss_per_lot_in_account_currency(conn, spec, account_ccy, entry, sl):
    """Loss of 1 lot between entry and sl, in account currency; None if it cannot be converted."""
    loss = abs(entry - sl) * spec.get("contractSize", 0)
    profit_ccy = spec.get("profitCurrency")
    if loss <= 0 or not profit_ccy:
        return None
    if profit_ccy == account_ccy:
        return loss
    try:
        price = await conn.get_symbol_price(f"{profit_ccy}{account_ccy}")
        return loss * price["bid"]
    except Exception:
        pass
    try:
        price = await conn.get_symbol_price(f"{account_ccy}{profit_ccy}")
        return loss / price["ask"]
    except Exception:
        return None


async def has_exposure(conn, cfg):
    positions = await conn.get_positions()
    orders = await conn.get_orders()
    for item in list(positions) + list(orders):
        if item.get("symbol") == cfg.symbol and item.get("magic") == cfg.magic:
            return True
    return False


async def check_signal(conn, account, cfg, spec, price, forming_start):
    if await has_exposure(conn, cfg):
        return

    candles = await account.get_historical_candles(cfg.symbol, cfg.timeframe, None, 60)
    r = to_series(candles)
    if not r:
        return

    # The MQL5 logic needs r[0] to be the bar that is still forming.
    # If the broker data stops at the last closed bar, add a placeholder.
    if int(r[0]["time"].timestamp()) < forming_start:
        placeholder = dict(r[0])
        placeholder["time"] = type(r[0]["time"]).fromtimestamp(forming_start, tz=r[0]["time"].tzinfo)
        r.insert(0, placeholder)

    digits = spec.get("digits", 5)
    setup = find_setup(r, cfg, price["ask"], price["bid"], digits)
    if not setup:
        return

    info = await conn.get_account_information()
    risk_money = info["equity"] * cfg.risk_percent / 100.0
    loss = await loss_per_lot_in_account_currency(
        conn, spec, info["currency"], setup["entry"], setup["sl"])
    if loss is None:
        log.warning("Cannot convert loss to account currency, skipping %s", setup)
        return

    lot = calc_lot(risk_money, loss, spec.get("volumeStep", 0.01),
                   spec.get("minVolume", 0.01), spec.get("maxVolume", 100))
    if lot <= 0:
        log.warning("Lot size is 0 (risk too small for min lot), skipping %s", setup)
        return

    log.info("SIGNAL %s lot=%s %s", setup["side"].upper(), lot, setup)
    if cfg.dry_run:
        log.info("DRY_RUN is on: no order sent")
        return

    opts = {"magic": cfg.magic, "comment": "ICT SMC " + setup["side"].upper()}
    if setup["side"] == "buy":
        res = await conn.create_limit_buy_order(
            cfg.symbol, lot, setup["entry"], setup["sl"], setup["tp"], opts)
    else:
        res = await conn.create_limit_sell_order(
            cfg.symbol, lot, setup["entry"], setup["sl"], setup["tp"], opts)
    log.info("Order result: %s", res)


async def run(cfg):
    from metaapi_cloud_sdk import MetaApi  # imported here so the logic can be tested without it

    api = MetaApi(os.environ["METAAPI_TOKEN"])
    account = await api.metatrader_account_api.get_account(os.environ["METAAPI_ACCOUNT_ID"])
    if account.state != "DEPLOYED":
        await account.deploy()
    await account.wait_connected()

    conn = account.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()
    spec = await conn.get_symbol_specification(cfg.symbol)
    log.info("ICT SMC Bot Started on %s (dry_run=%s)", cfg.symbol, cfg.dry_run)

    last_bar = None
    while True:
        try:
            price = await conn.get_symbol_price(cfg.symbol)
            ts = int(price["time"].timestamp())
            forming_start = ts - ts % cfg.timeframe_seconds
            if forming_start != last_bar:
                last_bar = forming_start
                await check_signal(conn, account, cfg, spec, price, forming_start)
        except Exception:
            log.exception("Loop error, will retry")
        await asyncio.sleep(cfg.poll_seconds)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config(
        symbol=os.environ.get("SYMBOL", "EURUSD"),
        dry_run=os.environ.get("DRY_RUN", "1") != "0",
    )
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
