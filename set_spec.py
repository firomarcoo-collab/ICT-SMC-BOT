"""Fills a market specification in markets.json from the MT5 symbol Specification screen.

Usage:  python set_spec.py XAUUSD tick_size=0.01 tick_value=1 spread=0.25 commission_per_lot=0 product_type=spot_cfd
        python set_spec.py EURUSD broker_symbol=EURUSD.m contract_size=100000 tick_size=0.00001 tick_value=1 \
            volume_min=0.01 volume_step=0.01 volume_max=100 spread=0.00012 commission_per_lot=0 product_type=spot_cfd
        python set_spec.py XAUUSD            (just shows what is still missing)

spread is in PRICE units (EURUSD 1.2 pips = 0.00012, gold 25 cents = 0.25). tick_value is the profit in your
ACCOUNT currency for one tick on 1.00 lot. Nothing is guessed: only what you type is saved.
"""
import json
import sys

import markets

NUMBERS = ("contract_size", "tick_size", "tick_value", "volume_min", "volume_step", "volume_max", "spread",
           "commission_per_lot", "risk_percent", "min_rr")
TEXT = ("broker_symbol", "product_type", "notes")


def apply(data, name, pairs):
    """Returns the updated dict for `name` (raises ValueError with a clear message on a bad value)."""
    if name not in data:
        raise ValueError(f"unknown market {name}; known: {', '.join(data)}")
    spec = dict(data[name])
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"{pair!r}: write it as key=value")
        key, value = pair.split("=", 1)
        if key in NUMBERS:
            try:
                number = float(value)
            except ValueError:
                raise ValueError(f"{key} must be a number, got {value!r}")
            if number < 0 or (key in ("tick_size", "tick_value", "contract_size", "volume_step") and number == 0):
                raise ValueError(f"{key} must be positive, got {value}")
            spec[key] = int(number) if number.is_integer() and key in ("contract_size", "volume_max") else number
        elif key in TEXT:
            if key == "product_type" and value not in markets.PRODUCT_TYPES:
                raise ValueError(f"product_type must be one of {', '.join(markets.PRODUCT_TYPES)}")
            spec[key] = value
        else:
            raise ValueError(f"unknown field {key}")
    out = dict(data)
    out[name] = spec
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sym = sys.argv[1].upper()
    with open(markets.DEFAULT_FILE) as f:
        data = json.load(f)
    try:
        data = apply(data, sym, sys.argv[2:])
    except ValueError as e:
        raise SystemExit(f"Error: {e}. Nothing saved.")
    if len(sys.argv) > 2:
        with open(markets.DEFAULT_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    spec = markets.load_markets()[sym]
    print(f"{sym}: " + ("VERIFIED, lot size will be calculated" if spec.verified()
                        else "still missing: " + ", ".join(spec.missing())))
