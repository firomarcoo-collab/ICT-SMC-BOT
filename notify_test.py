"""Sends one TEST of every alert channel so you can check notification, tone, voice and Telegram. Not a signal.
Run: python notify_test.py"""
import os
import shutil

import alert_classes
import ict_alerts
import telegram_alerts


def main():
    print("Tools:", {t: bool(shutil.which(t)) for t in ("termux-notification", "termux-media-player", "termux-tts-speak")})
    snd = ict_alerts.sound_path()
    print("Entry tone file:", snd or "none (the phone's own notification sound is used)")
    print("Telegram configured:", telegram_alerts.enabled())
    ict_alerts.notify("TEST ONLY - entry tone check. This is NOT a signal.", entry=True, side="buy")
    print("Sent: entry notification (tone + high priority).")
    started = alert_classes.deliver("IFVG_SIGNAL", "TEST ONLY - signal-class alert (sound + vibration + voice). NOT a signal.", "test")
    print("Sent: signal-class alert ->", started or "nothing started (not on Termux)")
    print("Zone reports stay silent on purpose (ZONES_SOUND=1 turns their sound on).")


if __name__ == "__main__":
    main()
