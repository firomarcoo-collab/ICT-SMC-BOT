"""Records your decision on an alert.  python decide.py <id> enter|wait|ignore   (the notification buttons run this)."""
import json
import sys

import alert_classes
import decisions


def pending(sid, path=None):
    try:
        with open(path or alert_classes.STATE_FILE) as f:
            return (json.load(f).get("meta", {}).get("pending", {}) or {}).get(sid)
    except (OSError, ValueError):
        return None


def main(argv):
    if len(argv) != 3 or argv[2] not in decisions.CHOICES:
        print("Usage: python decide.py <id> enter|wait|ignore")
        return 2
    sid, choice = argv[1], argv[2]
    plan = pending(sid)
    if plan is None:
        print(f"No pending alert with id {sid} (it may be older than a day).")
        return 1
    decisions.record(sid, choice, plan)
    print(decisions.answer(choice, plan, plan.get("lang", "en")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
