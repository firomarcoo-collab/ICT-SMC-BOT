"""Feed doctor: shows which free data sources work right now. Run: python check_feeds.py
For every symbol it tries each host and each fallback and prints OK (last price and time)
or the error. Nothing is changed."""
import urllib.parse
from datetime import datetime, timezone

import ict_alerts as a


def main():
    for name, inst in a.INSTRUMENTS.items():
        if inst["source"] == "binance":
            try:
                c = a.parse_binance(a.fetch_json(
                    f"https://api.binance.com/api/v3/klines?symbol={inst['id']}&interval=5m&limit=5"))
                print(f"{name:7} binance {inst['id']:10} OK  last {c[-1]['close']} at {c[-1]['time']:%H:%M} UTC")
            except Exception as e:
                print(f"{name:7} binance {inst['id']:10} FAILED: {e}")
            continue
        for sid in [inst["id"]] + list(inst.get("fallbacks", [])):
            for host in a.YAHOO_HOSTS:
                url = (f"https://{host}/v8/finance/chart/{urllib.parse.quote(sid, safe='')}"
                       f"?interval=5m&range=1d")
                try:
                    c = a.parse_yahoo(a.fetch_json(url))
                    age = (datetime.now(timezone.utc) - c[-1]["time"]).total_seconds() / 60
                    print(f"{name:7} {host[:6]} {sid:9} OK  last {c[-1]['close']} ({age:.0f} min old)")
                except Exception as e:
                    print(f"{name:7} {host[:6]} {sid:9} FAILED: {e}")


if __name__ == "__main__":
    main()
