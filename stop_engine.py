"""Stop Loss Engine (signals only: it calculates and advises, it NEVER places or moves an order).

Rules (all structural, never at a zone edge or a fixed number of points):
  1. Anchor: BUY below the sweep low / swing low that preceded the displacement; SELL above the sweep high / swing high.
  2. Dynamic buffer behind the anchor: ATR multiple for the asset class + the expected spread.
  3. Two tiers: Early Invalidation (a full candle close beyond the FVG/IFVG) warns early; Hard SL is the only reference for lot size.
  4. Risk scaling: sweep done AND with the higher timeframe = 100% of the risk, otherwise 50% (the stop stays as wide).
  5. Net R:R to TP1 (after spread / commission) below 1:2 = flagged "unbalanced risk" and rejected.
  6. Break-even advice (entry + cost) when TP1 is touched or 1.5R is reached.
Nothing here promises a result. Pure functions: no files, no network, no clock."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

MAX_RISK_PCT = 2.0        # absolute ceiling for the risk of one trade (% of the balance)
MIN_NET_RR = 2.0
BE_R = 1.5


class AssetClass(str, Enum):
    FOREX_MAJOR = "forex_major"
    JPY_CROSS = "jpy_cross"
    GOLD = "gold"
    INDEX = "index"
    CRYPTO = "crypto"


@dataclass(frozen=True)
class ClassParams:
    atr_mult: float            # buffer behind the structural anchor, in ATR
    spread_weight: float       # how many expected spreads are added to the buffer
    max_spread_atr: float      # a spread wider than this many ATR is a spike: reject
    max_stop_atr: float        # a structural stop farther than this many ATR is "too far": reject


PARAMS: dict[AssetClass, ClassParams] = {
    AssetClass.FOREX_MAJOR: ClassParams(0.50, 1.0, 0.20, 4.0),
    AssetClass.JPY_CROSS: ClassParams(0.70, 1.0, 0.20, 4.5),
    AssetClass.GOLD: ClassParams(0.70, 1.0, 0.25, 4.5),
    AssetClass.INDEX: ClassParams(0.70, 1.0, 0.25, 4.5),
    AssetClass.CRYPTO: ClassParams(0.70, 1.0, 0.25, 4.5),
}

_SYMBOLS = {"XAUUSD": AssetClass.GOLD, "BTCUSD": AssetClass.CRYPTO,
            "US100": AssetClass.INDEX, "NAS100": AssetClass.INDEX, "US30": AssetClass.INDEX, "DAX40": AssetClass.INDEX}


def classify(symbol: str) -> AssetClass:
    """Asset class from the symbol name. JPY crosses (GBPJPY...) are volatile; USDJPY behaves like a major in pips but uses
    the JPY parameters too because its quote is JPY."""
    s = symbol.strip().upper()
    if s in _SYMBOLS:
        return _SYMBOLS[s]
    if s.endswith("JPY") or s.startswith("JPY"):
        return AssetClass.JPY_CROSS
    return AssetClass.FOREX_MAJOR


def _check_side(side: str) -> str:
    side = side.lower()
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    return side


def structural_anchor(side: str, sweep_extreme: Optional[float], swing_extreme: Optional[float]) -> float:
    """The farthest of the sweep extreme and the pre-displacement swing (the more protective one). At least one is required."""
    side = _check_side(side)
    levels = [v for v in (sweep_extreme, swing_extreme) if v is not None]
    if not levels:
        raise ValueError("no structural anchor: a sweep extreme or a swing extreme is required")
    return min(levels) if side == "buy" else max(levels)


def buffer(asset: AssetClass, atr: float, spread: float) -> float:
    if atr <= 0 or spread < 0:
        raise ValueError("atr must be > 0 and spread >= 0")
    p = PARAMS[asset]
    return p.atr_mult * atr + p.spread_weight * spread


def hard_sl(side: str, anchor: float, atr: float, spread: float, asset: AssetClass,
            zone_low: Optional[float] = None, zone_high: Optional[float] = None) -> float:
    """Structural level + dynamic buffer. Refuses an anchor that sits at or inside the entry zone (a stop on the zone edge)."""
    side = _check_side(side)
    if side == "buy" and zone_low is not None and anchor >= zone_low:
        raise ValueError("BUY anchor must be below the zone, not at its edge")
    if side == "sell" and zone_high is not None and anchor <= zone_high:
        raise ValueError("SELL anchor must be above the zone, not at its edge")
    b = buffer(asset, atr, spread)
    return anchor - b if side == "buy" else anchor + b


@dataclass(frozen=True)
class EarlyInvalidation:
    level: float
    timeframe: str
    rule: str


def early_invalidation(side: str, zone_low: float, zone_high: float, timeframe: str = "M15") -> EarlyInvalidation:
    """Level whose FULL candle close (not a wick) beyond it kills the idea: below the zone for a BUY, above it for a SELL."""
    side = _check_side(side)
    if zone_high < zone_low:
        raise ValueError("zone_high < zone_low")
    level = zone_low if side == "buy" else zone_high
    word = "below" if side == "buy" else "above"
    return EarlyInvalidation(level, timeframe, f"a closed {timeframe} candle {word} {level}: exit early, do not wait for the Hard SL")


def is_invalidated(side: str, level: float, candle_close: float) -> bool:
    side = _check_side(side)
    return candle_close < level if side == "buy" else candle_close > level


def risk_multiplier(sweep_done: bool, htf_aligned: bool) -> float:
    """100% only with a prior liquidity sweep AND the higher timeframe; otherwise 50% (the stop is not tightened)."""
    return 1.0 if (sweep_done and htf_aligned) else 0.5


@dataclass(frozen=True)
class ContractSpec:
    tick_size: float
    tick_value: float          # money per tick per 1.0 lot, in the account currency
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 100.0


@dataclass(frozen=True)
class LotResult:
    lot: float
    risk_money: float          # the risk budget after scaling
    real_risk: float           # money lost at the Hard SL with this lot
    reason: str = ""


def adjusted_lot_size(balance: float, risk_pct: float, entry: float, sl: float, spec: ContractSpec,
                      multiplier: float = 1.0) -> LotResult:
    """Lot from the Hard SL distance only. Rounded DOWN to the volume step so the real risk never exceeds the budget.
    lot 0 + a reason when even the minimum lot would exceed it (structure too far for this account)."""
    if balance <= 0 or risk_pct <= 0 or spec.tick_size <= 0 or spec.tick_value <= 0:
        raise ValueError("balance, risk_pct, tick_size and tick_value must be > 0")
    pct = min(risk_pct, MAX_RISK_PCT)
    budget = balance * pct / 100.0 * multiplier
    dist = abs(entry - sl)
    if dist <= 0:
        raise ValueError("entry equals the stop")
    loss_per_lot = dist / spec.tick_size * spec.tick_value
    raw = budget / loss_per_lot
    lot = math.floor(raw / spec.volume_step + 1e-9) * spec.volume_step
    lot = min(lot, spec.volume_max)
    lot = round(lot, 8)
    if lot < spec.volume_min:
        return LotResult(0.0, budget, 0.0, f"the structural stop is too far for this risk: the minimum lot {spec.volume_min} "
                                             f"would risk {spec.volume_min * loss_per_lot:.2f} > budget {budget:.2f}")
    return LotResult(lot, budget, lot * loss_per_lot, "")


def net_rr(side: str, entry: float, sl: float, tp1: float, spread: float = 0.0, commission: float = 0.0) -> float:
    """Reward to risk to TP1 with the trading cost (spread + commission, in price units) taken off the reward and added to the risk."""
    side = _check_side(side)
    cost = spread + commission
    reward = (tp1 - entry) if side == "buy" else (entry - tp1)
    risk = abs(entry - sl) + cost
    if reward <= 0:
        return 0.0
    return (reward - cost) / risk


def breakeven_level(side: str, entry: float, spread: float = 0.0, commission: float = 0.0) -> float:
    cost = spread + commission
    return entry + cost if _check_side(side) == "buy" else entry - cost


def should_move_to_breakeven(side: str, entry: float, sl: float, tp1: float, price: float) -> bool:
    """True once TP1 is touched or the trade is +1.5R. ADVICE for you to apply in MT5: the bot never moves an order."""
    side = _check_side(side)
    risk = abs(entry - sl)
    if risk <= 0:
        return False
    if side == "buy":
        return price >= tp1 or (price - entry) >= BE_R * risk
    return price <= tp1 or (entry - price) >= BE_R * risk


@dataclass
class StopPlan:
    symbol: str
    side: str
    entry: float
    hard_sl: float
    early: EarlyInvalidation
    multiplier: float
    lot: Optional[LotResult]
    net_rr: float
    breakeven: float
    status: str                                  # "OK" | "UNBALANCED RISK" | "REJECTED"
    reasons: list[str] = field(default_factory=list)


def plan(symbol: str, side: str, entry: float, atr: float, spread: float, zone_low: float, zone_high: float,
         sweep_extreme: Optional[float], swing_extreme: Optional[float], tp1: float, sweep_done: bool, htf_aligned: bool,
         balance: Optional[float] = None, risk_pct: float = 1.0, spec: Optional[ContractSpec] = None,
         commission: float = 0.0, timeframe: str = "M15") -> StopPlan:
    """Whole plan for one setup. Rejects (never silently fixes) a spread spike, a structure too far, a missing anchor,
    a stop on the zone edge; flags R:R < 1:2 as unbalanced risk."""
    side = _check_side(side)
    asset = classify(symbol)
    p = PARAMS[asset]
    reasons: list[str] = []
    early = early_invalidation(side, zone_low, zone_high, timeframe)
    mult = risk_multiplier(sweep_done, htf_aligned)
    if not sweep_done:
        reasons.append("no prior liquidity sweep: risk reduced to 50%")
    elif not htf_aligned:
        reasons.append("against the higher timeframe (reversal): risk reduced to 50%")

    def rejected(msg: str, sl: float = float("nan")) -> StopPlan:
        return StopPlan(symbol, side, entry, sl, early, mult, None, 0.0, breakeven_level(side, entry, spread, commission),
                        "REJECTED", reasons + [msg])

    if spread > p.max_spread_atr * atr:
        return rejected(f"spread {spread:g} is wider than {p.max_spread_atr} ATR: spread spike, do not enter")
    try:
        anchor = structural_anchor(side, sweep_extreme, swing_extreme)
        sl = hard_sl(side, anchor, atr, spread, asset, zone_low, zone_high)
    except ValueError as e:
        return rejected(str(e))
    if abs(entry - sl) > p.max_stop_atr * atr:
        return rejected(f"the structural stop is {abs(entry - sl) / atr:.1f} ATR away (max {p.max_stop_atr}): too far", sl)
    if (side == "buy" and sl >= entry) or (side == "sell" and sl <= entry):
        return rejected("the stop is on the wrong side of the entry", sl)
    rr = net_rr(side, entry, sl, tp1, spread, commission)
    status = "OK"
    if rr < MIN_NET_RR:
        status = "UNBALANCED RISK"
        reasons.append(f"net R:R to TP1 is 1:{rr:.2f}, below 1:{MIN_NET_RR:g}")
    lot = None
    if balance is not None and spec is not None:
        lot = adjusted_lot_size(balance, risk_pct, entry, sl, spec, mult)
        if lot.lot == 0.0:
            status = "REJECTED"
            reasons.append(lot.reason)
    return StopPlan(symbol, side, entry, sl, early, mult, lot, rr, breakeven_level(side, entry, spread, commission),
                    status, reasons)
