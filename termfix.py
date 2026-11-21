"""Termux's screen draws Arabic left-to-right and unjoined (the letters come out reversed). Phone notifications are fine.
For the SCREEN ONLY, text with Arabic letters is shaped (letters joined) and reordered visually when the two small libraries
`arabic-reshaper` and `python-bidi` are installed:  pip install arabic-reshaper python-bidi
Without them the text is printed unchanged. ALERT_TERMINAL_BIDI=0 turns the shaping off. Notifications never use this."""
import os
import re

_AR = re.compile("[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")


def has_arabic(text):
    return bool(_AR.search(text))


def _libs():
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return arabic_reshaper, get_display
    except ImportError:
        return None


def shape(text):
    """Visual-order Arabic for a terminal that cannot do it itself; the text unchanged when it has no Arabic, the libraries
    are missing or ALERT_TERMINAL_BIDI=0. Every line is shaped on its own, so the layout stays."""
    if not isinstance(text, str) or not has_arabic(text) or os.environ.get("ALERT_TERMINAL_BIDI", "1") == "0":
        return text
    libs = _libs()
    if not libs:
        return text
    reshaper, get_display = libs
    try:
        return "\n".join(get_display(reshaper.reshape(line)) if has_arabic(line) else line for line in text.split("\n"))
    except Exception:                                   # a display problem must never stop the bot
        return text
