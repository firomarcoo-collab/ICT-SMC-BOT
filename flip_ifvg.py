"""State flip and IFVG watch (analysis alerts only: they are never an order and never the bot's FINAL signal).

1. STATE FLIP: an FVG is invalidated by a FULL BODY CLOSE beyond its far edge, with displacement, and the next closed candle
   stays outside (a wick through the edge, or an immediate close back inside, keeps the old scenario alive). The gap now works
   the other way: it becomes an Inverse FVG (IFVG) and a reversal SETUP in the opposite direction starts. One flip alert per zone,
   and a cooldown per market (FLIP_COOLDOWN_MIN, default 60) so the state cannot flip back and forth.
2. IFVG RETEST: price comes back into the IFVG. Never chase the big break candle: if price runs more than 1 ATR away without
   coming back the setup is dropped.
3. IFVG CONFIRMED: after the retest a closed M5 candle (the confirmation timeframe) closes INSIDE the zone, in the trade
   direction, and there is room (at least 1R) to the next liquidity. Suggested entry (50% of the zone), stop and targets are shown
   as analysis, not as an order.
Invalidation: a full M15 body close back through the other edge of the IFVG. Everything uses closed candles only.
"""
import math
import os

import alert_classes as ac
import ny_session
import structure as st

LOOKBACK = 12              # a break older than this many M15 candles is not a fresh flip any more
MAX_BARS = 32              # an IFVG setup older than this many M15 candles expires
CHASE_ATR = 1.0            # price this far from the zone without a retest = missed, do not chase
MIN_ROOM_R = 1.0           # the next liquidity must be at least this many R away
SL_MARGIN_ATR = 0.25       # safety margin behind the invalidation level
STRONG_SESSIONS = ("London", "London / New York pre-open", "New York open", "New York")


def cooldown_seconds():
    return int(float(os.environ.get("FLIP_COOLDOWN_MIN") or 60)) * 60


def _f(v, digits):
    return f"{v:,.{digits}f}"


def _trend_word(t, lang):
    ar = {"up": "صاعد", "down": "هابط"}
    en = {"up": "up", "down": "down"}
    return (ar if lang == "ar" else en).get(t, "محايد" if lang == "ar" else "neutral")


def _side_word(side, lang):
    return side.upper()


def _once(state, key, now):
    """True the first time `key` is seen (used for print-only notes that have no setup record to hang on)."""
    seen = state.meta.setdefault("seen_notes", {})
    if key in seen:
        return False
    seen[key] = int(now)
    if len(seen) > 200:
        for k in [k for k, t in seen.items() if now - t > 86400]:
            seen.pop(k, None)
    return True


def _sid(market, z, bars):
    return f"{market}-{'buy' if z['side'] == 'bull' else 'sell'}-IFVG-{z['low']}-{z['high']}-{int(bars[z['idx']]['time'].timestamp())}"


def fresh_flips(m15):
    """IFVGs from a recent, displaced, held body-close break: [{'zone', 'side' (trade side), 'idx'}]."""
    n = len(m15)
    out = []
    for z in st.ifvgs(m15):
        x = z["idx"]
        if n - 1 - x > LOOKBACK or x >= n - 1 or z["status"] == "invalid":
            continue
        if not st.is_displacement(m15, x):
            continue
        nxt = m15[x + 1]["close"]                                  # the next closed candle must stay outside the old gap
        held = nxt < z["low"] if z["side"] == "bear" else nxt > z["high"]
        if not held:
            continue
        out.append({"zone": z, "side": "sell" if z["side"] == "bear" else "buy", "idx": x})
    return out


def _targets(m15, side, entry, calendar):
    """The next unswept liquidity levels beyond `entry` in the trade direction, nearest first."""
    lv = st.liquidity_levels(m15, 2, calendar)
    if side == "sell":
        c = sorted((l for l in lv if l["type"] == "SSL" and not l["swept"] and l["price"] < entry), key=lambda l: -l["price"])
    else:
        c = sorted((l for l in lv if l["type"] == "BSL" and not l["swept"] and l["price"] > entry), key=lambda l: l["price"])
    return c[:2]


def _confluence(m15, side, h1_trend, phase, flip_idx):
    """The four filters of the method: H1 bias, a liquidity sweep before the flip, a strong session, a true body break."""
    want = "down" if side == "sell" else "up"
    sweeps = st.find_sweeps(m15[: flip_idx + 1], recent=20)
    sweep_dir = "sell" if side == "sell" else "buy"                # a sweep of highs leads to sells (find_sweeps side = trade side)
    return {"h1": h1_trend == want, "sweep": any(s["side"] == sweep_dir for s in sweeps),
            "session": phase in STRONG_SESSIONS, "body_break": True}


def _confluence_text(c, lang):
    names = {"h1": ("H1", "اتجاه H1"), "sweep": ("sweep first", "سحب سيولة مسبق"), "session": ("strong session", "جلسة قوية"),
             "body_break": ("true body break", "كسر جسم حقيقي")}
    got = [names[k][1 if lang == "ar" else 0] for k, v in c.items() if v]
    return f"{sum(c.values())}/4" + (f" ({'، '.join(got)})" if lang == "ar" else f" ({', '.join(got)})")


def render(kind, market, side, tf, h1, session, reason, invalidation, targets, confluence, lang="en", old_side=None,
           suggested=None):
    """The three alert layouts. kind: SETUP | CONFIRMED | FLIP. Text only, no decisions."""
    ar = lang == "ar"
    head_side = side.upper()
    tgt = " / ".join(targets) if targets else ("لا سيولة واضحة" if ar else "no clear liquidity")
    if kind == "FLIP":
        old = (old_side or ("buy" if side == "sell" else "sell")).upper()
        title = (f"🔄 [STATE FLIP: {old} ❌ ➔ {head_side} REVERSAL ⚠️] — {market}")
        lines = [title,
                 (f"⏱️ الفريم: {tf} (اتجاه H1: {_trend_word(h1, lang)}) | الجلسة: {session}" if ar
                  else f"⏱️ TF: {tf} (H1 trend: {_trend_word(h1, lang)}) | Session: {session}"),
                 (f"💥 سبب التبديل: {reason}" if ar else f"💥 Reason for the flip: {reason}"),
                 ("📌 الحالة الحالية: مراقبة انعكاس (Setup Only — بانتظار إعادة الاختبار والتأكيد دون ملاحقة شمعة الكسر)" if ar
                  else "📌 Current state: reversal watch (Setup only: wait for the retest and confirmation, do not chase the break candle)"),
                 (f"🛑 مستوى الإبطال الجديد: {invalidation}" if ar else f"🛑 New invalidation: {invalidation}"),
                 (f"💧 السيولة التالية (Targets): {tgt}" if ar else f"💧 Next liquidity (targets): {tgt}")]
    else:
        mark = "🟢 [CONFIRMED]" if kind == "CONFIRMED" else "🟡 [SETUP]"
        lines = [f"{mark} — {market} | {head_side}",
                 (f"⏱️ الفريم: {tf} (اتجاه H1: {_trend_word(h1, lang)}) | الجلسة: {session}" if ar
                  else f"⏱️ TF: {tf} (H1 trend: {_trend_word(h1, lang)}) | Session: {session}"),
                 (f"🎯 السبب: {reason}" if ar else f"🎯 Reason: {reason}"),
                 (f"🛑 مستوى الإبطال (Invalidation): {invalidation}" if ar else f"🛑 Invalidation: {invalidation}"),
                 (f"💧 السيولة التالية (Targets): {tgt}" if ar else f"💧 Next liquidity (targets): {tgt}"),
                 (f"📊 التوافق (Confluence): {_confluence_text(confluence, lang)} شروط مكتملة" if ar
                  else f"📊 Confluence: {_confluence_text(confluence, lang)} conditions met")]
        if suggested:
            lines.append(("📐 مقترح للتحليل: " if ar else "📐 Suggested (analysis): ") + suggested)
    lines.append("⚠️ ملاحظة: تنبيه تحليل ومراقبة فقط — وليس أمراً تنفيذياً تلقائياً." if ar
                 else "⚠️ Note: analysis and watch alert only, not an automatic order.")
    return "\n".join(lines)


def _inside(z, price):
    return z["low"] <= price <= z["high"]


def process(state, market, m15, m5, h1_trend, now, lang="en", digits=2, calendar="utc"):
    """Creates flip setups and walks them through retest / confirmation. m15, m5: closed candles, oldest first.
    Returns [(class, sid, text)] (phone alerts) and logs nothing itself; reasons for drops come back as ('DROP', sid, text)."""
    out = []
    n = len(m15)
    if n < 40:
        return out
    now = int(now)
    phase = ny_session.phase(now)
    atr = st.atr_at(m15, n)
    cool = state.meta.setdefault("flip_cool", {})
    # 1. fresh flips -> new IFVG setups
    for fl in fresh_flips(m15):
        z, side = fl["zone"], fl["side"]
        sid = _sid(market, z, m15)
        if sid in state.setups:
            continue
        if now - int(cool.get(market, 0)) < cooldown_seconds():
            if _once(state, f"cool|{sid}", now):
                out.append(("DROP", sid, f"{market} {side.upper()} flip not announced: cooldown "
                                         f"({cooldown_seconds() // 60} min since the last flip on this market)"))
            continue
        cool[market] = now
        old_side = "buy" if side == "sell" else "sell"
        prior = next((r for r in state.setups.values() if r["market"] == market and r.get("side") == old_side
                      and (r.get("zone") or {}).get("kind") == "FVG" and math.isclose(r["zone"]["low"], z["low"], rel_tol=1e-9)
                      and math.isclose(r["zone"]["high"], z["high"], rel_tol=1e-9)), None)
        if prior is not None and not prior.get("done"):
            prior["done"] = "flipped"
        far = z["high"] if side == "sell" else z["low"]                      # a body close beyond this edge cancels the new setup
        state.setups[sid] = {"market": market, "side": side, "kind": "IFVG", "extreme": far, "zone": {"kind": "IFVG", "low": z["low"],
                             "high": z["high"]}, "phase": "watching", "created": now, "done": "", "flip_idx": fl["idx"]}
        tg = [f"{_f(t['price'], digits)} ({t['name']})" for t in _targets(m15, side, (z["low"] + z["high"]) / 2, calendar)]
        edge = "below" if side == "sell" else "above"
        reason = (f"{'bullish' if side == 'sell' else 'bearish'} FVG {_f(z['low'], digits)}-{_f(z['high'], digits)} was "
                  f"invalidated by a full-body displacement close {edge} its far edge and the next candle held outside: it is now an IFVG"
                  if lang != "ar" else
                  f"إبطال FVG {_f(z['low'], digits)}-{_f(z['high'], digits)} بإغلاق جسم شمعة كامل {'أسفل' if side == 'sell' else 'أعلى'} الحد البعيد بزخم، "
                  f"وثبات الشمعة التالية خارجه، فتحولت المنطقة إلى IFVG")
        inv = (f"{'full candle close above' if side == 'sell' else 'full candle close below'} {_f(far, digits)}" if lang != "ar"
               else f"إغلاق شمعة كاملة {'أعلى' if side == 'sell' else 'أسفل'} {_f(far, digits)}")
        conf = _confluence(m15, side, h1_trend, phase, fl["idx"])
        if state.fire(sid, "STATE_FLIP", now):
            out.append(("STATE_FLIP", sid, render("FLIP", market, side, "M15", h1_trend, phase, reason, inv, tg, conf, lang, old_side)))
    # 2. walk the open IFVG setups
    last15 = m15[-1]
    p5 = m5[-1] if m5 else None
    for sid, rec in list(state.setups.items()):
        if rec.get("market") != market or rec.get("kind") != "IFVG" or rec.get("done"):
            continue
        z, side = rec["zone"], rec["side"]
        age = (now - int(rec["created"])) / 900
        beyond = last15["close"] > z["high"] if side == "sell" else last15["close"] < z["low"]
        if beyond:
            rec["done"] = "invalidated"
            if state.fire(sid, "INVALIDATED", now):
                out.append(("INVALIDATED", sid, ac._text("INVALIDATED", {"side": side, "market": market, "level": "", "zone": z}, lang,
                            f"an M15 candle closed fully {'above' if side == 'sell' else 'below'} the IFVG ({_f(rec['extreme'], digits)})")))
            continue
        if age > MAX_BARS:
            rec["done"] = "expired"
            out.append(("DROP", sid, f"{market} {side.upper()} IFVG setup expired after {MAX_BARS} M15 candles"))
            continue
        if p5 is None:
            continue
        mid = (z["low"] + z["high"]) / 2
        touched = p5["low"] <= z["high"] and p5["high"] >= z["low"]
        away = (z["low"] - p5["close"]) if side == "sell" else (p5["close"] - z["high"])
        if rec["phase"] == "watching" and not touched and atr and away > CHASE_ATR * atr:
            rec["done"] = "missed"
            out.append(("DROP", sid, f"{market} {side.upper()} IFVG missed: price ran {away / atr:.1f} ATR away without a retest, do not chase"))
            continue
        if rec["phase"] == "watching" and touched:
            rec["phase"] = "retest"
            if state.fire(sid, "IFVG_RETEST", now):
                tg = [f"{_f(t['price'], digits)} ({t['name']})" for t in _targets(m15, side, mid, calendar)]
                inv = f"{'above' if side == 'sell' else 'below'} {_f(rec['extreme'], digits)} (full M15 close)"
                reason = (f"price returned into the IFVG {_f(z['low'], digits)}-{_f(z['high'], digits)} (50% = {_f(mid, digits)}); "
                          "waiting for a closed M5 confirmation candle inside it" if lang != "ar" else
                          f"عاد السعر إلى IFVG {_f(z['low'], digits)}-{_f(z['high'], digits)} (المنتصف {_f(mid, digits)})؛ بانتظار إغلاق شمعة M5 تأكيد داخل المنطقة")
                out.append(("IFVG_RETEST", sid, render("SETUP", market, side, "M5/M15", h1_trend, phase, reason, inv, tg,
                                                       _confluence(m15, side, h1_trend, phase, rec["flip_idx"]), lang)))
        if rec["phase"] == "retest" and not rec.get("confirmed"):
            dir_ok = p5["close"] < p5["open"] if side == "sell" else p5["close"] > p5["open"]
            if _inside(z, p5["close"]) and dir_ok and state.fire(sid, "IFVG_CONFIRMED", now):
                risk = abs(rec["extreme"] - mid) + SL_MARGIN_ATR * (atr or 0)
                sl = rec["extreme"] + SL_MARGIN_ATR * (atr or 0) if side == "sell" else rec["extreme"] - SL_MARGIN_ATR * (atr or 0)
                tg = _targets(m15, side, mid, calendar)
                room = abs(tg[0]["price"] - mid) / risk if tg and risk else 0.0
                if not tg or room < MIN_ROOM_R:
                    rec["done"] = "skipped"
                    out.append(("DROP", sid, f"{market} {side.upper()} IFVG confirmed but skipped: the next liquidity is too close "
                                             f"({room:.1f}R, at least {MIN_ROOM_R:.0f}R needed)"))
                    continue
                rec["confirmed"] = True
                rr = abs(tg[-1]["price"] - mid) / risk if risk else 0.0
                sug = (f"entry {_f(mid, digits)} (50% of the zone), stop {_f(sl, digits)}, "
                       + ", ".join(f"TP{i + 1} {_f(t['price'], digits)}" for i, t in enumerate(tg)) + f", R:R about 1:{rr:.1f}")
                inv = f"{'above' if side == 'sell' else 'below'} {_f(rec['extreme'], digits)} (full M15 close)"
                reason = ("closed M5 confirmation candle inside the IFVG after the retest, with room to the next liquidity" if lang != "ar"
                          else "إغلاق شمعة تأكيد M5 داخل IFVG بعد إعادة الاختبار مع مساحة كافية للسيولة التالية")
                out.append(("IFVG_CONFIRMED", sid, render("CONFIRMED", market, side, "M5/M15", h1_trend, phase, reason, inv,
                            [f"{_f(t['price'], digits)} ({t['name']})" for t in tg],
                            _confluence(m15, side, h1_trend, phase, rec["flip_idx"]), lang, suggested=sug)))
    state.save()
    return out
