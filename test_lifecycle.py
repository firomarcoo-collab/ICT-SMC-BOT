import lifecycle as lc
import unified_engine as ue
from test_unified_engine import NOW, vote_all, main


def test_valid_path_and_terminal_is_final():
    life = {}
    assert lc.move(life, "a", "CONFIRMED", NOW)
    assert lc.move(life, "a", "WARNING", NOW + 1)
    assert lc.move(life, "a", "LOSS", NOW + 2)
    assert not lc.move(life, "a", "LOSS", NOW + 3)            # no second loss
    assert not lc.move(life, "a", "TP_HIT", NOW + 4)          # no result after the result
    assert lc.state(life, "a") == "LOSS"


def test_wait_cannot_jump_to_a_result():
    assert not lc.move({}, "b", "TP_HIT", NOW)


def test_outcome_names():
    assert lc.from_outcome("win") == "TP_HIT" and lc.from_outcome("loss") == "LOSS" and lc.from_outcome("x") is None


def _confirm():
    vote_all(); main()
    return ue.run_market("XAUUSD", NOW)


def test_confirmation_sets_state_and_result_is_recorded_once():
    a = _confirm()
    assert a and lc.state(ue._load()["life"], a["id"]) == "CONFIRMED"
    assert ue.record_result(a["main_id"], "loss", NOW + 60) == "LOSS"
    assert ue.record_result(a["main_id"], "loss", NOW + 120) is None      # duplicate: silent
    assert ue.record_result(a["main_id"], "win", NOW + 180) is None
    assert ue._load()["active"][a["id"]]["status"] == "LOSS"


def test_unknown_ref_is_ignored():
    assert ue.record_result("nope", "win", NOW) is None


def test_cancel_and_expire_move_the_state():
    a = _confirm()
    out = ue.check_active("XAUUSD", {"close": 1000.0}, NOW + 60)
    assert out and out[0][0] == "CANCELLED"
    assert lc.state(ue._load()["life"], a["id"]) == "INVALIDATED"


def test_h1_flip_gives_one_warning():
    a = _confirm()
    d = ue._load()
    for k in d["votes"]["XAUUSD"]:
        d["votes"]["XAUUSD"][k]["h1"] = "down"
    ue._save(d)
    first = ue.check_active("XAUUSD", None, NOW + 60)
    assert [s for s, _ in first] == ["WARNING"] and lc.state(ue._load()["life"], a["id"]) == "WARNING"
    assert ue.check_active("XAUUSD", None, NOW + 120) == []             # only once
