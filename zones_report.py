"""Entry-zones report and setup quality grade. INFORMATION ONLY: never a signal, never a trade.
The bot's tracker already finds live setups (liquidity sweep -> MSS -> FVG/OB zone). This module keeps the latest view per
market, grades each setup and prints / phones ONE list of the zones near price.
Grade = how many of 5 checks are true (sweep, MSS with displacement, a fresh zone, price in / near the zone, H1 agrees).
It counts conditions; it is NOT a probability of winning. On demand it is printed every ZONES_REPORT_MINUTES (default 30; 0 = off)."""
import os
import time

LATEST = {}          # market -> {"ts", "last", "obs": [...], "h1": 'up' / 'down' / None}

GRADE_AR = {"STRONG": "قوية", "MEDIUM": "متوسطة", "WEAK": "ضعيفة"}
SIDE_AR = {"buy": "شراء", "sell": "بيع"}
REL_EN = {"in": "price is in the zone", "near": "price is near the zone", "far": "price is far from the zone"}
REL_AR = {"in": "السعر داخل المنطقة", "near": "السعر قريب من المنطقة", "far": "السعر بعيد عن المنطقة"}


def record(market, observations, last_close, now_ts, h1):
    LATEST[market] = {"ts": now_ts, "last": last_close, "obs": list(observations), "h1": h1}


def grade(obs, h1=None):
    """Returns (grade, score out of 5, missing list)."""
    want = "up" if obs["side"] == "buy" else "down"
    checks = [("liquidity sweep", True), ("MSS with displacement", bool(obs.get("stage3"))),
              ("fresh FVG/OB zone", bool(obs.get("zone"))),
              ("price in / near the zone", obs.get("rel") in ("in", "near")),
              ("H1 agrees", h1 == want)]
    score = sum(1 for _, ok in checks if ok)
    missing = [n for n, ok in checks if not ok]
    mandatory = bool(obs.get("stage3")) and bool(obs.get("zone"))
    g = "STRONG" if score >= 4 and mandatory else "MEDIUM" if score >= 3 else "WEAK"
    return g, score, missing


def v2_line(market, now_ts, ar=False):
    """Kenan v2 entry rules for this market right now (session table). Empty when KENAN_V2=0."""
    if os.environ.get("KENAN_V2", "1") == "0":
        return ""
    import session_engine
    st = session_engine.status(market, now_ts or time.time())
    ok = st["allowed"]
    if ar:
        return (f"  Kenan v2 ({st['session']}): " + ("الدخول مسموح بعد إغلاق شمعة M15 + درجة ≥80 + R:R ≥1:2" if ok else f"لا إشارة الآن ({st['reason']})"))
    return (f"  Kenan v2 ({st['session']}): " + ("entry allowed after a closed M15 confirmation + score >= 80 + R:R >= 1:2" if ok
                                               else f"no signal now ({st['reason']})"))


def build(now_ts=None, max_age=1800, lang=None, digits=2):
    lang = (lang or os.environ.get("ALERT_LANG") or "en").lower()
    ar = lang == "ar"
    rows = []
    for market, rec in sorted(LATEST.items()):
        if now_ts and now_ts - rec["ts"] > max_age:
            continue
        for o in rec["obs"]:
            if not o.get("zone"):
                continue
            g, score, missing = grade(o, rec["h1"])
            rows.append((market, o, g, score, missing, rec["last"]))
    order = {"STRONG": 0, "MEDIUM": 1, "WEAK": 2}
    rows.sort(key=lambda r: (order[r[2]], {"in": 0, "near": 1, "far": 2}.get(r[1].get("rel"), 3)))
    head = "📍 مناطق الدخول القريبة (معلومات فقط، ليست إشارة)" if ar else "📍 Entry zones near price (information only, not a signal)"
    if not rows:
        return head + ("\nلا توجد مناطق جاهزة الآن." if ar else "\nNo ready zones right now.")
    lines = [head]
    seen_v2 = set()
    for market, o, g, score, missing, last in rows:
        z, f = o["zone"], f"{{:.{digits}f}}"
        side = SIDE_AR[o["side"]] if ar else o["side"].upper()
        rel = (REL_AR if ar else REL_EN).get(o.get("rel"), "")
        lastp = f" | {f.format(last)}" if last is not None else ""
        if ar:
            lines.append(f"- {market} {side}: {z['kind']} {f.format(z['low'])} - {f.format(z['high'])}{lastp}\n"
                         f"  الجودة: {GRADE_AR[g]} ({score}/5) | {rel}\n"
                         f"  ينقص: {', '.join(missing) if missing else 'لا شيء'}")
        else:
            lines.append(f"- {market} {side}: {z['kind']} {f.format(z['low'])} - {f.format(z['high'])}{lastp}\n"
                         f"  Quality: {g} ({score}/5) | {rel}\n"
                         f"  Missing: {', '.join(missing) if missing else 'nothing'}")
    for market in sorted({r[0] for r in rows}):
        v = v2_line(market, now_ts, ar)
        if v:
            lines.append(f"{market}:\n{v}")
    lines.append("الجودة = عدد الشروط المتحققة، وليست نسبة نجاح. لا دخول قبل شمعة التأكيد." if ar else
                 "Quality = number of conditions met, not a win probability. No entry before the confirmation candle.")
    return "\n".join(lines)


def has_zones(text):
    return "No ready zones" not in text and "لا توجد مناطق جاهزة" not in text


def notify(text):
    """Phone notification only when there is something to show (an empty report never makes a sound). It is silent and updates
    one notification (--alert-once); ZONES_SOUND=1 allows the sound."""
    import shutil
    import subprocess
    if not shutil.which("termux-notification") or not has_zones(text):
        return
    try:
        import alert_classes
        path = alert_classes.save_full_text("ZONES", text)
        ar = (os.environ.get("ALERT_LANG") or "en").lower() == "ar"
        cmd = ["termux-notification", "--title", "📍 مناطق الدخول" if ar else "📍 Entry zones", "--content", text,
               "--priority", "low", "--id", "kenan-zones"]
        if os.environ.get("ZONES_SOUND", "0") != "1":
            cmd += ["--alert-once"]
        if path:
            cmd += ["--action", f"termux-open {path}"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
