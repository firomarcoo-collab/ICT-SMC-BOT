"""Links the bot's gold (or any) price to your broker's price and saves the difference.

Usage:  python calc_offset.py XAUUSD 4012.35
  type the Bid you see in MT5 for that symbol RIGHT NOW. The script reads the bot's feed price at the
  same moment, works out OFFSET = broker - feed and SAVES it in offsets.json. The running bot picks the
  new value up by itself (no restart); python analyze_now.py uses it too.
  python calc_offset.py XAUUSD 0      -> removes the offset
  python calc_offset.py --show        -> prints the saved offsets
"""
import json
import sys

import ict_alerts


def feed_price(name):
    inst = ict_alerts.INSTRUMENTS[name]
    candles = ict_alerts.get_candles(inst, "1m")
    return max(candles, key=lambda c: c["time"])["close"]


def offset_line(name, broker_price, feed):
    off = round(broker_price - feed, 2)
    return (f"{name}: feed {feed:.2f}, broker {broker_price:.2f} -> OFFSET_{name}={off}\n"
            f"Saved in {ict_alerts.OFFSETS_FILE}; the bot now shows {name} prices like your broker.")


def save_offset(name, value, path=None):
    path = path or ict_alerts.OFFSETS_FILE
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return data


def sane(broker_price, feed, limit=0.05):
    """A broker/feed gap above 5% of the price is almost always a typo or the wrong symbol."""
    return feed > 0 and abs(broker_price - feed) / feed <= limit


if __name__ == "__main__":
    if sys.argv[1:] == ["--show"]:
        print(ict_alerts.load_offsets({}) or "no offsets saved")
        raise SystemExit
    if len(sys.argv) != 3:
        raise SystemExit("Use: python calc_offset.py XAUUSD <the Bid shown in MT5 now>   (or --show)")
    sym = sys.argv[1].upper()
    broker = float(sys.argv[2])
    if broker == 0:
        save_offset(sym, 0)
        raise SystemExit(f"{sym}: offset removed")
    feed = feed_price(sym)
    if not sane(broker, feed):
        raise SystemExit(f"{sym}: feed {feed:.2f} and your number {broker:.2f} differ by more than 5%: "
                         "check the number and the symbol. Nothing saved.")
    print(offset_line(sym, broker, feed))
    save_offset(sym, round(broker - feed, 2))
