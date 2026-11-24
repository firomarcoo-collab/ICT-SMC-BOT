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


def build(rows, by=("profile", "symbol"), lang=None):
    lang = (lang or os.environ.get("ALERT_LANG") or "en").lower()
    ar = lang == "ar"
    if not rows:
        return "لا توجد صفقات مغلقة في السجل بعد." if ar else "No closed trades in the journal yet."
    out = [("📊 مراجعة الأداء (معلومات فقط)" if ar else "📊 Performance review (information only)"), journal.line("ALL", rows)]
    for key in by:
        out.append(f"-- {key}")
        for val in sorted({(r.get(key) or "unknown") for r in rows}):
            sub = [r for r in rows if (r.get(key) or "unknown") == val]
            m = journal.metrics(sub)
            out.append(f"  {val}: {m['trades']} trades | {m['total_r']:+.1f}R | avg {m['avg_r']:+.2f}R | win {m['win_rate']:.0f}% -> {verdict(sub, lang)}")
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
    keys = tuple(a for a in sys.argv[1:] if a in ("symbol", "session", "profile", "mode", "side")) or ("profile", "symbol")
    print(build(journal.load(journal.path_for(journal.DEFAULT_CSV)), keys))
    extra = session_section(session_tracker.load_results(), (os.environ.get("ALERT_LANG") or "en").lower())
    if extra:
        print(extra)
