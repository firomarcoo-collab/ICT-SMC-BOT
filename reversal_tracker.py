"""The six named states of a reversal setup. Display and journal only: a state is never a trade signal except
ENTRY CONFIRMED, which is set by the normal signal path (alert_classes.AlertState.mark_confirmed)."""

STATES = ("REVERSAL DETECTED", "REVERSAL DEVELOPING", "MSS CONFIRMED", "RETEST PENDING", "ENTRY CONFIRMED", "INVALIDATED")
AR = {"REVERSAL DETECTED": "انعكاس مرصود", "REVERSAL DEVELOPING": "انعكاس قيد التكوّن", "MSS CONFIRMED": "كسر الهيكل مؤكد",
      "RETEST PENDING": "بانتظار إعادة الاختبار", "ENTRY CONFIRMED": "دخول مؤكد", "INVALIDATED": "أُلغي"}


def state_of(rec):
    """rec: a setup record of AlertState (done, stage, developing, zone)."""
    if rec.get("done") == "invalidated":
        return "INVALIDATED"
    if rec.get("done") == "confirmed":
        return "ENTRY CONFIRMED"
    if rec.get("stage", 3) < 3:
        return "REVERSAL DEVELOPING" if rec.get("developing") else "REVERSAL DETECTED"
    return "RETEST PENDING" if rec.get("zone") else "MSS CONFIRMED"


def update(state, market, now, lang="en"):
    """Refreshes every setup's state; returns [(sid, old, new, text)] for the ones that changed (print-only lines)."""
    out = []
    for sid, rec in state.setups.items():
        if rec.get("market") != market:
            continue
        new = state_of(rec)
        old = rec.get("state")
        if new != old:
            rec["state"] = new
            if old is not None or new != "INVALIDATED":
                name = AR[new] if lang == "ar" else new
                out.append((sid, old, new, f"STATE {market} {rec['side'].upper()}: {name} (setup only, not an entry signal)"
                            if new != "ENTRY CONFIRMED" else f"STATE {market} {rec['side'].upper()}: {name}"))
    return out
