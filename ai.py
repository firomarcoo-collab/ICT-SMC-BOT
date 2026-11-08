"""Optional AI comment on a signal, using the free Gemini API (Google AI Studio key).
Off unless GEMINI_API_KEY is set. Standard library only."""
import json
import urllib.error
import urllib.request

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.5-flash"
CANDLES_IN_PROMPT = 40

SYSTEM = (
    "You are a careful trading analyst. You get an automatic signal and recent candles. "
    "Reply in Arabic, at most 6 short lines: (1) do you agree, disagree or are you neutral, with the "
    "main reason; (2) the main risk; (3) what would invalidate the idea. Use only the data given: "
    "do not invent news or prices. This is a study aid, not investment advice."
)


def candles_context(r, digits=2, n=CANDLES_IN_PROMPT):
    """Compact text of the last closed candles, oldest first (r is newest first, r[0] forming)."""
    rows = list(reversed(r[1:1 + n]))
    return "\n".join(
        f"{c['time'].strftime('%m-%d %H:%M')} O{c['open']:.{digits}f} H{c['high']:.{digits}f} "
        f"L{c['low']:.{digits}f} C{c['close']:.{digits}f}" for c in rows)


def build_prompt(alert_text, context):
    return f"Signal:\n{alert_text}\n\nLast candles (oldest first, UTC):\n{context}"


def ask(prompt, key, model=DEFAULT_MODEL, timeout=30):
    """Returns the model's text. Raises RuntimeError with a readable message on failure."""
    body = json.dumps({
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
    }).encode()
    req = urllib.request.Request(
        URL.format(model=model), data=body, method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"Gemini HTTP {e.code}: {detail}")
    except Exception as e:
        raise RuntimeError(f"Gemini request failed: {e}")
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        raise RuntimeError(f"Gemini returned no text: {str(data)[:200]}")
