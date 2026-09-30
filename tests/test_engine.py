"""Tests for the pure engine: chaining, interruption, half-up rounding."""

from fractions import Fraction

import pytest

from app import engine


def cue(cid, target=255, duration=1000, start=None, fixture="L1", channel="d"):
    return {"id": cid, "fixture": fixture, "channel": channel,
            "target": target, "duration": duration,
            "start": start or {"mode": "absolute", "at": 0}}


def lane_map(compiled):
    return {l["key"]: l for l in compiled["lanes"]}


def val(compiled, t):
    out = engine.evaluate(compiled, Fraction(t) if isinstance(t, int) else t)
    return out["lanes"]["L1|d"]["value"]


def raw(compiled, t):
    out = engine.evaluate(compiled, Fraction(t) if isinstance(t, int) else t)
    return out["lanes"]["L1|d"]["raw"]


# ---------------------------------------------------------------------------
# basic chaining
# ---------------------------------------------------------------------------

def test_initial_value_zero_before_first_cue():
    c = engine.compile_project({"cues": [cue("a", target=100, duration=1000,
        start={"mode": "absolute", "at": 100})]})
    assert val(c, 0) == 0
    assert val(c, 99) == 0


def test_linear_ramp_from_zero_unrounded():
    c = engine.compile_project({"cues": [cue("a", target=100, duration=1000,
        start={"mode": "absolute", "at": 0})]})
    assert val(c, 0) == 0
    assert raw(c, 250)["n"] == 25 and raw(c, 250)["d"] == 1
    assert val(c, 250) == 25
    assert val(c, 1000) == 100
    assert val(c, 5000) == 100


def test_chain_uses_unrounded_previous_value():
    # ramp 0->100 over 1000, new ramp at 333 to 200 over 667 ms
    c = engine.compile_project({"cues": [
        cue("a", target=100, duration=1000, start={"mode": "absolute", "at": 0}),
        cue("b", target=200, duration=667, start={"mode": "absolute", "at": 333}),
    ]})
    r = raw(c, 333)
    assert r["n"] * 1000 == r["d"] * 33300  # exactly 33.3...
    # b at start must equal the unrounded a value (not 33)
    lane = lane_map(c)["L1|d"]
    assert lane["cues"][1]["fromValue"] == r


def test_completed_previous_ramps_leave_target_value():
    c = engine.compile_project({"cues": [
        cue("a", target=100, duration=100, start={"mode": "absolute", "at": 0}),
        cue("b", target=0, duration=100, start={"mode": "absolute", "at": 500}),
    ]})
    assert lane_map(c)["L1|d"]["cues"][1]["fromValue"]["n"] == 100


def test_zero_duration_instant_switch():
    c = engine.compile_project({"cues": [
        cue("a", target=100, duration=1000, start={"mode": "absolute", "at": 0}),
        cue("b", target=255, duration=0, start={"mode": "absolute", "at": 500}),
    ]})
    assert val(c, 499) == 50
    assert val(c, 500) == 255
    assert val(c, 501) == 255


def test_target_zero_ramp_to_black():
    c = engine.compile_project({"cues": [
        cue("a", target=100, duration=0, start={"mode": "absolute", "at": 0}),
        cue("b", target=0, duration=1000, start={"mode": "absolute", "at": 1000}),
    ]})
    assert val(c, 1500) == 50
    assert val(c, 2000) == 0


# ---------------------------------------------------------------------------
# interruption
# ---------------------------------------------------------------------------

def test_interrupted_gradient_marked_and_ghost_value_used():
    c = engine.compile_project({"cues": [
        cue("a", target=200, duration=4000, start={"mode": "absolute", "at": 1000}),
        cue("b", target=255, duration=0, start={"mode": "absolute", "at": 1500}),
    ]})
    cc = {x["id"]: x for x in c["cues"]}
    assert cc["a"]["interrupted"] is True
    assert cc["b"]["interrupted"] is False
    # at 1500 the first gradient would have reached 25
    assert cc["b"]["fromValue"] == {"n": 25, "d": 1}
    assert val(c, 1250) == 13   # ramp in progress: (250/4000)*200 = 12.5 -> 13 (half up)
    assert val(c, 1500) == 255  # instant switch takes over


def test_multi_channel_interruption_does_not_cross_channels():
    c = engine.compile_project({"cues": [
        cue("a", target=200, duration=4000, start={"mode": "absolute", "at": 0},
            fixture="L1"),
        cue("b", target=100, duration=100, start={"mode": "absolute", "at": 10},
            fixture="L2"),
    ]})
    assert not c["cues"][0]["interrupted"]
    assert not c["cues"][1]["interrupted"]


# ---------------------------------------------------------------------------
# references & actual times
# ---------------------------------------------------------------------------

def test_reference_start_end_and_anchors():
    c = engine.compile_project({"cues": [
        cue("a", target=100, duration=1000, start={"mode": "absolute", "at": 100}),
        cue("b", target=100, duration=500,
            start={"mode": "reference", "refId": "a", "anchor": "end",
                   "offset": 200}),
        cue("c", target=100, duration=0,
            start={"mode": "reference", "refId": "a", "anchor": "start",
                   "offset": 50}),
    ]})
    at = {x["id"]: x["actualStart"] for x in c["cues"]}
    assert at == {"a": 100, "b": 1300, "c": 150}


def test_reference_chain_transitive():
    c = engine.compile_project({"cues": [
        cue("a", target=1, duration=10, start={"mode": "absolute", "at": 5}),
        cue("b", target=1, duration=10,
            start={"mode": "reference", "refId": "a", "anchor": "start",
                   "offset": 100}),
        cue("c", target=1, duration=10,
            start={"mode": "reference", "refId": "b", "anchor": "end",
                   "offset": 7}),
    ]})
    at = {x["id"]: x["actualStart"] for x in c["cues"]}
    assert at["b"] == 105 and at["c"] == 122
    cc = {x["id"]: x for x in c["cues"]}
    assert cc["c"]["refAncestors"] == ["a", "b"]
    assert c["depends"]["a"] == ["b"]


# ---------------------------------------------------------------------------
# rejection rules
# ---------------------------------------------------------------------------

def reject(raw):
    with pytest.raises(engine.EngineError) as ei:
        engine.compile_project(raw)
    return ei.value.errors


def test_missing_reference_rejected():
    errs = reject({"cues": [
        cue("a", start={"mode": "reference", "refId": "ghost",
                        "anchor": "end", "offset": 0})]})
    assert any(e["code"] == "missing_reference" for e in errs)


def test_reference_cycle_rejected():
    errs = reject({"cues": [
        cue("a", start={"mode": "reference", "refId": "b",
                        "anchor": "end", "offset": 0}),
        cue("b", start={"mode": "reference", "refId": "a",
                        "anchor": "start", "offset": 10}),
    ]})
    codes = {e["code"] for e in errs}
    assert "reference_cycle" in codes


def test_self_reference_rejected():
    errs = reject({"cues": [
        cue("a", start={"mode": "reference", "refId": "a",
                        "anchor": "end", "offset": 0})]})
    assert any(e["code"] == "reference_cycle" for e in errs)


def test_same_channel_same_actual_start_rejected_even_through_refs():
    errs = reject({"cues": [
        cue("a", target=10, duration=100,
            start={"mode": "absolute", "at": 100}),
        cue("b", target=20, duration=0,
            start={"mode": "absolute", "at": 200}),
        cue("c", target=30, duration=0, fixture="L1", channel="d",
            start={"mode": "reference", "refId": "a", "anchor": "end",
                   "offset": 0}),  # 100+100 = 200, collides with b
    ]})
    assert any(e["code"] == "same_channel_same_start" for e in errs)


def test_same_start_different_channel_allowed():
    c = engine.compile_project({"cues": [
        cue("a", fixture="L1", start={"mode": "absolute", "at": 100}),
        cue("b", fixture="L2", start={"mode": "absolute", "at": 100}),
    ]})
    assert len(c["lanes"]) == 2


def test_field_validation_collects_multiple_errors():
    errs = reject({"cues": [
        {"id": "a", "fixture": " ", "channel": "d", "target": 300,
         "duration": -1, "start": {"mode": "absolute", "at": -2}},
    ]})
    fields = {e["field"] for e in errs}
    assert {"fixture", "target", "duration", "start.at"} <= fields


def test_duplicate_ids_rejected():
    errs = reject({"cues": [
        cue("a", target=1, duration=0, start={"mode": "absolute", "at": 0}),
        cue("a", target=2, duration=0, start={"mode": "absolute", "at": 1}),
    ]})
    assert any(e["code"] == "duplicate_id" for e in errs)


def test_float_fields_rejected():
    errs = reject({"cues": [
        cue("a", target=10.5, duration=0, start={"mode": "absolute", "at": 0})]})
    assert errs  # pydantic catches it at API; engine also rejects raw floats
    errs2 = reject({"cues": [
        cue("a", target=10, duration=0.5, start={"mode": "absolute", "at": 0})]})
    assert errs2


# ---------------------------------------------------------------------------
# evaluation details
# ---------------------------------------------------------------------------

def test_half_up_rounding_exact_half_goes_up():
    assert engine.round_half_up(Fraction(12, 2)) == 6
    assert engine.round_half_up(Fraction(125, 10)) == 13   # 12.5 -> 13
    assert engine.round_half_up(Fraction(0, 1)) == 0
    assert engine.round_half_up(Fraction(1, 2)) == 1       # 0.5 -> 1


def test_evaluation_at_fractional_time():
    c = engine.compile_project({"cues": [
        cue("a", target=1, duration=3, start={"mode": "absolute", "at": 0})]})
    # t = 1/3 -> value 1/9
    out = engine.evaluate(c, Fraction(1, 3))
    r = out["lanes"]["L1|d"]
    assert r["raw"] == {"n": 1, "d": 9}
    assert r["value"] == 0


def test_parse_time_matches_python_fraction_of_double():
    # JavaScript sends the exact binary fraction of 0.1; that value is NOT
    # the decimal 1/10, but both sides use the same IEEE-754 double.
    assert engine.parse_time(0.1) == Fraction(0.1)
    assert engine.parse_time({"n": 1, "d": 3}) == Fraction(1, 3)
    # browser sends BigInt n/d as decimal strings
    assert engine.parse_time({"n": "10000000000000000000000000000000000000000",
                              "d": "3"}) == Fraction(10 ** 40, 3)
    assert engine.parse_time("1250") == Fraction(1250)
    assert engine.parse_time("-5") is None
    assert engine.parse_time(-1) is None
    assert engine.parse_time("abc") is None


def test_evaluation_endpoints_inclusive():
    c = engine.compile_project({"cues": [
        cue("a", target=100, duration=1000,
            start={"mode": "absolute", "at": 1000})]})
    assert val(c, 1000) == 0
    assert val(c, 2000) == 100


def test_zero_duration_then_hold_and_later_ramp():
    # jump at 1000 to 255, hold; ramp at 3000 continues from 255
    c = engine.compile_project({"cues": [
        cue("a", target=255, duration=0,
            start={"mode": "absolute", "at": 1000}),
        cue("b", target=0, duration=1000,
            start={"mode": "absolute", "at": 3000})]})
    assert val(c, 1000) == 255
    assert val(c, 2999) == 255
    cc = {x["id"]: x for x in c["cues"]}
    assert cc["b"]["fromValue"] == {"n": 255, "d": 1}
    assert val(c, 3500) == 128  # 255 -> 0 half way = 127.5 -> 128
    assert cc["a"]["interrupted"] is False  # zero-duration is never "cut"


def test_reference_to_start_anchor_actual_anchor_value():
    # b starts at a.start + 100 = 100, right inside a's ramp
    c = engine.compile_project({"cues": [
        cue("a", target=200, duration=1000,
            start={"mode": "absolute", "at": 0}),
        cue("b", target=200, duration=0,
            start={"mode": "reference", "refId": "a",
                   "anchor": "start", "offset": 100})]})
    cc = {x["id"]: x for x in c["cues"]}
    assert cc["b"]["anchorActual"] == 0
    assert cc["b"]["actualStart"] == 100
    assert cc["b"]["fromValue"] == {"n": 20, "d": 1}
    assert cc["a"]["interrupted"] is True


def test_reference_end_anchor_with_zero_duration_parent():
    # referencing END of a zero-duration cue with positive offset is fine;
    # offset 0 would be the same instant and is therefore rejected.
    c = engine.compile_project({"cues": [
        cue("a", target=200, duration=0,
            start={"mode": "absolute", "at": 1000}),
        cue("b", target=100, duration=100,
            start={"mode": "reference", "refId": "a",
                   "anchor": "end", "offset": 1})]})
    assert c["cues"][1]["actualStart"] == 1001

    errs = reject({"cues": [
        cue("a", target=200, duration=0,
            start={"mode": "absolute", "at": 1000}),
        cue("b", target=100, duration=100,
            start={"mode": "reference", "refId": "a",
                   "anchor": "end", "offset": 0})]})
    assert any(e["code"] == "same_channel_same_start" for e in errs)


def test_demo_project_compiles_and_has_interruption():
    from app.seed import DEMO_PROJECT
    c = engine.compile_project(DEMO_PROJECT)
    interrupted = {x["id"] for x in c["cues"] if x["interrupted"]}
    assert "a" in interrupted  # L1 4000ms ramp cut at 1500
    assert "d" in interrupted  # L2 ramp cut at 4000
    at = {x["id"]: x["actualStart"] for x in c["cues"]}
    assert at == {"a": 1000, "b": 1500, "c": 3000, "d": 2000,
                  "e": 4000, "f": 7000, "g": 7500}
