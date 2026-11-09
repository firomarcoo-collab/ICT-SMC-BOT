"""Per-market specifications (XAUUSD, NAS100, US30, BTCUSD) read from markets.json.

Nothing about a broker symbol is assumed: contract size, tick size, tick value, lot limits,
spread and commission stay empty (null) until you copy them from the MT5 symbol
specification. A market whose required fields are missing is "unverified", and the system
then refuses to compute a lot size instead of guessing.

Check the state with:   python markets.py
"""
import json
import math
import os
from dataclasses import dataclass, field, fields

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "markets.json")

# fields that must be filled (from MT5 > symbol > Specification) before a lot is computed
REQUIRED = ("broker_symbol", "product_type", "contract_size", "tick_size", "tick_value",
            "volume_min", "volume_step", "volume_max", "spread", "commission_per_lot")
PRODUCT_TYPES = ("spot_cfd", "futures_cfd", "crypto_cfd", "index_cfd", "other")


@dataclass
class MarketSpec:
    name: str                       # internal key: XAUUSD, NAS100, US30, BTCUSD
    feed_symbol: str                # key used by ict_alerts.INSTRUMENTS (free data feed)
    broker_symbol: str = None       # exact name in your MT5 (e.g. "XAUUSD", "NAS100", "USTEC")
    product_type: str = None        # one of PRODUCT_TYPES, as stated by the broker
    contract_size: float = None     # units per 1.00 lot
    tick_size: float = None         # smallest price step
    tick_value: float = None        # account-currency profit per tick per 1.00 lot
    volume_min: float = None
    volume_step: float = None
    volume_max: float = None
    spread: float = None            # typical spread in PRICE units (not points)
    commission_per_lot: float = None  # account currency, round turn, per 1.00 lot (0 if none)
    risk_percent: float = 0.25      # per-market defaults, edit in markets.json
    min_rr: float = 2.0
    notes: str = ""

    def missing(self):
        return [f for f in REQUIRED if getattr(self, f) is None]

    def verified(self):
        return not self.missing() and self.product_type in PRODUCT_TYPES


DEFAULTS = {
    "XAUUSD": MarketSpec("XAUUSD", "XAUUSD"),
    "NAS100": MarketSpec("NAS100", "US100"),
    "US30": MarketSpec("US30", "US30"),
    "BTCUSD": MarketSpec("BTCUSD", "BTCUSD"),
}
ALIASES = {"US100": "NAS100", "USTEC": "NAS100", "NDX": "NAS100",
           "DJ30": "US30", "DOW": "US30", "GOLD": "XAUUSD"}


def canonical(name):
    name = name.strip().upper()
    return ALIASES.get(name, name)


def feed_name(name):
    """Name used by the data feed / alerts (NAS100 and USTEC give US100)."""
    c = canonical(name)
    return DEFAULTS[c].feed_symbol if c in DEFAULTS else name.strip().upper()


def load_markets(path=DEFAULT_FILE):
    """Returns {name: MarketSpec}. Unknown keys in the file are ignored; bad numbers raise."""
    out = {k: MarketSpec(**{f.name: getattr(v, f.name) for f in fields(v)}) for k, v in DEFAULTS.items()}
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return out
    known = {f.name for f in fields(MarketSpec)}
    for key, values in data.items():
        key = canonical(key)
        if key not in out or not isinstance(values, dict):
            continue
        for k, v in values.items():
            if k in known and k not in ("name", "feed_symbol"):
                if v is not None and k not in ("broker_symbol", "product_type", "notes"):
                    v = float(v)
                setattr(out[key], k, v)
    return out


def loss_per_lot(spec, distance):
    """Account-currency loss per 1.00 lot when price moves `distance` against the trade."""
    return distance / spec.tick_size * spec.tick_value


def lot_for_risk(spec, balance, risk_percent, entry, sl):
    """Returns (lot, money_at_risk, reason). lot is 0 with a reason when it cannot be sized.
    The lot is rounded DOWN to the broker's volume step, never up."""
    if not spec.verified():
        return 0.0, 0.0, f"specs not verified (missing: {', '.join(spec.missing()) or 'product_type'})"
    distance = abs(entry - sl)
    if balance <= 0 or distance <= 0:
        return 0.0, 0.0, "balance or stop distance is zero"
    money = balance * risk_percent / 100.0
    raw = money / loss_per_lot(spec, distance)
    lot = math.floor(raw / spec.volume_step + 1e-9) * spec.volume_step
    lot = round(min(lot, spec.volume_max), 8)
    if lot < spec.volume_min:
        need = spec.volume_min * loss_per_lot(spec, distance) / balance * 100
        return 0.0, money, f"stop too wide: the minimum lot would risk {need:.2f}% (limit {risk_percent}%)"
    return lot, money, ""


def cost_in_price(spec):
    """Spread plus round-turn commission, expressed in price units; None when unknown."""
    if spec.spread is None or spec.commission_per_lot is None or not spec.verified():
        return None
    return spec.spread + spec.commission_per_lot / (spec.tick_value / spec.tick_size)


def status_table(markets):
    lines = []
    for name in ("XAUUSD", "NAS100", "US30", "BTCUSD"):
        m = markets[name]
        if m.verified():
            lines.append(f"{name}: verified ({m.broker_symbol}, {m.product_type})")
        else:
            lines.append(f"{name}: NOT verified, missing: {', '.join(m.missing())}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(status_table(load_markets()))
