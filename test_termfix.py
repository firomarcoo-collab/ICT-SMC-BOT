import termfix


def test_text_without_arabic_or_without_the_libraries_is_unchanged(monkeypatch):
    assert termfix.shape("plain text") == "plain text"
    monkeypatch.setattr(termfix, "_libs", lambda: None)
    assert termfix.shape("شراء XAUUSD") == "شراء XAUUSD"


def test_arabic_lines_are_shaped_one_by_one_and_the_switch_turns_it_off(monkeypatch):
    class R:
        @staticmethod
        def reshape(t):
            return "R(" + t + ")"
    monkeypatch.setattr(termfix, "_libs", lambda: (R, lambda t: t[::-1]))
    out = termfix.shape("line one\nشراء\nlast")
    assert out.split("\n")[0] == "line one" and out.split("\n")[2] == "last" and out.split("\n")[1] == ")ءارش(R"
    monkeypatch.setenv("ALERT_TERMINAL_BIDI", "0")
    assert termfix.shape("شراء") == "شراء"


def test_a_failing_library_never_breaks_the_print(monkeypatch):
    class R:
        @staticmethod
        def reshape(t):
            raise RuntimeError("boom")
    monkeypatch.setattr(termfix, "_libs", lambda: (R, lambda t: t))
    assert termfix.shape("شراء") == "شراء"


def test_ict_alerts_print_goes_through_the_shaper(monkeypatch, capsys):
    import ict_alerts as a
    monkeypatch.setattr(termfix, "shape", lambda t: "SHAPED" if isinstance(t, str) and "شراء" in t else t)
    a.print("شراء", flush=True)
    assert "SHAPED" in capsys.readouterr().out
