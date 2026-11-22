"""The six audible alert classes of the setup tracker, with one persistent state per market and setup.

  ZONE_APPROACH    price is close to a valid FVG / OB made by a sweep + MSS
  ZONE_ENTRY       price entered that zone
  REVERSAL_STAGE3  sweep + MSS / CHoCH with displacement confirmed
  RETEST           price left the zone and came back to it
  CONFIRMED        every mandatory condition passed (the real BUY / SELL signal, sent by the normal signal path)
  INVALIDATED      the setup failed (a close beyond the sweep extreme or through the zone)

Only CONFIRMED is a trade signal. The other five are heads-up messages and say so in their text.
Each (market, setup, class) fires once; the state is saved to alerts_state.json, so a restart repeats nothing.
Sounds do not need audio files: every class has its own vibration pattern and a spoken label (Termux:API
text-to-speech, ALERT_VOICE=0 turns the voice off); CONFIRMED also keeps the normal alert tone.
All phone calls are started without waiting for them (the scan never blocks on a notification).
"""
import decisions
import json
import os
import shutil
import subprocess
import time

APPROACH_ATR = 0.5            # "approaching" = outside the zone by at most this many M15 ATR
KEEP_SECONDS = 24 * 3600      # a setup is forgotten after a day
STATE_FILE = os.environ.get("ALERTS_STATE_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts_state.json")

CLASSES = {
    "ZONE_APPROACH": {"emoji": "🟠", "title": "ZONE APPROACH", "vibrate": "150", "voice": "Zone approach"},
    "ZONE_ENTRY": {"emoji": "🟡", "title": "ZONE ENTRY", "vibrate": "150,100,150", "voice": "Price in zone"},
    "REVERSAL_STAGE3": {"emoji": "🔵", "title": "REVERSAL STAGE 3", "vibrate": "300,100,300", "voice": "Reversal confirmed, stage three"},
    "RETEST": {"emoji": "🟣", "title": "RETEST", "vibrate": "100,100,100,100,100", "voice": "Retest of the zone"},
    "CONFIRMED": {"emoji": "🟢", "title": "CONFIRMED", "vibrate": "600,150,600", "voice": "Confirmed signal"},
    "INVALIDATED": {"emoji": "⚫", "title": "SETUP INVALIDATED", "vibrate": "800", "voice": "Setup invalidated"},
}


EXTRA_CLASSES = {      # IFVG / state-flip alerts (flip_ifvg.py): analysis only, never an entry order
    "STATE_FLIP": {"emoji": "🔄", "title": "STATE FLIP", "vibrate": "200,80,200,80,200", "voice": "State flip, zone failed"},
    "IFVG_RETEST": {"emoji": "🟤", "title": "IFVG RETEST", "vibrate": "120,60,120", "voice": "I F V G retest"},
    "IFVG_SIGNAL": {"emoji": "🟢", "title": "IFVG SIGNAL", "vibrate": "700,120,700,120,700", "voice": "I F V G signal"},
    "IFVG_CONFIRMED": {"emoji": "🟩", "title": "IFVG CONFIRMED", "vibrate": "500,100,500,100,500", "voice": "I F V G confirmation"},
    "ASIA_SETUP": {"emoji": "🌏", "title": "ASIA BREAKOUT SETUP", "vibrate": "120,60,120", "voice": "Asia breakout setup"},
    "ASIA_CONFIRMED": {"emoji": "🟩", "title": "ASIA BREAKOUT CONFIRMED", "vibrate": "500,100,500,100,500", "voice": "Asia breakout confirmation"},
    "ASIA_SIGNAL": {"emoji": "🟢", "title": "ASIA BREAKOUT SIGNAL", "vibrate": "700,120,700,120,700", "voice": "Asia breakout signal"},
}


AR = {   # class -> (Arabic title, spoken label, what it means and what to do)
    "ZONE_APPROACH": ("اقتراب من منطقة", "السعر يقترب من منطقة", "السعر يقترب من منطقة قد تعطي إعداداً. هذا تنبيه مراقبة فقط وليس إشارة دخول. لا تفعل شيئاً الآن."),
    "ZONE_ENTRY": ("دخول المنطقة", "السعر داخل المنطقة", "السعر دخل المنطقة المراقبة. الإشارة تنتظر إغلاق شمعة تأكيد. لا تدخل قبل التأكيد."),
    "REVERSAL_STAGE3": ("انعكاس: المرحلة 3", "تأكد الانعكاس المرحلة الثالثة", "حدث سحب سيولة ثم كسر هيكل بزخم. ننتظر عودة السعر لمنطقة طازجة ثم شمعة تأكيد. ليست إشارة دخول بعد."),
    "RETEST": ("إعادة اختبار", "إعادة اختبار المنطقة", "السعر عاد لإعادة اختبار المنطقة. ننتظر شمعة تأكيد مغلقة قبل أي قرار."),
    "CONFIRMED": ("تأكيد", "إشارة مؤكدة", "اكتملت شروط الإشارة. راجع الدخول والوقف والأهداف، ثم قرر بنفسك: دخول أو انتظار أو تجاهل."),
    "INVALIDATED": ("إلغاء الإعداد", "أُلغي الإعداد", "فشل الإعداد وانتهت صلاحيته. تجاهله وانتظر إعداداً جديداً."),
    "STATE_FLIP": ("انقلاب الحالة", "انقلاب الحالة، فشلت المنطقة",
                   "المنطقة القديمة فشلت وانقلبت إلى IFVG في الاتجاه المعاكس. هذا تنبيه مراقبة وليس إشارة دخول. انتظر عودة السعر للمنطقة وإغلاق شمعة M5 تأكيد."),
    "IFVG_RETEST": ("إعادة اختبار IFVG", "إعادة اختبار آي إف في جي", "عاد السعر إلى منطقة IFVG. ننتظر إغلاق شمعة M5 تأكيد داخلها. لا تدخل قبلها."),
    "IFVG_CONFIRMED": ("تأكيد IFVG (دون إشارة)", "تأكيد آي إف في جي",
                       "أُغلقت شمعة تأكيد داخل IFVG لكن الإعداد لم يجتز شروط الإصدار (التقييم أو التكلفة أو النسبة). عامله كتحليل فقط."),
    "IFVG_SIGNAL": ("إشارة IFVG", "إشارة آي إف في جي",
                    "إشارة مكتملة. فيها دخول مثالي ووقف هيكلي وخروج مبكر وأهداف. أنت تنفّذ بنفسك في MT5. خاطر بـ 1% أو أقل من حسابك. لا نسبة نجاح مضمونة."),
    "ASIA_SETUP": ("إعداد اختراق آسيا", "إعداد اختراق آسيا", "اخترق السعر النطاق الآسيوي. ننتظر إعادة اختبار الحافة ثم شمعة M15 تأكيد. لا تلاحق الاختراق."),
    "ASIA_CONFIRMED": ("تأكيد اختراق آسيا (دون إشارة)", "تأكيد اختراق آسيا", "اكتمل التأكيد لكن الإعداد لم يجتز شروط الإصدار. عامله كتحليل فقط."),
    "ASIA_SIGNAL": ("إشارة اختراق آسيا", "إشارة اختراق آسيا",
                    "إشارة مكتملة على الذهب: دخول ووقف خلف إعادة الاختبار وخروج مبكر وأهداف. أنت تنفّذ بنفسك. خاطر بـ 1% أو أقل. لا نسبة نجاح مضمونة."),
}


def arabic():
    """Alerts are English unless ALERT_LANG=ar."""
    return (os.environ.get("ALERT_LANG") or "en").lower() == "ar"


def explanation(cls):
    return AR[cls][2] if cls in AR else ""


def save_full_text(cls, text, sid=None):
    """Writes the whole alert plus its explanation to a file, so tapping the notification opens the complete Arabic text.
    Keeps the 40 newest files. Returns the path, or '' when it cannot be written."""
    d = os.environ.get("ALERT_TEXT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_texts")
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{int(time.time())}-{cls}.txt")
        body = text + ("\n\n💡 شرح: " + explanation(cls) if cls in AR and arabic() else "")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        files = sorted(x for x in os.listdir(d) if x.endswith(".txt"))
        for old in files[:-40]:
            os.remove(os.path.join(d, old))
        return path
    except OSError:
        return ""


DECISION_CLASSES = ("STATE_FLIP", "IFVG_RETEST", "IFVG_CONFIRMED", "IFVG_SIGNAL", "ASIA_SETUP", "ASIA_CONFIRMED", "ASIA_SIGNAL")     # alerts that carry ENTER / WAIT / IGNORE buttons


def setup_id(market, side, sweep):
    """Stable id of one setup: market, direction and the swept liquidity (type, level, time)."""
    return f"{market}-{side}-{sweep['type']}-{sweep['level']}-{int(sweep['time'].timestamp())}"


class AlertState:
    """Persistent record of every setup being tracked and every alert already fired."""

    def __init__(self, path=None):
        self.path = path or STATE_FILE
        self.setups, self.fired, self.meta = {}, {}, {}
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                raw = json.load(f)
            self.setups, self.fired = dict(raw.get("setups", {})), dict(raw.get("fired", {}))
            self.meta = dict(raw.get("meta", {}))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError):      # a damaged file must never stop the bot
            self.setups, self.fired, self.meta = {}, {}, {}

    def save(self):
        try:
            with open(self.path + ".tmp", "w") as f:
                json.dump({"setups": self.setups, "fired": self.fired, "meta": self.meta}, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass

    def expire(self, now):
        """Forgets setups (and their alerts) older than KEEP_SECONDS."""
        old = [sid for sid, r in self.setups.items() if now - r.get("created", now) > KEEP_SECONDS]
        for sid in old:
            self.setups.pop(sid, None)
        self.fired = {k: v for k, v in self.fired.items() if k.rsplit("|", 1)[0] in self.setups}

    def fire(self, sid, cls, now):
        """True the first time (and records it); False when this class already fired for this setup."""
        key = f"{sid}|{cls}"
        if key in self.fired:
            return False
        self.fired[key] = int(now)
        return True

    def mark_confirmed(self, market, side, now):
        """A real signal was issued: the open watch setups of that market and side are finished (no INVALIDATED later)."""
        for sid, r in self.setups.items():
            if r["market"] == market and r["side"] == side and not r.get("done"):
                r["done"] = "confirmed"
                self.fired.setdefault(f"{sid}|CONFIRMED", int(now))
        self.save()


def invalidation_reason(rec, last_close):
    """Why a tracked setup is dead, or '' while it lives: a close beyond the sweep extreme, or through the far side of the zone."""
    buy = rec["side"] == "buy"
    if (buy and last_close < rec["extreme"]) or (not buy and last_close > rec["extreme"]):
        return f"price closed {'below' if buy else 'above'} the sweep extreme {rec['extreme']:.5g}"
    z = rec.get("zone")
    if z and ((buy and last_close < z["low"]) or (not buy and last_close > z["high"])):
        return f"price closed through the {z['kind']} {z['low']:.5g}-{z['high']:.5g}"
    return ""


def _text(cls, obs, lang, extra=""):
    up = obs["side"] == "buy"
    side_en, side_ar = ("BUY", "شراء") if up else ("SELL", "بيع")
    z = obs.get("zone") or {}
    zone = f"{z.get('kind', '')} {z.get('low', 0):.5g}-{z.get('high', 0):.5g}"
    if lang == "ar":
        body = {
            "ZONE_APPROACH": f"السعر يقترب من منطقة {zone} قد تعطي إعداد {side_ar}. لا شيء لتفعله الآن.",
            "ZONE_ENTRY": f"السعر دخل المنطقة {zone} (إعداد {side_ar}). ننتظر إغلاق شمعة التأكيد.",
            "REVERSAL_STAGE3": f"المرحلة 3: كسر الهيكل MSS/CHoCH مع إزاحة بعد سحب السيولة عند {obs['level']}. ننتظر عودة السعر إلى FVG/OB طازج.",
            "RETEST": f"السعر عاد لإعادة اختبار المنطقة {zone} (إعداد {side_ar}). ننتظر شمعة التأكيد المغلقة.",
            "INVALIDATED": f"أُلغي إعداد {side_ar}: {extra}. تجاهله.",
        }[cls]
        return f"{CLASSES[cls]['emoji']} {AR[cls][0]} ({obs['market']}) - ليست إشارة دخول: {body}"
    body = {
        "ZONE_APPROACH": f"price is approaching a {zone} that could give a {side_en} setup. Nothing to do yet.",
        "ZONE_ENTRY": f"price entered the {zone} ({side_en} setup). Waiting for the closed confirmation candle.",
        "REVERSAL_STAGE3": f"stage 3: MSS / CHoCH with displacement confirmed after the sweep of {obs['level']}. "
                           "Waiting for price to return to a fresh FVG / OB.",
        "RETEST": f"price came back to retest the {zone} ({side_en} setup). Waiting for the closed confirmation candle.",
        "INVALIDATED": f"the {side_en} setup is cancelled: {extra}. Forget it.",
    }[cls]
    return f"{CLASSES[cls]['emoji']} {CLASSES[cls]['title']} ({obs['market']}), NOT an entry signal: {body}"


def process(state, market, observations, last_close, now, lang="en"):
    """Turns what the market looks like now into the alerts that must fire. Returns [(class, setup id, text)].
    observations: from selective.watch_observations (one per live sweep + MSS setup). last_close: last closed M5 close."""
    state.expire(now)
    out = []
    for obs in observations:
        sid = obs["sid"]
        rec = state.setups.setdefault(sid, {"market": market, "side": obs["side"], "extreme": obs["extreme"],
                                            "zone": None, "phase": None, "created": int(now), "done": ""})
        if rec["done"]:
            continue
        if obs.get("zone"):
            rec["zone"] = obs["zone"]
        rec["stage"] = 3 if obs["stage3"] else 2
        rec["developing"] = bool(obs.get("developing"))
        if obs["stage3"] and state.fire(sid, "REVERSAL_STAGE3", now):
            out.append(("REVERSAL_STAGE3", sid, _text("REVERSAL_STAGE3", obs, lang)))
        rel = obs.get("rel")
        if rel == "near" and rec["phase"] is None and state.fire(sid, "ZONE_APPROACH", now):
            rec["phase"] = "near"
            out.append(("ZONE_APPROACH", sid, _text("ZONE_APPROACH", obs, lang)))
        elif rel == "in":
            if rec["phase"] in (None, "near") and state.fire(sid, "ZONE_ENTRY", now):
                out.append(("ZONE_ENTRY", sid, obs.get("explain") or _text("ZONE_ENTRY", obs, lang)))
            elif rec["phase"] == "out" and state.fire(sid, "RETEST", now):
                out.append(("RETEST", sid, _text("RETEST", obs, lang)))
            rec["phase"] = "in"
        elif rel in ("near", "far") and rec["phase"] == "in":
            rec["phase"] = "out"
    for sid, rec in list(state.setups.items()):                   # the invalidation check covers every tracked setup of the market
        if rec["market"] != market or rec["done"] or last_close is None or rec.get("kind") == "IFVG":
            continue                                        # IFVG setups are judged by flip_ifvg (M15 body closes), not here
        why = invalidation_reason(rec, last_close)
        if why:
            rec["done"] = "invalidated"
            if rec.get("suppressed"):                       # a suppressed setup ends silently
                continue
            if state.fire(sid, "INVALIDATED", now):
                out.append(("INVALIDATED", sid, _text("INVALIDATED", {"side": rec["side"], "market": market, "level": "", "zone": rec.get("zone")}, lang, why)))
    state.save()
    return out


def deliver(cls, text, sid=None):
    """Phone side of an alert: notification, vibration pattern and spoken label, all started without waiting.
    Returns the list of commands started (empty outside Termux)."""
    spec, started = CLASSES.get(cls) or EXTRA_CLASSES[cls], []
    ar = arabic() and cls in AR

    def run(cmd):
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                started.append(cmd[0])
            except OSError:
                pass
    title = AR[cls][0] if ar else spec["title"]
    shown = (text + "\n\n💡 " + explanation(cls)) if ar else text
    tap = []
    path = save_full_text(cls, text, sid)
    if path:
        tap = ["--action", f"termux-open {path}"]       # tapping the notification opens the complete text with its explanation
    run(["termux-notification", "--title", f"{spec['emoji']} {title}", "--content", shown,
         "--priority", "high", "--vibrate", spec["vibrate"], "--id", f"kenan-{cls}"] + tap
        + (decisions.button_args(sid) if sid and cls in DECISION_CLASSES else []))
    if os.environ.get("ALERT_VOICE", "1") != "0":
        run(["termux-tts-speak", "-l", "ar", AR[cls][1]] if ar else ["termux-tts-speak", spec["voice"]])
    return started
