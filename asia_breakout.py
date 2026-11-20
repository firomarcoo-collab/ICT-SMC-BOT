"""Gold (XAUUSD) Asia-range breakout, SIGNALS ONLY, closed M15 candles only. It never trades.

Asia range = 19:00-03:00 New York time. Window: a breakout candle must close 03:00-08:00 ET, nothing new after 09:30 ET.
1. Range filter  : width between 1.0 and 3.0 x H1 ATR (a tiny or bloated range is skipped).
2. Breakout      : a closed M15 candle beyond the range edge by >= 0.1 ATR(M15), body >= 50% of its range, tick volume >= 1.2 x
                   the 20-candle average (checked only when the feed has volume), and the H1 structure trend agrees.
3. Retest        : price comes back to the edge; two closes back inside the range cancel the setup; > 1 ATR away without a
                   retest = missed, do not chase.
4. Confirmation  : a closed M15 candle in the breakout direction that closes beyond the edge after the retest.
Plan            : Hard SL behind the retest extreme +/- 0.5 ATR (never at the range edge), Early Exit = an M15 close back inside the
                  range, TP1 1R (stop to break-even), TP2 2R, TP3 3R or the next liquidity. The DXY filter is NOT used: the bot has no
                  DXY feed, and the correlation is unstable (test it before trusting it).
These numbers are starting hypotheses, not measured results."""
import os

import alert_classes as ac
import decisions
import flip_ifvg as fl
import ny_session
import structure as st
from datetime import time as dtime

MIN_RANGE_H1, MAX_RANGE_H1 = 1.0, 3.0
BREAK_ATR, BODY_FRAC, VOL_MULT = 0.1, 0.5, 1.2
WIN_START, WIN_END, NO_NEW = dtime(3, 0), dtime(8, 0), dtime(9, 30)
MAX_BARS = 24                      # a setup older than 6 hours expires
CHASE_ATR = 1.0


def enabled(market):
    return market == "XAUUSD" and os.environ.get("ASIA_BREAKOUT", "1") != "0"


def sl_atr():
    return float(os.environ.get("ASIA_SL_ATR") or 0.5)


def _t(bar):
    return ny_session._local(bar).replace(tzinfo=None)


def _qualifies(m15, i, edge, side, atr):
    """Quality of the breakout candle i: (ok, why)."""
    b = m15[i]
    rng = b["high"] - b["low"]
    if rng <= 0 or abs(b["close"] - b["open"]) < BODY_FRAC * rng:
        return False, "the breakout candle body is under 50% of its range (a wick, not a body break)"
    beyond = (b["close"] - edge) if side == "buy" else (edge - b["close"])
    if beyond < BREAK_ATR * atr:
        return False, f"the close is less than {BREAK_ATR} ATR beyond the range edge"
    vols = [x.get("volume") or 0.0 for x in m15[max(0, i - 20):i]]
    if sum(vols) > 0 and (b.get("volume") or 0.0) < VOL_MULT * (sum(vols) / len(vols)):
        return False, f"volume is under {VOL_MULT}x the 20-candle average"
    return True, ""


def find_break(m15, asia, atr_at, today):
    """The FIRST candle after 03:00 ET (before 08:00) that closes outside the range: (side, idx) or (None, None)."""
    for i, b in enumerate(m15):
        t = _t(b)
        if t < today.replace(hour=3, minute=0) or t.time() >= WIN_END or t.date() != today.date():
            continue
        if b["close"] > asia["high"]:
            return "buy", i
        if b["close"] < asia["low"]:
            return "sell", i
    return None, None


def _plan_text(side, entry, sl, ext, tps, rr, early, digits, lang):
    f = lambda v: fl._f(v, digits)                                                                 # noqa: E731
    ar = lang == "ar"
    be = " (انقل الوقف إلى التعادل)" if ar else " (move the stop to break-even)"
    ex = "قمة" if side == "sell" else "قاع"
    lines = [("📌 خطة الصفقة:" if ar else "📌 Trade plan:"),
             (f"- الدخول: {f(entry)} (إغلاق شمعة التأكيد M15)" if ar else f"- Entry: {f(entry)} (the closed M15 confirmation candle)"),
             (f"- وقف الخسارة (Hard SL): {f(sl)} خلف {ex} إعادة الاختبار {f(ext)} {'+' if side == 'sell' else '-'} {sl_atr()} ATR"
              if ar else f"- Hard SL: {f(sl)} behind the retest {'high' if side == 'sell' else 'low'} {f(ext)} "
                         f"{'+' if side == 'sell' else '-'} {sl_atr()} ATR (not at the range edge)"),
             (f"- الخروج المبكر: إغلاق M15 كامل داخل النطاق عند {f(early)}" if ar else f"- Early Exit: an M15 close back inside the range at {f(early)}"),
             f"- TP1 {f(tps[0])}" + be, f"- TP2 {f(tps[1])}", f"- TP3 {f(tps[2])}",
             (f"- R:R: 1:{rr:.1f} (محسوب من الوقف الهيكلي)" if ar else f"- R:R: 1:{rr:.1f} (from the structural stop)")]
    return "\n".join(lines)


def render(kind, side, asia, h1, session, conf, lang, digits, plan_text="", note="", reason=""):
    f = lambda v: fl._f(v, digits)                                                                 # noqa: E731
    ar = lang == "ar"
    mark = {"SETUP": "🟡 [SETUP ONLY — انتظر التأكيد]" if ar else "🟡 [SETUP ONLY - wait for the confirmation]",
            "CONFIRMED": "🟢 [CONFIRMED ENTRY]", "INVALIDATED": "🔴 [SETUP INVALIDATED]"}[kind]
    lines = [f"{mark} — XAUUSD | {side.upper()} | ASIA BREAKOUT",
             (f"⏱️ الفريم: M15 (اتجاه H1: {fl._trend_word(h1, lang)}) | الجلسة: {session}" if ar
              else f"⏱️ TF: M15 (H1 trend: {fl._trend_word(h1, lang)}) | Session: {session}"),
             (f"🌏 النطاق الآسيوي: {f(asia['low'])} - {f(asia['high'])}" if ar else f"🌏 Asia range: {f(asia['low'])} - {f(asia['high'])}")]
    if reason:
        lines.append(("🎯 السبب: " if ar else "🎯 Reason: ") + reason)
    lines.append(fl.confluence_block(conf, kind == "CONFIRMED", lang))
    if plan_text:
        lines.append(plan_text)
    if note:
        lines.append(note)
    lines.append("ℹ️ DXY filter not used (no DXY feed)." if not ar else "ℹ️ فلتر DXY غير مستخدم (لا يوجد مصدر DXY).")
    lines.append("⚠️ إشارة فقط: نفّذ بنفسك في MT5. لا نسبة نجاح مضمونة." if ar else "⚠️ Signal only: place the trade yourself in MT5. No win rate is guaranteed.")
    return "\n".join(lines)


def process(state, market, m15, h1, h1_trend, now, lang="en", digits=2, calendar="utc", gate=None):
    """[(class, sid, text)]: ASIA_SETUP / ASIA_CONFIRMED / ASIA_SIGNAL / INVALIDATED / DROP (reasons)."""
    out = []
    if not enabled(market) or len(m15) < 60:
        return out
    now = int(now)
    et = ny_session._et(now)
    lv = ny_session.levels(m15, now)
    asia = lv.get("asia")
    n = len(m15)
    atr = st.atr_at(m15, n)
    today = et.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    day = today.strftime("%Y%m%d")
    phase = ny_session.phase(now)
    last = m15[-1]
    # 1. a new setup
    if asia and atr and et.weekday() < 5 and WIN_START <= et.time() < WIN_END:
        side, i = find_break(m15, asia, None, today)
        sid = f"{market}-ASIA-{side}-{day}" if side else None
        if side and sid not in state.setups and fl._once(state, f"asia|{sid}", now):
            ok, why = True, ""
            h1atr = st.atr_at(h1, len(h1)) if len(h1) > 20 else None
            width = asia["high"] - asia["low"]
            if not h1atr:
                ok, why = False, "not enough H1 candles for the range filter"
            elif not (MIN_RANGE_H1 * h1atr <= width <= MAX_RANGE_H1 * h1atr):
                ok, why = False, f"the Asia range ({width:.2f}) is not between {MIN_RANGE_H1} and {MAX_RANGE_H1} x H1 ATR ({h1atr:.2f})"
            if ok:
                ok, why = _qualifies(m15, i, asia["high"] if side == "buy" else asia["low"], side, st.atr_at(m15, i) or atr)
            if ok and h1_trend != ("up" if side == "buy" else "down"):
                ok, why = False, f"the H1 trend ({h1_trend or 'neutral'}) does not agree with a {side.upper()}"
            if not ok:
                out.append(("DROP", sid, f"XAUUSD {side.upper()} Asia breakout rejected: {why}"))
            elif now - int(m15[i]["time"].timestamp()) > 900 * 12:
                out.append(("DROP", sid, f"XAUUSD {side.upper()} Asia breakout is older than 12 candles: not announced"))
            else:
                edge = asia["high"] if side == "buy" else asia["low"]
                conf = {"h1": True, "sweep": True, "body_break": True, "session": phase in fl.STRONG_SESSIONS}
                state.setups[sid] = {"market": market, "side": side, "kind": "ASIA", "level": edge, "range": [asia["low"], asia["high"]],
                                     "created": now, "break_ts": int(m15[i]["time"].timestamp()), "break_close": m15[i]["close"], "phase": "watching", "done": "",
                                     "atr": atr, "conf": conf}
                tail = ("إغلاق شمعة M15 فوق " if side == "buy" else "إغلاق شمعة M15 تحت ") if lang == "ar" else \
                       ("a closed M15 candle above " if side == "buy" else "a closed M15 candle below ")
                note = (f"⏳ المطلوب: إعادة اختبار الحافة {fl._f(edge, digits)} ثم {tail}{fl._f(edge, digits)}. "
                        f"الوقف الهيكلي المقترح: خلف {'قاع' if side == 'buy' else 'قمة'} إعادة الاختبار {'-' if side == 'buy' else '+'} {sl_atr()} ATR."
                        if lang == "ar" else
                        f"⏳ Needed: a retest of the edge {fl._f(edge, digits)}, then {tail}{fl._f(edge, digits)}. Suggested structural stop: "
                        f"behind the retest {'low' if side == 'buy' else 'high'} {'-' if side == 'buy' else '+'} {sl_atr()} ATR.")
                reason = (f"إغلاق جسم M15 {'فوق' if side == 'buy' else 'تحت'} النطاق الآسيوي مع حجم واتجاه H1 متوافق" if lang == "ar"
                          else f"M15 body close {'above' if side == 'buy' else 'below'} the Asia range with volume and the H1 trend agreeing")
                out.append(("ASIA_SETUP", sid, render("SETUP", side, asia, h1_trend, phase, dict(conf, session=False), lang, digits,
                                                      note=note, reason=reason) + "\n" + decisions.options_text(sid, lang)))
    # 2. walk the open setups
    for sid, rec in list(state.setups.items()):
        if rec.get("market") != market or rec.get("kind") != "ASIA" or rec.get("done"):
            continue
        side, edge = rec["side"], rec["level"]
        lo, hi = rec["range"]
        asia_r = {"low": lo, "high": hi}
        after = [b for b in m15 if int(b["time"].timestamp()) > rec["break_ts"]]
        if (now - int(rec["created"])) / 900 > MAX_BARS or et.time() >= NO_NEW and et.date() == today.date():
            rec["done"] = "expired"
            out.append(("DROP", sid, f"XAUUSD {side.upper()} Asia breakout expired (no confirmation before 09:30 ET / {MAX_BARS} candles)"))
            continue
        if len(after) >= 2 and all(lo <= b["close"] <= hi for b in after[-2:]):
            rec["done"] = "invalidated"
            if state.fire(sid, "INVALIDATED", now):
                out.append(("INVALIDATED", sid, render("INVALIDATED", side, asia_r, h1_trend, phase, rec["conf"], lang, digits,
                            reason="two M15 closes back inside the Asia range: a fake breakout. Next: watch the opposite edge or the next session.")))
            continue
        a = rec["atr"] or atr or 0.0
        tol = BREAK_ATR * a
        idx = next((k for k, b in enumerate(after) if (b["low"] <= edge + tol if side == "buy" else b["high"] >= edge - tol)), None)
        if idx is None:
            bc = rec.get("break_close", edge)                      # how far price ran beyond the breakout candle itself
            away = (last["close"] - bc) if side == "buy" else (bc - last["close"])
            if after and a and away > CHASE_ATR * a:
                rec["done"] = "missed"
                out.append(("DROP", sid, f"XAUUSD {side.upper()} Asia breakout missed: price ran {away / a:.1f} ATR away without a retest, do not chase"))
            continue
        seg = after[idx:]
        ext = min(b["low"] for b in seg) if side == "buy" else max(b["high"] for b in seg)
        rec["phase"] = "retest"
        ok_dir = last["close"] > last["open"] if side == "buy" else last["close"] < last["open"]
        beyond = last["close"] > edge if side == "buy" else last["close"] < edge
        if not (ok_dir and beyond and last in seg) or rec.get("confirmed") or et.time() >= NO_NEW or et.time() < WIN_START:
            continue
        if not state.fire(sid, "ASIA_CONFIRMED", now):
            continue
        entry = last["close"]
        sl = ext - sl_atr() * a if side == "buy" else ext + sl_atr() * a
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        sgn = 1 if side == "buy" else -1
        tp1, tp2 = entry + sgn * risk, entry + sgn * 2 * risk
        tp3 = entry + sgn * 3 * risk
        for t in fl._targets(m15, "buy" if side == "buy" else "sell", entry, calendar):
            if abs(t["price"] - entry) >= 2 * risk:
                tp3 = t["price"]
                break
        rr = 2.0
        rec["confirmed"] = True
        early = edge
        plan = _plan_text(side, entry, sl, ext, [tp1, tp2, tp3], rr, early, digits, lang)
        pend = {"entry": fl._f(entry, digits), "sl": fl._f(sl, digits), "tps": [fl._f(x, digits) for x in (tp1, tp2, tp3)], "rr": "2.0",
                "invalid": f"an M15 close back inside the range at {fl._f(edge, digits)}", "early_exit": fl._f(edge, digits), "limit": False,
                "wait": "this setup is already confirmed: do not chase it once price is more than 0.5 ATR from the entry"}
        age = now - (int(last["time"].timestamp()) + 900)
        max_age = int(float(os.environ.get("ASIA_MAX_AGE") or 420))
        reason = ("إغلاق شمعة تأكيد M15 بعد إعادة اختبار حافة النطاق" if lang == "ar" else "closed M15 confirmation candle after the retest of the range edge")
        conf = dict(rec["conf"], session=phase in fl.STRONG_SESSIONS)
        gate_why, final = "", False
        if age > max_age:
            gate_why = f"the confirmation candle closed {age} s ago (limit {max_age} s): not issued late"
        elif gate is not None:
            setup = {"side": side, "entry": float(entry), "sl": float(sl), "tp": float(tp2), "tp1": float(tp1), "mode": "asia",
                     "profile": "asia", "bar_time": int(last["time"].timestamp()), "uses": [f"asia|{day}|{side}"],
                     "market_entry": True, "early_exit": pend["early_exit"], "h1": h1_trend or "neutral", "session": phase}
            ok, why = gate({"risk": risk, "reward": 2.0 * risk, "setup": setup})
            if ok:
                final = True
                rec["plan"] = setup
            else:
                gate_why = why
        else:
            gate_why = "analysis only (no signal gate)"
        fl._pending(state, sid, market, side, now, lang, **pend)
        if final:
            out.append(("ASIA_SIGNAL", sid, render("CONFIRMED", side, asia_r, h1_trend, phase, conf, lang, digits, plan_text=plan, reason=reason)
                        + "\n" + decisions.options_text(sid, lang)))
        else:
            out.append(("ASIA_CONFIRMED", sid, render("CONFIRMED", side, asia_r, h1_trend, phase, conf, lang, digits, plan_text=plan, reason=reason,
                                                      note=f"⛔ NOT issued as a signal: {gate_why}") + "\n" + decisions.options_text(sid, lang)))
            if gate is not None:
                out.append(("DROP", sid, f"XAUUSD {side.upper()} Asia breakout not issued as a signal: {gate_why}"))
    state.save()
    return out
