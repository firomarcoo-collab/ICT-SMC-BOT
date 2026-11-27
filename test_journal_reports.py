import journal as jn


def row(t, sym, r, mfe=1.0, sess="London", score="85", kind="selective"):
    return {"time_utc": t, "symbol": sym, "R": str(r), "mfe_r": str(mfe), "mae_r": "0.5", "session": sess, "score": score, "kind": kind, "profile": ""}


ROWS = [row("2026-11-02 10:00", "XAUUSD", 2), row("2026-11-03 10:00", "XAUUSD", -1), row("2026-11-10 10:00", "US100", 1.5, score="92"),
        row("2026-12-01 10:00", "US100", -1, score="70")]


def test_expectancy_is_win_rate_times_average_win_minus_loss():
    m = jn.metrics(ROWS[:2])
    assert abs(m["expectancy_r"] - 0.5) < 1e-9 and m["win_rate"] == 50


def test_period_reports():
    assert len(jn.period_report(ROWS, "week").splitlines()) == 3
    assert len(jn.period_report(ROWS, "month").splitlines()) == 2
    assert jn.period_report([], "day").startswith("No finished")


def test_compare_has_market_strategy_session_and_score_band():
    txt = jn.compare(ROWS)
    for k in ("by market", "by strategy", "by session", "by score band", "90+", "below 80"):
        assert k in txt


def test_recommendation_needs_30_trades_and_is_never_applied():
    assert "No recommendation" in jn.recommendations(ROWS)[0]
    bad = [row("2026-11-%02d 10:00" % (1 + i % 28), "GBPUSD", -1 if i % 3 else 1) for i in range(40)]
    rec = jn.recommendations(bad)[0]
    assert "RECOMMENDATION (not applied)" in rec and "GBPUSD" in rec


def test_old_journal_header_is_upgraded(tmp_path):
    p = tmp_path / "journal.csv"
    p.write_text(",".join(jn.COLUMNS[:-1]) + "\n2026-11-01 10:00,XAUUSD,5m,k,buy,win,1.0,1,0.2,1,0,2,t,London,,\n")
    jn.append(str(p), row("2026-11-02 10:00", "XAUUSD", 1))
    assert len(jn.load(str(p))) == 2 and "score" in jn.load(str(p))[0]
