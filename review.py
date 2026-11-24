"""Performance review from the trades journal (information only). Run: python review.py [symbol|profile|session|side]
Honest rules: under 30 closed trades per group is 'too early'; always R, never the win rate alone; no promises."""
import os
import sys

import journal

MIN_TRADES = 30


def verdict(rows, lang="en"):
    m = journal.metrics(rows)
    ar = lang == "ar"
    if m["trades"] < MIN_TRADES:
        return (f"مبكر جداً ({m['trades']}/{MIN_TRADES})" if ar else f"too early ({m['trades']}/{MIN_TRADES})")
    if m["total_r"] > 0 and m["profit_factor"] > 1.2:
        return "نتيجة موجبة حتى الآن (لا ضمان للمستقبل)" if ar else "positive so far (no guarantee for the future)"
    if m["total_r"] < 0:
        return "خاسرة: راجع الفلاتر أو أوقفها" if ar else "losing: review the filters or pause it"
    return "قريبة من التعادل" if ar else "near break-even"


def extended(rows):
    """Extra numbers from the journal only (nothing invented). TP1/TP2 hit = the trade reached +1R / +2R at any time (MFE);
    SL rate = closed at a loss; expectancy = average R per trade; losing streak = most consecutive losing trades."""
    n = len(rows)
    if not n:
        return {}
    mfe = [float(r.get("mfe_r") or 0) for r in rows]
    rs = [float(r["R"]) for r in rows]
    streak = best = 0
    for x in rs:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    return {"tp1_rate": 100 * sum(1 for x in mfe if x >= 1.0) / n, "tp2_rate": 100 * sum(1 for x in mfe if x >= 2.0) / n,
            "sl_rate": 100 * sum(1 for x in rs if x < 0) / n, "expectancy": sum(rs) / n, "max_losing_streak": best}


def score_band(r):
    try:
        v = float(r.get("score") or r.get("score100") or "")
    except ValueError:
        return "unknown"
    return "90-100" if v >= 90 else "80-89" if v >= 80 else "<80"


def ext_line(rows):
    e = extended(rows)
    return (f"TP1 {e['tp1_rate']:.0f}% | TP2 {e['tp2_rate']:.0f}% | SL {e['sl_rate']:.0f}% | expectancy {e['expectancy']:+.2f}R | "
            f"max losing streak {e['max_losing_streak']}") if e else ""


def build(rows, by=("profile", "symbol"), lang=None):
    lang = (lang or os.environ.get("ALERT_LANG") or "en").lower()
    ar = lang == "ar"
    if not rows:
        return "لا توجد صفقات مغلقة في السجل بعد." if ar else "No closed trades in the journal yet."
    out = [("📊 مراجعة الأداء (معلومات فقط)" if ar else "📊 Performance review (information only)"), journal.line("ALL", rows)]
    out.append("  " + ext_line(rows))
    for key in by:
        out.append(f"-- {key}")
        get = score_band if key == "score" else (lambda r, k=key: r.get(k) or "unknown")
        for val in sorted({get(r) for r in rows}):
            sub = [r for r in rows if get(r) == val]
            m = journal.metrics(sub)
            out.append(f"  {val}: {m['trades']} trades | {m['total_r']:+.1f}R | avg {m['avg_r']:+.2f}R | win {m['win_rate']:.0f}% -> {verdict(sub, lang)}")
            out.append("    " + ext_line(sub))
    out.append("الحكم يحتاج 30 صفقة على الأقل لكل مجموعة. الصفقات المترابطة (EURUSD/GBPUSD, US100/US30) مخاطرة واحدة. لا ضمان للأرباح."
               if ar else "A verdict needs 30+ trades per group. Correlated trades (EURUSD/GBPUSD, US100/US30) are one risk. No profit is guaranteed.")
    out.append("Displacement alerts are alert-only: they are not in this journal." if not ar else
               "تنبيهات الاندفاع للتنبيه فقط ولا تدخل هذا السجل.")
    return "\n".join(out)


def session_section(rows, lang="en"):
    """The alert-only detectors, measured on closed M5 bars by session_tracker.py (stop first when both hit in one bar)."""
    if not rows:
        return ""
    head = "🧪 تنبيهات الجلسة (قياس تقريبي من شموع M5)" if lang == "ar" else "🧪 Session detectors (approximate, measured on closed M5 bars)"
    out = [head]
    for val in sorted({r["name"] for r in rows}):
        sub = [r for r in rows if r["name"] == val]
        m = journal.metrics(sub)
        out.append(f"  {val}: {m['trades']} trades | {m['total_r']:+.1f}R | avg {m['avg_r']:+.2f}R | win {m['win_rate']:.0f}% -> {verdict(sub, lang)}")
    return "\n".join(out)


if __name__ == "__main__":
    import session_tracker
    keys = tuple(a for a in sys.argv[1:] if a in ("symbol", "session", "profile", "mode", "side", "score")) or ("profile", "symbol")
    print(build(journal.load(journal.path_for(journal.DEFAULT_CSV)), keys))
    extra = session_section(session_tracker.load_results(), (os.environ.get("ALERT_LANG") or "en").lower())
    if extra:
        print(extra)
