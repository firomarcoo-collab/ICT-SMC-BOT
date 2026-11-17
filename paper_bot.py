"""Paper-trading bot: EMA 9/21 cross + RSI(14) 50 filter on closed candles, simulated account only.

NOTHING in this file can place a real order: it reads public Binance candles (no API key) and keeps a virtual
balance. It is separate from the ICT/SMC signals bot.

Structure
    Config        settings read from the environment / a .env file
    DataFeed      downloads closed candles from Binance's public API (retries, never raises)
    indicators    ema() and rsi() in plain Python (no pandas needed)
    Strategy      turns candles into BUY / SELL / None
    RiskManager   position size, stop loss, take profit and slippage
    PaperBroker   the virtual account: open position, fills, fees, journal, saved state
    PaperBot      the loop that ties everything together

Install:  pip install requests python-dotenv
Run:      python paper_bot.py          (Ctrl+C stops it)     python paper_bot.py --once   (one check, then exit)
"""
import argparse
import csv
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from logging.handlers import RotatingFileHandler

import requests

try:
    from dotenv import load_dotenv
except ImportError:                       # python-dotenv missing: plain environment variables still work
    def load_dotenv(*_a, **_k):
        return False

LOG = logging.getLogger("paper_bot")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INTERVAL_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


def setup_logging(path="trading_bot.log"):
    """Logs to the screen and to trading_bot.log (rotating, so the file never grows without limit)."""
    LOG.setLevel(logging.INFO)
    if LOG.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.StreamHandler(), RotatingFileHandler(os.path.join(BASE_DIR, path), maxBytes=1_000_000, backupCount=3)):
        h.setFormatter(fmt)
        LOG.addHandler(h)


@dataclass
class Config:
    """All settings. Defaults are the example values; change them in .env, never in the code."""
    symbol: str = "BTCUSDT"
    interval: str = "15m"
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_level: float = 50.0
    sl_pct: float = 1.5            # stop loss, % of the entry price
    tp_pct: float = 3.0            # take profit, % of the entry price
    risk_pct: float = 1.0          # % of the balance lost if the stop is hit (this sizes the position)
    slippage_pct: float = 0.05     # a bad fill: buys cost more, sells receive less, by this % of the price
    fee_pct: float = 0.1           # fee per side, % of the traded value
    start_balance: float = 1000.0
    poll_seconds: int = 30
    base_url: str = "https://api.binance.com"
    state_file: str = "paper_state.json"
    journal_file: str = "paper_trades.csv"

    @classmethod
    def from_env(cls, env=None):
        """Builds a Config from environment variables (PAPER_SYMBOL, PAPER_SL_PCT ...); bad numbers raise ValueError."""
        env = os.environ if env is None else env
        cfg = cls()
        for name, default in asdict(cfg).items():
            raw = env.get(f"PAPER_{name.upper()}")
            if raw not in (None, ""):
                setattr(cfg, name, type(default)(raw))
        cfg.validate()
        return cfg

    def validate(self):
        """Stops the bot at start-up on nonsense settings instead of trading with them."""
        if self.interval not in INTERVAL_SECONDS:
            raise ValueError(f"interval must be one of {sorted(INTERVAL_SECONDS)}")
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be smaller than ema_slow")
        if not (0 < self.sl_pct < 100 and 0 < self.tp_pct < 100 and 0 < self.risk_pct <= 5):
            raise ValueError("sl_pct and tp_pct must be 0-100 and risk_pct 0-5")
        if self.start_balance <= 0:
            raise ValueError("start_balance must be positive")


class DataFeed:
    """Public Binance candles. Only CLOSED candles are returned (the forming one is dropped)."""

    def __init__(self, cfg, session=None):
        self.cfg, self.http = cfg, session or requests.Session()

    def closed_candles(self, limit=200, now=None, retries=3):
        """Returns a list of dicts {time, open, high, low, close}; [] when the feed fails (the loop just tries again)."""
        url = f"{self.cfg.base_url}/api/v3/klines"
        params = {"symbol": self.cfg.symbol, "interval": self.cfg.interval, "limit": limit}
        for attempt in range(1, retries + 1):
            try:
                resp = self.http.get(url, params=params, timeout=10)
                resp.raise_for_status()
                rows = resp.json()
                break
            except (requests.RequestException, ValueError) as exc:
                LOG.warning("candle download failed (%d/%d): %s", attempt, retries, exc)
                time.sleep(min(2 * attempt, 5) if now is None else 0)
        else:
            return []
        now = time.time() if now is None else now
        step = INTERVAL_SECONDS[self.cfg.interval]
        out = []
        try:
            for r in rows:
                start = int(r[0]) // 1000
                if start + step <= now:                       # the candle has really closed
                    out.append({"time": start, "open": float(r[1]), "high": float(r[2]),
                                "low": float(r[3]), "close": float(r[4])})
        except (IndexError, TypeError, ValueError) as exc:
            LOG.error("unexpected candle format: %s", exc)
            return []
        return out


def ema(values, period):
    """Exponential moving average; one value per input, the first `period-1` are None."""
    out, k, prev = [], 2 / (period + 1), None
    for i, v in enumerate(values):
        if i + 1 < period:
            out.append(None)
        elif prev is None:
            prev = sum(values[i + 1 - period:i + 1]) / period
            out.append(prev)
        else:
            prev = v * k + prev * (1 - k)
            out.append(prev)
    return out


def rsi(values, period=14):
    """Wilder RSI; one value per input, None until enough data."""
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, period + 1)]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, period + 1)]
    ag, al = sum(gains) / period, sum(losses) / period
    for i in range(period, len(values)):
        if i > period:
            d = values[i] - values[i - 1]
            ag = (ag * (period - 1) + max(d, 0)) / period
            al = (al * (period - 1) + max(-d, 0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


class Strategy:
    """BUY when EMA fast crosses above EMA slow and RSI > level; SELL on the opposite cross with RSI < level."""

    def __init__(self, cfg):
        self.cfg = cfg

    def signal(self, candles):
        """candles: closed candles, oldest first. Returns 'buy', 'sell' or None, judged on the LAST closed candle."""
        need = max(self.cfg.ema_slow, self.cfg.rsi_period) + 3
        if len(candles) < need:
            return None
        closes = [c["close"] for c in candles]
        fast, slow, r = ema(closes, self.cfg.ema_fast), ema(closes, self.cfg.ema_slow), rsi(closes, self.cfg.rsi_period)
        if None in (fast[-2], slow[-2], fast[-1], slow[-1], r[-1]):
            return None
        if fast[-2] <= slow[-2] and fast[-1] > slow[-1] and r[-1] > self.cfg.rsi_level:
            return "buy"
        if fast[-2] >= slow[-2] and fast[-1] < slow[-1] and r[-1] < self.cfg.rsi_level:
            return "sell"
        return None


class RiskManager:
    """Stop loss, take profit, position size and the slippage-adjusted fill price."""

    def __init__(self, cfg):
        self.cfg = cfg

    def fill_price(self, price, side, closing=False):
        """Slippage always works against you: opening a buy or closing a sell pays more / receives less."""
        buy_like = (side == "buy") != closing
        return price * (1 + self.cfg.slippage_pct / 100) if buy_like else price * (1 - self.cfg.slippage_pct / 100)

    def levels(self, entry, side):
        """(stop loss, take profit) as percentages of the entry price."""
        sl, tp = self.cfg.sl_pct / 100, self.cfg.tp_pct / 100
        return (entry * (1 - sl), entry * (1 + tp)) if side == "buy" else (entry * (1 + sl), entry * (1 - tp))

    def size(self, balance, entry, sl):
        """Units to trade so that hitting the stop loses risk_pct of the balance; never more than the balance
        (no leverage). Returns 0 when the stop distance is zero."""
        dist = abs(entry - sl)
        if dist <= 0 or balance <= 0:
            return 0.0
        units = balance * self.cfg.risk_pct / 100 / dist
        return min(units, balance / entry)


class PaperBroker:
    """The virtual account: one open position at a time, fees, a CSV journal and a state file that survives restarts."""

    JOURNAL_HEAD = ["time_utc", "side", "entry", "exit", "units", "pnl", "balance", "reason"]

    def __init__(self, cfg, risk):
        self.cfg, self.risk = cfg, risk
        self.balance, self.position, self.last_candle = cfg.start_balance, None, 0
        self._load()

    def _load(self):
        """Reads the saved state; a damaged file is ignored (and logged), never fatal."""
        try:
            with open(os.path.join(BASE_DIR, self.cfg.state_file)) as f:
                s = json.load(f)
            self.balance, self.position, self.last_candle = float(s["balance"]), s.get("position"), int(s["last_candle"])
            LOG.info("state restored: balance %.2f, position %s", self.balance, "open" if self.position else "none")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            LOG.error("state file unreadable, starting fresh: %s", exc)

    def save(self):
        """Writes the state atomically (temp file then rename) so a crash never leaves half a file."""
        path = os.path.join(BASE_DIR, self.cfg.state_file)
        try:
            with open(path + ".tmp", "w") as f:
                json.dump({"balance": self.balance, "position": self.position, "last_candle": self.last_candle}, f)
            os.replace(path + ".tmp", path)
        except OSError as exc:
            LOG.error("could not save state: %s", exc)

    def open(self, side, price, ts):
        """Opens a virtual position with slippage and the entry fee. Returns True when opened."""
        if self.position:
            return False
        entry = self.risk.fill_price(price, side)
        sl, tp = self.risk.levels(entry, side)
        units = self.risk.size(self.balance, entry, sl)
        if units <= 0:
            LOG.warning("position size is zero: no trade")
            return False
        self.balance -= units * entry * self.cfg.fee_pct / 100
        self.position = {"side": side, "entry": entry, "sl": sl, "tp": tp, "units": units, "time": ts}
        LOG.info("PAPER %s opened at %.2f (SL %.2f, TP %.2f, units %.6f)", side.upper(), entry, sl, tp, units)
        return True

    def check_exit(self, candle):
        """Looks at one closed candle: stop loss or take profit touched? If both are inside the same candle the stop
        loss is assumed first (the pessimistic choice). Returns the closed-trade dict or None."""
        p = self.position
        if not p:
            return None
        buy = p["side"] == "buy"
        hit_sl = candle["low"] <= p["sl"] if buy else candle["high"] >= p["sl"]
        hit_tp = candle["high"] >= p["tp"] if buy else candle["low"] <= p["tp"]
        if not (hit_sl or hit_tp):
            return None
        reason, level = ("stop loss", p["sl"]) if hit_sl else ("take profit", p["tp"])
        return self.close(level, candle["time"], reason)

    def close(self, price, ts, reason):
        """Closes the position at `price` (slippage and the exit fee applied), updates the balance, writes the journal."""
        p = self.position
        exit_price = self.risk.fill_price(price, p["side"], closing=True)
        gross = (exit_price - p["entry"]) * p["units"] * (1 if p["side"] == "buy" else -1)
        self.balance += gross - p["units"] * exit_price * self.cfg.fee_pct / 100
        trade = {"time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)), "side": p["side"], "entry": round(p["entry"], 2),
                 "exit": round(exit_price, 2), "units": round(p["units"], 6), "pnl": round(gross, 2),
                 "balance": round(self.balance, 2), "reason": reason}
        self.position = None
        self._journal(trade)
        LOG.info("PAPER trade closed (%s): pnl %.2f, balance %.2f", reason, gross, self.balance)
        return trade

    def _journal(self, trade):
        path = os.path.join(BASE_DIR, self.cfg.journal_file)
        try:
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self.JOURNAL_HEAD)
                if new:
                    w.writeheader()
                w.writerow(trade)
        except OSError as exc:
            LOG.error("could not write the journal: %s", exc)


class PaperBot:
    """Ties the pieces together. process() handles each closed candle exactly once."""

    def __init__(self, cfg, feed=None):
        self.cfg = cfg
        self.risk = RiskManager(cfg)
        self.broker = PaperBroker(cfg, self.risk)
        self.strategy = Strategy(cfg)
        self.feed = feed or DataFeed(cfg)

    def process(self, candles):
        """Handles every closed candle newer than the last one processed. Returns the list of events (for the log/tests)."""
        events = []
        for i, c in enumerate(candles):
            if c["time"] <= self.broker.last_candle:
                continue
            self.broker.last_candle = c["time"]
            closed = self.broker.check_exit(c)
            if closed:
                events.append(("closed", closed))
            sig = self.strategy.signal(candles[:i + 1]) if i == len(candles) - 1 else None   # only the newest candle can open
            if sig and not self.broker.position and self.broker.open(sig, c["close"], c["time"]):
                events.append(("opened", sig))
        self.broker.save()
        return events

    def run(self, once=False):
        """The main loop. Any error inside one round is logged and the loop carries on."""
        LOG.info("paper bot started: %s %s, SL %.2f%% TP %.2f%% risk %.2f%% (SIMULATION, no real orders)",
                 self.cfg.symbol, self.cfg.interval, self.cfg.sl_pct, self.cfg.tp_pct, self.cfg.risk_pct)
        while True:
            try:
                candles = self.feed.closed_candles()
                if candles:
                    self.process(candles)
            except Exception:                                   # a bug must not stop the bot: log it and go on
                LOG.exception("round failed")
            if once:
                return
            time.sleep(self.cfg.poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="Paper-trading bot (simulation only)")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    args = parser.parse_args()
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    setup_logging()
    try:
        cfg = Config.from_env()
    except ValueError as exc:
        raise SystemExit(f"bad setting: {exc}")
    try:
        PaperBot(cfg).run(once=args.once)
    except KeyboardInterrupt:
        LOG.info("stopped by the user")


if __name__ == "__main__":
    main()
