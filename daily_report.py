"""Trades report: open and closed signals, profit / loss in R (and in money when RISK_USD is set), per day.
These are the BOT's followed signals, not your real MT5 account (the bot never sees it and never trades).
R = one unit of risk: a win of the planned target is +R(reward/risk), a loss is -1R.
Shown on the Termux screen every TRADES_REPORT_MINUTES (default 30; 0 = off) and as ONE silent phone notification that is
replaced each time (tap = the whole text). On demand:  python daily_report.py   (reads the saved state files only)"""
import os
import sys
import time

import signal_store

DAYS = 7
SIDE_AR = {"buy": "شراء", "sell": "بيع"}
OUT_AR = {"win": "ربح", "loss": "خسارة"}
WEEKDAY_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _day_label(ts):
    t = time.localtime(ts)
    return f"{WEEKDAY_AR[t.tm_wday]} {t.tm_mday:02d}/{t.tm_mon:02d}"


def _money(r, risk_usd):
    return f" (≈ {r * risk_usd:+.2f}$)" if risk_usd else ""


def _num(v):
    return f"{float(v):.8g}"


def floating_r(sig, price):
    """Open profit / loss of a signal in R at `price` (None when there is no price or no stop distance)."""
    risk = abs(sig["entry"] - sig["sl"])
    if price is None or not risk:
        return None
    return ((price - sig["entry"]) if sig["side"] == "buy" else (sig["entry"] - price)) / risk


EN = {"side": {"buy": "BUY", "sell": "SELL"}, "out": {"win": "win", "loss": "loss"},
      "week": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def _lang(lang=None):
    return (lang or os.environ.get("ALERT_LANG") or "en").lower()


def build(store, now_ts=None, prices=None, states=None, tally=None, risk_usd=None, days=DAYS, lang=None):
    """The Arabic report text. store: SignalStore. prices: {market: last price} for the open P/L. states: {signal id: 'open'|'pending'}
    (from the tracker). tally: the tracker's all-time line."""
    if _lang(lang) != "ar":
        return build_en(store, now_ts, prices, states, tally, risk_usd, days)
    now_ts = int(now_ts if now_ts is not None else time.time())
    prices, states = prices or {}, states or {}
    if risk_usd is None:
        try:
            risk_usd = float(os.environ.get("RISK_USD") or 0) or None
        except ValueError:
            risk_usd = None
    lines = [f"📊 تقرير الصفقات — {_day_label(now_ts)} {time.strftime('%H:%M', time.localtime(now_ts))}", ""]
    active = list(store.active_signals.values())
    lines.append(f"🟢 المفتوحة الآن ({len(active)})")
    total_float, n_float = 0.0, 0
    for s in sorted(active, key=lambda x: x["created"]):
        st = states.get(s["id"])
        where = {"open": "دخلت", "pending": "بانتظار السعر للدخول"}.get(st, "نشطة")
        fr = floating_r(s, prices.get(s["market"])) if st != "pending" else None
        if fr is not None:
            total_float += fr
            n_float += 1
        mins = max(0, int((now_ts - s["created"]) / 60))
        lines.append(f"• {s['market']} {SIDE_AR.get(s['side'], s['side'])} | الدخول {_num(s['entry'])} | الوقف {_num(s['sl'])} | "
                     f"الهدف {_num(s['tp'])} | {where} منذ {mins} د"
                     + (f" | عائم {fr:+.2f}R{_money(fr, risk_usd)}" if fr is not None else ""))
    if not active:
        lines.append("• لا توجد صفقات مفتوحة")
    elif n_float:
        lines.append(f"  مجموع العائم: {total_float:+.2f}R{_money(total_float, risk_usd)}")
    results = list(store.results)
    today = _day(now_ts)
    done_today = [r for r in results if _day(r["t"]) == today]
    lines += ["", f"🔴 المغلقة اليوم ({len(done_today)})"]
    for r in sorted(done_today, key=lambda x: x["t"]):
        lines.append(f"• {time.strftime('%H:%M', time.localtime(r['t']))} {r.get('market', '')} — "
                     f"{OUT_AR.get(r['outcome'], r['outcome'])} {r['R']:+.2f}R{_money(r['R'], risk_usd)}")
    if not done_today:
        lines.append("• لا توجد صفقات مغلقة اليوم")
    else:
        net = sum(r["R"] for r in done_today)
        w = sum(r["outcome"] == "win" for r in done_today)
        lines.append(f"  اليوم: {w} ربح، {len(done_today) - w} خسارة، الصافي {net:+.2f}R{_money(net, risk_usd)}")
    by_day = {}
    for r in results:
        by_day.setdefault(_day(r["t"]), []).append(r)
    lines += ["", f"📅 آخر {days} أيام"]
    shown = 0
    for d in sorted(by_day, reverse=True)[:days]:
        rs = by_day[d]
        w = sum(x["outcome"] == "win" for x in rs)
        net = sum(x["R"] for x in rs)
        lines.append(f"• {_day_label(rs[0]['t'])}: {len(rs)} صفقة ({w} ربح / {len(rs) - w} خسارة) | {net:+.2f}R{_money(net, risk_usd)}")
        shown += 1
    if not shown:
        lines.append("• لا توجد نتائج مسجلة بعد")
    if tally:
        lines += ["", "📈 " + tally]
    lines += ["", "ℹ️ هذه صفقات البوت المتتبَّعة وليست حسابك الحقيقي في MT5 (البوت لا ينفذ صفقات). R = وحدة المخاطرة؛ "
                  "الربح = العائد/المخاطرة للهدف، والخسارة = -1R. النتائج لا تضمن المستقبل."
              + ("" if risk_usd else " لعرض المبالغ بالدولار اضبط RISK_USD (المبلغ الذي تخاطر به في الصفقة).")]
    try:
        import session_tracker
        lines += [""] + session_tracker.report_lines(now_ts, "ar")
    except Exception:                                              # the extra block must never break the report
        pass
    if os.environ.get("KENAN_V2", "1") != "0":
        lines.append("Kenan v2: M15 entry only, score >= 80, R:R >= 1:2, TP1 = 1R, TP2 = 2R, one signal per market. Signals only.")
    return "\n".join(lines)


def build_en(store, now_ts=None, prices=None, states=None, tally=None, risk_usd=None, days=DAYS):
    """The same report in English (the default)."""
    now_ts = int(now_ts if now_ts is not None else time.time())
    prices, states = prices or {}, states or {}
    if risk_usd is None:
        try:
            risk_usd = float(os.environ.get("RISK_USD") or 0) or None
        except ValueError:
            risk_usd = None
    mny = lambda r: f" (~{r * risk_usd:+.2f}$)" if risk_usd else ""                      # noqa: E731
    label = lambda ts: f"{EN['week'][time.localtime(ts).tm_wday]} {time.localtime(ts).tm_mday:02d}/{time.localtime(ts).tm_mon:02d}"   # noqa: E731
    lines = [f"📊 TRADES REPORT - {label(now_ts)} {time.strftime('%H:%M', time.localtime(now_ts))}", ""]
    active = list(store.active_signals.values())
    lines.append(f"🟢 OPEN NOW ({len(active)})")
    total_float, n_float = 0.0, 0
    for s in sorted(active, key=lambda x: x["created"]):
        st = states.get(s["id"])
        where = {"open": "entered", "pending": "waiting for the entry price"}.get(st, "active")
        fr = floating_r(s, prices.get(s["market"])) if st != "pending" else None
        if fr is not None:
            total_float += fr
            n_float += 1
        mins = max(0, int((now_ts - s["created"]) / 60))
        lines.append(f"• {s['market']} {EN['side'].get(s['side'], s['side'])} | entry {_num(s['entry'])} | SL {_num(s['sl'])} | "
                     f"TP {_num(s['tp'])} | {where} {mins} min ago"
                     + (f" | floating {fr:+.2f}R{mny(fr)}" if fr is not None else ""))
    if not active:
        lines.append("• no open trades")
    elif n_float:
        lines.append(f"  total floating: {total_float:+.2f}R{mny(total_float)}")
    results = list(store.results)
    done_today = [r for r in results if _day(r["t"]) == _day(now_ts)]
    lines += ["", f"🔴 CLOSED TODAY ({len(done_today)})"]
    for r in sorted(done_today, key=lambda x: x["t"]):
        lines.append(f"• {time.strftime('%H:%M', time.localtime(r['t']))} {r.get('market', '')} - "
                     f"{EN['out'].get(r['outcome'], r['outcome'])} {r['R']:+.2f}R{mny(r['R'])}")
    if not done_today:
        lines.append("• no closed trades today")
    else:
        net = sum(r["R"] for r in done_today)
        w = sum(r["outcome"] == "win" for r in done_today)
        lines.append(f"  today: {w} wins, {len(done_today) - w} losses, net {net:+.2f}R{mny(net)}")
    by_day = {}
    for r in results:
        by_day.setdefault(_day(r["t"]), []).append(r)
    lines += ["", f"📅 LAST {days} DAYS"]
    for d in sorted(by_day, reverse=True)[:days]:
        rs = by_day[d]
        w = sum(x["outcome"] == "win" for x in rs)
        net = sum(x["R"] for x in rs)
        lines.append(f"• {label(rs[0]['t'])}: {len(rs)} trades ({w} wins / {len(rs) - w} losses) | {net:+.2f}R{mny(net)}")
    if not by_day:
        lines.append("• no results recorded yet")
    if tally:
        lines += ["", "📈 " + tally]
    lines += ["", "ℹ️ These are the bot's followed signals, not your real MT5 account (the bot never trades). R = one unit of risk: a win is the "
                  "target's reward/risk, a loss is -1R. Past results do not guarantee the future."
              + ("" if risk_usd else " Set RISK_USD (the money you risk per trade) to see approximate dollar amounts.")]
    try:
        import session_tracker
        lines += [""] + session_tracker.report_lines(now_ts, "en")
    except Exception:                                              # the extra block must never break the report
        pass
    if os.environ.get("KENAN_V2", "1") != "0":
        lines.append("Kenan v2: M15 entry only, score >= 80, R:R >= 1:2, TP1 = 1R, TP2 = 2R, one signal per market. Signals only.")
    return "\n".join(lines)


def notify(text):
    """ONE silent notification that is replaced on every report; tap = the whole report."""
    import shutil
    import subprocess
    if not shutil.which("termux-notification"):
        return
    try:
        import alert_classes
        path = alert_classes.save_full_text("REPORT", text)
        cmd = ["termux-notification", "--title", "📊 تقرير الصفقات" if _lang() == "ar" else "📊 Trades report", "--content", text, "--priority", "low", "--id", "kenan-report"]
        if path:
            cmd += ["--action", f"termux-open {path}"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STATE_FILE", signal_store.DEFAULT_FILE)
    print(build(signal_store.SignalStore(path)))
