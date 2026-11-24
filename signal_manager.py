"""One idea, one alert (signals only). Two jobs:
1. allow(): the alert-only detectors (displacement, SMC Prime, scalper, pulse) may issue a SIGNAL for a market only when no
   SELECTIVE/IFVG/Asia signal is active there (signal_store) and no other detector holds an unexpired claim on that market.
   Claims survive a restart (signal_manager.json) and expire after TTL hours or when the idea is invalidated (release()).
2. single_instance(): a lock file so two bot processes can never run at once (a second one would double every alert).
Every market is independent. Nothing here ever places or changes an order."""
from __future__ import annotations

import json
import os

TTL_HOURS = 6.0


def _file():
    return os.environ.get("SIGNAL_MANAGER_FILE", "signal_manager.json")


def _load():
    try:
        with open(_file()) as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(rows):
    try:
        with open(_file(), "w") as f:
            json.dump(rows, f)
    except OSError:
        pass


def active_claims(market, now_ts, ttl_hours=None):
    ttl = (ttl_hours if ttl_hours is not None else float(os.environ.get("SIGNAL_TTL_HOURS") or TTL_HOURS)) * 3600
    return [c for c in _load() if c["market"] == market and now_ts - c["ts"] < ttl]


def allow(market, side, entry, now_ts, strategy, store=None, ttl_hours=None):
    """(True, '') and the idea is claimed, or (False, why)."""
    if store is not None and store.active_list(market):
        cur = store.active_list(market)[-1]
        return False, f"{market} already has an active {cur['side'].upper()} signal ({cur['id']})"
    ttl = (ttl_hours if ttl_hours is not None else float(os.environ.get("SIGNAL_TTL_HOURS") or TTL_HOURS)) * 3600
    rows = [c for c in _load() if now_ts - c["ts"] < ttl]
    for c in rows:
        if c["market"] == market:
            same = "the same idea" if c["side"] == side else "an opposite idea"
            return False, f"{market} already has {same} from {c['strategy']} (one active signal per market)"
    rows.append({"market": market, "side": side, "entry": entry, "ts": int(now_ts), "strategy": strategy})
    _save(rows)
    return True, ""


def release(market, strategy=None):
    """The idea ended (invalidated / expired): the market may signal again."""
    rows = [c for c in _load() if not (c["market"] == market and (strategy is None or c["strategy"] == strategy))]
    _save(rows)


def single_instance(path="bot.lock"):
    """Takes an exclusive lock for the life of the process. Returns the open file (keep it referenced) or None when another
    copy of the bot already holds it. Without fcntl (not Linux / Android) it cannot lock and returns True."""
    try:
        import fcntl
    except ImportError:                                           # pragma: no cover
        return True
    f = open(path, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f
