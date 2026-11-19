"""Shows why opportunities were rejected.  python why.py XAUUSD      (last 10)   python why.py XAUUSD 30   python why.py"""
import sys

import rejections

if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else None
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    print(rejections.text(rejections.read(market, n)))
