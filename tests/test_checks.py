"""Deterministic gate checks: board geometry, timeline sync, script rules."""
from app import board
from app.agents import MAX_WORDS_BETWEEN_CUES, Narrator, parse_json


def text(x, y, s, size=20):
    return {"t": "text", "x": x, "y": y, "text": s, "size": size, "color": "ink"}


def test_valid_board_has_no_problems():
    assert board.board_problems([[text(40, 60, "query")], [text(40, 120, "key"), {"t": "arrow", "x1": 40, "y1": 70, "x2": 40, "y2": 95}]]) == []


def test_board_rejects_out_of_bounds_overlap_empty_and_malformed():
    probs = board.board_problems([
        [text(700, 60, "this label runs off the right edge")],
        [text(40, 200, "same spot"), text(45, 205, "same spot too")],
        [],
        [{"t": "path", "d": "M 0 0 a 5 5 0 0 1 10 10"}, {"t": "blob"}],
    ])
    joined = " | ".join(probs)
    assert "outside" in joined and "overlaps" in joined and "nothing to draw" in joined
    assert "absolute M, L, Q, C, Z" in joined and "unknown element type" in joined


def make_section(gap_words=10, n_cues=3, wps=2.5):
    sentences = [{"text": " ".join(["word"] * gap_words), "cue": f"draw {i}"} for i in range(n_cues)]
    n = gap_words * n_cues
    words = [{"w": "word", "s": i / wps, "e": (i + 1) / wps} for i in range(n)]
    dur = n / wps
    els = [[text(40, 60 + 40 * i, f"cue {i}")] for i in range(n_cues)]
    return {"sentences": sentences, "audio": {"words": words, "duration": dur, "estimated": False},
            "cues": board.build_timeline(sentences, words, dur, els)}


def test_timeline_pins_cues_to_trigger_words_and_passes_sync():
    sec = make_section()
    assert [c["word"] for c in sec["cues"]] == [0, 10, 20]
    assert [c["t"] for c in sec["cues"]] == [0.0, 4.0, 8.0]
    assert all(ok for _, ok, _ in board.sync_checks(sec))


def test_sync_fails_on_long_blank_stretch_drift_and_missing_strokes():
    sec = make_section(gap_words=60)  # 24 s between cues
    assert not dict((n, ok) for n, ok, _ in board.sync_checks(sec))["No more than 20 s of narration with nothing new on the board"]
    sec = make_section()
    sec["cues"][1]["t"] += 1.5
    sec["cues"][2]["elements"] = []
    res = {n: ok for n, ok, _ in board.sync_checks(sec)}
    assert not res["Cues start within 300 ms of their trigger word"]
    assert not res["Every cue mark has strokes and every stroke group has a cue"]


def test_cue_finishes_drawing_before_the_next_one():
    sec = make_section(gap_words=2, n_cues=4)  # cues 0.8 s apart, 1 element each
    for a, b in zip(sec["cues"], sec["cues"][1:]):
        assert a["t"] + a["dur"] <= b["t"]


def test_script_rules():
    paper = "The model uses 8 attention heads and a dimension of 512."
    good = {"sentences": [{"text": "The model uses 512 dimensions.", "cue": "write 512"},
                          {"text": "It has several heads.", "cue": None}]}
    assert Narrator.script_problems(good, 9, paper) == []
    bad = {"sentences": [{"text": "It scored 99.7 on the test.", "cue": None},
                         {"text": " ".join(["word"] * (MAX_WORDS_BETWEEN_CUES + 5)), "cue": "draw"}]}
    joined = " | ".join(Narrator.script_problems(bad, 10, paper))
    assert "first sentence must have a cue" in joined and "99.7" in joined
    assert "nothing new on the board" in joined and "must be between" in joined


def test_parse_json_tolerates_fences_and_rejects_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    try:
        parse_json("sorry, no")
    except ValueError:
        return
    raise AssertionError("expected ValueError")
