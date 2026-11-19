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
import decisions
import ny_session
import structure as st

LOOKBACK = 12              # a break older than this many M15 candles is not a fresh flip any more
MAX_BARS = 32              # an IFVG setup older than this many M15 candles expires
CHASE_ATR = 1.0            # price this far from the zone without a retest = missed, do not chase
MIN_ROOM_R = 1.0           # the next liquidity must be at least this many R away
SL_ATR = 0.5               # Hard SL: behind the sweep / swing extreme by this many ATR (IFVG_SL_ATR overrides)
SWING_BARS = 24            # M15 candles looked at (up to the break) for the sweep / swing extreme
DEPTH_LOW, DEPTH_HIGH = 0.50, 0.75   # optimal entry: 50%-75% into the IFVG, measured from the edge price comes from
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


def ifvg_score(conf, h1_trend, room_r):
    """Transparent 100-point rating of a confirmed IFVG setup (a technical rating, NOT a win probability)."""
    parts = {"htf": 25 if conf["h1"] else (10 if h1_trend not in ("up", "down") else 0),
             "sweep": 20 if conf["sweep"] else 0, "session": 15 if conf["session"] else 0,
             "displacement": 15, "retest_confirm": 15,
             "room": 10 if room_r >= 2 else 5 if room_r >= MIN_ROOM_R else 0}
    return sum(parts.values()), parts


def min_score(h1_aligned_or_neutral):
    base = int(float(os.environ.get("IFVG_MIN_SCORE") or 70))
    return base if h1_aligned_or_neutral else max(base, 80)            # against the H1 trend: stricter


def cost_check(risk, reward, spread, commission_price, cfg):
    """(ok, reason): spread / commission / slippage against the stop and the net and gross R:R of the final target."""
    if spread is None and not cfg.allow_unknown_cost:
        return False, "spread unknown (set SPREAD_<SYMBOL> or fill markets.json)"
    cost = (spread or 0.0) * cfg.spread_margin + (commission_price or 0.0) + cfg.slippage
    if risk <= 0:
        return False, "no stop distance"
    if cost / risk > cfg.max_cost_frac:
        return False, f"cost {cost:.4g} is {cost / risk:.0%} of the stop (limit {cfg.max_cost_frac:.0%})"
    min_rr = float(os.environ.get("IFVG_MIN_RR") or 2.0)
    if reward / risk < min_rr:
        return False, f"R:R 1:{reward / risk:.1f} to the final target is under 1:{min_rr:g}"
    net = (reward - cost) / (risk + cost)
    if net < cfg.min_net_rr:
        return False, f"net R:R {net:.2f} after costs is under {cfg.min_net_rr}"
    return True, "" if spread is not None else "COST UNVERIFIED"


def _pending(state, sid, market, side, now, lang, **plan):
    """What decide.py needs to answer ENTER / WAIT / IGNORE for this alert (kept one day)."""
    pend = state.meta.setdefault("pending", {})
    pend[sid] = dict(plan, market=market, side=side, lang=lang, created=int(now))
    for k in [k for k, v in pend.items() if now - v.get("created", now) > 86400]:
        pend.pop(k, None)


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


FOUR = (("h1", "H1 bias", "توافق الفريم الأعلى"), ("sweep", "liquidity sweep first", "سحب السيولة المسبق"),
        ("body_break", "true body break (IFVG)", "كسر الجسم الحقيقي IFVG"), ("session_confirm", "session + confirmation candle", "الجلسة وشمعة التأكيد"))
EFFECT = {"h1": ("H1 is not with the trade: smaller risk and the stricter score applies",
                 "H1 غير موافق: خفّف المخاطرة ويُطبَّق حد تقييم أعلى"),
          "sweep": ("no prior liquidity sweep: weaker setup, smaller risk",
                    "لا سحب سيولة مسبق: إعداد أضعف، خفّف المخاطرة"),
          "body_break": ("not a true body break: a wick is not a flip, wait",
                         "ليس كسر جسم حقيقياً: الذيل ليس تبدلاً، انتظر"),
          "session_confirm": ("no strong session or no closed confirmation candle yet: wait for the closed M5 candle inside the zone",
                              "لا جلسة قوية أو لا شمعة تأكيد مغلقة بعد: انتظر إغلاق M5 داخل المنطقة")}


def four_view(c, confirmed):
    """The four conditions of the method: [(key, english, arabic, met)]. Condition 4 needs the closed confirmation candle."""
    met = {"h1": c["h1"], "sweep": c["sweep"], "body_break": c["body_break"], "session_confirm": bool(c["session"] and confirmed)}
    return [(k, en, ar, met[k]) for k, en, ar in FOUR]


def _confluence_text(c, lang):
    names = {"h1": ("H1", "اتجاه H1"), "sweep": ("sweep first", "سحب سيولة مسبق"), "session": ("strong session", "جلسة قوية"),
             "body_break": ("true body break", "كسر جسم حقيقي")}
    got = [names[k][1 if lang == "ar" else 0] for k, v in c.items() if v]
    return f"{sum(c.values())}/4" + (f" ({'، '.join(got)})" if lang == "ar" else f" ({', '.join(got)})")


def confluence_block(c, confirmed, lang="en"):
    """Score [X/4], completed, missing and what the missing ones mean for the trade (in every alert)."""
    v = four_view(c, confirmed)
    ar = lang == "ar"
    done = [(a if ar else e) for k, e, a, m in v if m]
    miss = [(a if ar else e) for k, e, a, m in v if not m]
    effect = [EFFECT[k][1 if ar else 0] for k, e, a, m in v if not m]
    sep = "، " if ar else ", "
    if ar:
        return "\n".join([f"📊 التوافق (Confluence): [{len(done)}/4]", f"- المكتملة: {sep.join(done) or 'لا يوجد'}",
                          f"- الناقصة: {sep.join(miss) or 'لا يوجد'}",
                          f"- أثر النواقص: {'؛ '.join(effect) or 'توافق كامل، لا أثر'}"])
    return "\n".join([f"📊 Confluence: [{len(done)}/4]", f"- Completed: {sep.join(done) or 'none'}",
                      f"- Missing: {sep.join(miss) or 'none'}",
                      f"- Effect of the missing: {'; '.join(effect) or 'full confluence, no effect'}"])


def render(kind, market, side, tf, h1, session, reason, invalidation, targets, confluence, lang="en", old_side=None,
           suggested=None, score=None, parts=None):
    """The three alert layouts. kind: SETUP | CONFIRMED | FLIP. Text only, no decisions."""
    ar = lang == "ar"
    head_side = side.upper()
    tgt = " / ".join(targets) if targets else ("لا سيولة واضحة" if ar else "no clear liquidity")
    if kind == "FINAL":
        ttl = os.environ.get("SIGNAL_TTL_HOURS") or "6"
        lines = [f"🟩 KENAN TRADER AI — IFVG SIGNAL | {market} | {head_side}",
                 (f"التقييم: {score}/100 (تقييم فني وليس احتمال ربح) | الفريم: {tf} (اتجاه H1: {_trend_word(h1, lang)}) | الجلسة: {session}" if ar
                  else f"Score: {score}/100 (a technical rating, NOT a win probability) | TF: {tf} (H1 trend: {_trend_word(h1, lang)}) | Session: {session}"),
                 (f"🎯 السبب: {reason}" if ar else f"🎯 Reason: {reason}"),
                 (f"📐 {suggested}" if suggested else ""),
                 (f"🛑 الإبطال: {invalidation}" if ar else f"🛑 Invalidation: {invalidation}"),
                 (f"💧 الأهداف: {tgt}" if ar else f"💧 Targets: {tgt}"),
                 confluence_block(confluence, True, lang),
                 (f"🧮 نقاط التقييم: {parts}" if ar else f"🧮 Score parts: {parts}"),
                 (f"⏳ صلاحية الإشارة: {ttl} ساعات | الحالة: CONFIRMED" if ar else f"⏳ Signal expiry: {ttl} h | Status: CONFIRMED"),
                 ("⚠️ إشارة فقط: نفّذ الصفقة بنفسك في MT5. لا نسبة نجاح مضمونة." if ar
                  else "⚠️ Signal only: place the trade yourself in MT5. No win rate is guaranteed.")]
        return "\n".join(l for l in lines if l)
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
                 (f"💧 السيولة التالية (Targets): {tgt}" if ar else f"💧 Next liquidity (targets): {tgt}"),
                 confluence_block(confluence, False, lang)]
    else:
        mark = "🟢 [CONFIRMED]" if kind == "CONFIRMED" else "🟡 [SETUP]"
        lines = [f"{mark} — {market} | {head_side}",
                 (f"⏱️ الفريم: {tf} (اتجاه H1: {_trend_word(h1, lang)}) | الجلسة: {session}" if ar
                  else f"⏱️ TF: {tf} (H1 trend: {_trend_word(h1, lang)}) | Session: {session}"),
                 (f"🎯 السبب: {reason}" if ar else f"🎯 Reason: {reason}"),
                 (f"🛑 مستوى الإبطال (Invalidation): {invalidation}" if ar else f"🛑 Invalidation: {invalidation}"),
                 (f"💧 السيولة التالية (Targets): {tgt}" if ar else f"💧 Next liquidity (targets): {tgt}"),
                 confluence_block(confluence, kind == "CONFIRMED", lang)]
        if suggested:
            lines.append(("📐 مقترح للتحليل: " if ar else "📐 Suggested (analysis): ") + suggested)
    lines.append("⚠️ ملاحظة: تنبيه تحليل ومراقبة فقط — وليس أمراً تنفيذياً تلقائياً." if ar
                 else "⚠️ Note: analysis and watch alert only, not an automatic order.")
    return "\n".join(lines)


def structural_extreme(m15, side, flip_idx, zone_edge):
    """Highest high (SELL) / lowest low (BUY) of the last SWING_BARS M15 candles up to the break: the sweep or swing extreme the
    Hard SL must sit behind. Never tighter than the IFVG's own far edge."""
    seg = m15[max(0, flip_idx - SWING_BARS + 1): flip_idx + 1]
    if not seg:
        return zone_edge
    return max([zone_edge] + [b["high"] for b in seg]) if side == "sell" else min([zone_edge] + [b["low"] for b in seg])


def optimal_entry(zone, side, close):
    """Entry at 50%-75% depth of the IFVG (62.5% target). Returns (price, is_market): if the confirmation close is already inside
    the band or deeper it is a market entry at that close, otherwise a limit order at the 62.5% level."""
    w = zone["high"] - zone["low"]
    if side == "sell":
        lo, tgt = zone["low"] + DEPTH_LOW * w, zone["low"] + (DEPTH_LOW + DEPTH_HIGH) / 2 * w
        return (close, True) if close >= lo else (tgt, False)
    hi, tgt = zone["high"] - DEPTH_LOW * w, zone["high"] - (DEPTH_LOW + DEPTH_HIGH) / 2 * w
    return (close, True) if close <= hi else (tgt, False)


def hard_sl(side, extreme, atr):
    m = float(os.environ.get("IFVG_SL_ATR") or SL_ATR) * (atr or 0)
    return extreme + m if side == "sell" else extreme - m


def _inside(z, price):
    return z["low"] <= price <= z["high"]


def process(state, market, m15, m5, h1_trend, now, lang="en", digits=2, calendar="utc", gate=None):
    """Creates flip setups and walks them through retest / confirmation. m15, m5: closed candles, oldest first.
    Returns [(class, sid, text)] (phone alerts); reasons for drops come back as ('DROP', sid, text).
    gate(plan) -> (ok, reason) is the caller's last word before a FINAL signal (cost check, one open signal per market); without
    a gate an IFVG is never more than an analysis alert (IFVG_CONFIRMED). rec['plan'] holds the signal setup of an IFVG_SIGNAL."""
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
        conf0 = _confluence(m15, side, h1_trend, phase, fl["idx"])
        state.setups[sid] = {"market": market, "side": side, "kind": "IFVG", "extreme": far, "zone": {"kind": "IFVG", "low": z["low"],
                             "high": z["high"]}, "phase": "watching", "created": now, "done": "", "flip_idx": fl["idx"], "conf": conf0}
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
            wait = (f"wait for price to come back into {_f(z['low'], digits)}-{_f(z['high'], digits)} and for a closed M5 candle inside it in the "
                    f"{side.upper()} direction; cancel if an M15 candle closes fully {'above' if side == 'sell' else 'below'} {_f(far, digits)}"
                    if lang != "ar" else
                    f"انتظر عودة السعر إلى {_f(z['low'], digits)}-{_f(z['high'], digits)} وإغلاق شمعة M5 داخلها في اتجاه {side.upper()}؛ "
                    f"ويُلغى إذا أغلقت M15 كاملة {'أعلى' if side == 'sell' else 'أسفل'} {_f(far, digits)}")
            _pending(state, sid, market, side, now, lang, wait=wait, entry=None, invalid=inv)
            out.append(("STATE_FLIP", sid, render("FLIP", market, side, "M15", h1_trend, phase, reason, inv, tg, conf, lang, old_side)
                        + "\n" + decisions.options_text(sid, lang)))
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
                            f"an M15 candle closed fully {'above' if side == 'sell' else 'below'} the IFVG ({_f(rec['extreme'], digits)})")
                            + "\n" + confluence_block(rec.get("conf") or {"h1": False, "sweep": False, "session": False, "body_break": True},
                                                      bool(rec.get("confirmed")), lang)))
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
                _pending(state, sid, market, side, now, lang, entry=None, invalid=inv,
                         wait=(f"a closed M5 candle must close inside {_f(z['low'], digits)}-{_f(z['high'], digits)} in the {side.upper()} direction; "
                               f"skip it if an M15 candle closes fully {'above' if side == 'sell' else 'below'} {_f(rec['extreme'], digits)}"))
                out.append(("IFVG_RETEST", sid, render("SETUP", market, side, "M5/M15", h1_trend, phase, reason, inv, tg,
                                                       _confluence(m15, side, h1_trend, phase, rec["flip_idx"]), lang)
                            + "\n" + decisions.options_text(sid, lang)))
        if rec["phase"] == "retest" and not rec.get("confirmed"):
            dir_ok = p5["close"] < p5["open"] if side == "sell" else p5["close"] > p5["open"]
            if _inside(z, p5["close"]) and dir_ok and state.fire(sid, "IFVG_CONFIRMED", now):
                entry, is_market = optimal_entry(z, side, p5["close"])
                sw_ext = structural_extreme(m15, side, rec["flip_idx"], z["high"] if side == "sell" else z["low"])
                sl = hard_sl(side, sw_ext, atr)
                risk = abs(sl - entry)
                tg = _targets(m15, side, entry, calendar)
                room = abs(tg[0]["price"] - entry) / risk if tg and risk else 0.0
                if not tg or room < MIN_ROOM_R:
                    rec["done"] = "skipped"
                    out.append(("DROP", sid, f"{market} {side.upper()} IFVG confirmed but skipped: the next liquidity is too close "
                                             f"({room:.1f}R, at least {MIN_ROOM_R:.0f}R needed)"))
                    continue
                rec["confirmed"] = True
                final_t = tg[-1]["price"]
                reward = abs(final_t - entry)
                rr = reward / risk if risk else 0.0
                conf = _confluence(m15, side, h1_trend, phase, rec["flip_idx"])
                score, parts = ifvg_score(conf, h1_trend, room)
                aligned = conf["h1"] or h1_trend not in ("up", "down")
                inv = f"{'above' if side == 'sell' else 'below'} {_f(rec['extreme'], digits)} (full M15 close)"
                tps = [_f(t["price"], digits) for t in tg]
                tgt_txt = [f"{_f(t['price'], digits)} ({t['name']})" for t in tg]
                be = " (move the stop to break-even there)"
                sug = (("Optimal Entry " + _f(entry, digits) + (" (market: the closed M5 candle is already in the 50-75% band)" if is_market
                                                                else " (LIMIT at 62.5% depth of the IFVG)"))
                       + f"\n🛑 Hard SL {_f(sl, digits)} (behind the {'sweep/swing high' if side == 'sell' else 'sweep/swing low'} "
                         f"{_f(sw_ext, digits)} + {os.environ.get('IFVG_SL_ATR') or SL_ATR} ATR; not at the IFVG edge)"
                       + f"\n🚪 Early Exit: an M15 candle closing fully {'above' if side == 'sell' else 'below'} {_f(rec['extreme'], digits)}"
                       + "".join(f"\n🎯 TP{i + 1} {_f(t['price'], digits)}" + (be if i == 0 else "") for i, t in enumerate(tg))
                       + f"\n⚖️ R:R about 1:{rr:.1f} (from the structural stop)")
                reason = ("closed M5 confirmation candle inside the IFVG after the retest, with room to the next liquidity" if lang != "ar"
                          else "إغلاق شمعة تأكيد M5 داخل IFVG بعد إعادة الاختبار مع مساحة كافية للسيولة التالية")
                plan = {"entry": _f(entry, digits), "sl": _f(sl, digits), "tps": tps, "rr": f"{rr:.1f}", "invalid": inv,
                        "early_exit": inv, "limit": not is_market,
                        "wait": "this setup is already confirmed: if you did not enter, do not chase it once price is more than 0.5 ATR "
                                "from the entry; cancel it if an M15 candle closes fully beyond the invalidation"}
                age = now - (int(p5["time"].timestamp()) + 300)
                max_age = int(float(os.environ.get("IFVG_MAX_AGE") or 420))
                gate_why = ""
                final = False
                if score < min_score(aligned):
                    gate_why = f"score {score}/100 is under {min_score(aligned)}" + ("" if aligned else " (against the H1 trend: stricter)")
                elif age > max_age:
                    gate_why = f"the confirmation candle closed {age} s ago (limit {max_age} s): not issued late"
                elif gate is not None:
                    setup = {"side": side, "entry": float(entry), "sl": float(sl), "tp": float(final_t), "tp1": float(tg[0]["price"]),
                             "mode": "flip", "profile": "ifvg", "bar_time": int(p5["time"].timestamp()),
                             "uses": [f"ifvg|{z['low']}|{z['high']}|{rec['flip_idx']}"], "score100": score, "invalidation": inv,
                             "market_entry": is_market, "early_exit": inv,
                             "zones": [f"IFVG {z['low']}-{z['high']}"], "parts": parts, "h1": h1_trend or "neutral", "session": phase}
                    ok, why = gate({"risk": risk, "reward": reward, "setup": setup})
                    if ok:
                        final = True
                        rec["plan"] = setup
                        plan["note"] = f"score {score}/100"
                    else:
                        gate_why = why
                else:
                    gate_why = "analysis only (no signal gate)"
                _pending(state, sid, market, side, now, lang, **plan)
                parts_txt = ", ".join(f"{k} {v}" for k, v in parts.items())
                if final:
                    out.append(("IFVG_SIGNAL", sid, render("FINAL", market, side, "M5 confirm / M15", h1_trend, phase, reason, inv, tgt_txt, conf,
                                                          lang, suggested=sug, score=score, parts=parts_txt)
                                + "\n" + decisions.options_text(sid, lang)))
                else:
                    out.append(("IFVG_CONFIRMED", sid, render("CONFIRMED", market, side, "M5/M15", h1_trend, phase, reason, inv, tgt_txt, conf,
                                                              lang, suggested=sug)
                                + f"\n⛔ NOT issued as a signal: {gate_why}" + f" (score {score}/100: {parts_txt})\n" + decisions.options_text(sid, lang)))
                    if gate is not None:
                        out.append(("DROP", sid, f"{market} {side.upper()} IFVG not issued as a signal: {gate_why}"))
    state.save()
    return out
