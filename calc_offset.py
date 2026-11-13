"""Works out OFFSET_<SYMBOL> from your broker's price.

Usage:  python calc_offset.py XAUUSD 4012.35
  (type the Bid you see in MT5 for that symbol RIGHT NOW; the script reads the bot's feed price
   at the same moment and prints the line to put in your shortcut file.)
"""
import sys

import ict_alerts


def feed_price(name):
    inst = ict_alerts.INSTRUMENTS[name]
    candles = ict_alerts.get_candles(inst, "1m")
    return max(candles, key=lambda c: c["time"])["close"]


def offset_line(name, broker_price, feed):
    off = round(broker_price - feed, 2)
    return (f"{name}: feed {feed:.2f}, broker {broker_price:.2f} -> OFFSET_{name}={off}\n"
            f"Put this before the python line in ~/.shortcuts/ICT-Alerts:  OFFSET_{name}={off} ")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Use: python calc_offset.py XAUUSD <the Bid shown in MT5 now>")
    sym = sys.argv[1].upper()
    print(offset_line(sym, float(sys.argv[2]), feed_price(sym)))
