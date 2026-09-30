"""Core timeline engine: validation, reference resolution, exact evaluation.

All arithmetic is exact (``fractions.Fraction``) so that values at fractional
play times are never distorted by floating point and the browser (BigInt
fractions, see ``static/engine.js``) agrees with the server bit-for-bit.

Public surface
--------------
EngineError          raised with ``code`` + ``errors`` when validation fails
compile_project(inp) -> dict            validate and compute actual timeline
evaluate(compiled, t) -> dict           all lanes at (possibly fractional) time
round_half_up(fr)   -> int              banker-free rounding for non-negatives
parse_time(value)   -> Fraction|None    accept int / float / fraction dict
"""

from __future__ import annotations

from fractions import Fraction
from math import floor
from typing import Any

ZERO = Fraction(0)
MAX_VALUE = 255


class EngineError(Exception):
    """A collection of validation errors keyed by cue index (or '_project')."""

    def __init__(self, errors: list[dict] | dict[str, Any]):
        if isinstance(errors, dict):
            errors = errors["errors"]
        self.errors = list(errors)
        super().__init__(self.errors)


# ---------------------------------------------------------------------------
# Normalisation / field validation
# ---------------------------------------------------------------------------

def _as_nonneg_int(value: Any, *, field: str, idx: int,
                   errors: list[dict]) -> int | None:
    """Accept genuine non-negative integers only (no floats, no booleans)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append({"index": idx, "field": field,
                       "code": "not_nonneg_integer",
                       "message": "必须是非负整数"})
        return None
    return value


def normalize_cue(raw: Any, idx: int, errors: list[dict],
                  seen_ids: set[str]) -> dict | None:
    """Normalize one cue; record every problem found and return None on error."""
    if not isinstance(raw, dict):
        errors.append({"index": idx, "field": None, "code": "bad_object",
                       "message": "提示项必须是对象"})
        return None

    cid = raw.get("id")
    if not isinstance(cid, str) or not cid.strip():
        errors.append({"index": idx, "field": "id", "code": "missing_id",
                       "message": "缺少 id"})
        return None
    cid = cid.strip()
    if len(cid) > 64:
        errors.append({"index": idx, "field": "id", "code": "id_too_long",
                       "message": "id 长度不能超过 64"})
        return None
    if cid in seen_ids:
        errors.append({"index": idx, "field": "id", "code": "duplicate_id",
                       "message": f"id 重复: {cid}"})
        return None
    seen_ids.add(cid)

    fixture = raw.get("fixture")
    if not isinstance(fixture, str) or not fixture.strip():
        errors.append({"index": idx, "field": "fixture",
                       "code": "missing_fixture",
                       "message": "必须填写灯具名称"})
    else:
        fixture = fixture.strip()

    channel = raw.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        errors.append({"index": idx, "field": "channel",
                       "code": "missing_channel",
                       "message": "必须填写通道名称"})
    else:
        channel = channel.strip()

    target = _as_nonneg_int(raw.get("target"), field="target", idx=idx,
                            errors=errors)
    if target is not None and target > MAX_VALUE:
        errors.append({"index": idx, "field": "target",
                       "code": "target_out_of_range",
                       "message": "目标值必须在 0~255 之间"})
        target = None

    duration = _as_nonneg_int(raw.get("duration"), field="duration", idx=idx,
                              errors=errors)

    start = raw.get("start")
    norm_start = None
    if not isinstance(start, dict):
        errors.append({"index": idx, "field": "start",
                       "code": "bad_start",
                       "message": "起点必须为绝对时间或引用形式"})
    else:
        mode = start.get("mode")
        if mode == "absolute":
            at = _as_nonneg_int(start.get("at"), field="start.at", idx=idx,
                                errors=errors)
            if at is not None:
                norm_start = {"mode": "absolute", "at": at}
        elif mode == "reference":
            ref_id = start.get("refId")
            if not isinstance(ref_id, str) or not ref_id.strip():
                errors.append({"index": idx, "field": "start.refId",
                               "code": "missing_ref",
                               "message": "引用必须指向另一项"})
                ref_id = None
            else:
                ref_id = ref_id.strip()
                if ref_id == cid:
                    errors.append({"index": idx, "field": "start.refId",
                                   "code": "reference_cycle",
                                   "message": "不能引用自身"})
                    ref_id = None
            off = _as_nonneg_int(start.get("offset"), field="start.offset",
                                 idx=idx, errors=errors)
            if ref_id is not None and off is not None:
                norm_start = {"mode": "reference", "refId": ref_id,
                              "anchor": start.get("anchor", "end")
                              if start.get("anchor") in ("start", "end")
                              else "end",
                              "offset": off}
        else:
            errors.append({"index": idx, "field": "start.mode",
                           "code": "bad_start_mode",
                           "message": "起点模式只能是 absolute 或 reference"})

    if errors and any(e["index"] == idx for e in errors):
        return None
    return {
        "id": cid,
        "fixture": fixture,
        "channel": channel,
        "target": target,
        "duration": duration,
        "start": norm_start,
    }


def normalize_input(raw: Any) -> list[dict]:
    """Validate field-level constraints; raise EngineError with all errors."""
    if not isinstance(raw, dict) or not isinstance(raw.get("cues"), list):
        raise EngineError([{"index": -1, "field": "cues",
                            "code": "bad_payload",
                            "message": "请求体必须包含 cues 数组"}])
    errors: list[dict] = []
    cues: list[dict] = []
    seen: set[str] = set()
    for i, raw_cue in enumerate(raw["cues"]):
        cue = normalize_cue(raw_cue, i, errors, seen)
        if cue is not None:
            cues.append(cue)

    # A reference to an id that does not exist (deleted cue) is illegal.
    live_ids = {c["id"] for c in cues}
    for cue in cues:
        st = cue["start"]
        if st["mode"] == "reference" and st["refId"] not in live_ids:
            errors.append({"index": raw_index(raw, cue["id"]),
                           "field": "start.refId",
                           "code": "missing_reference",
                           "message": f"引用的项不存在: {st['refId']}"})
    if errors:
        raise EngineError(errors)
    return cues


def raw_index(raw: dict, cue_id: str) -> int:
    for i, c in enumerate(raw["cues"]):
        if isinstance(c, dict) and c.get("id") == cue_id:
            return i
    return -1


# ---------------------------------------------------------------------------
# Reference graph: cycle detection + actual-time resolution
# ---------------------------------------------------------------------------

def _detect_cycles(cues: list[dict]) -> list[str]:
    """Return ids that participate in a reference cycle (Kahn's algorithm)."""
    ids = {c["id"] for c in cues}
    indeg = {c["id"]: 0 for c in cues}
    dependents: dict[str, list[str]] = {c["id"]: [] for c in cues}
    for c in cues:
        st = c["start"]
        if st["mode"] == "reference" and st["refId"] in ids:
            indeg[c["id"]] += 1
            dependents[st["refId"]].append(c["id"])
    queue = [cid for cid, d in indeg.items() if d == 0]
    while queue:
        cid = queue.pop()
        for dep in dependents[cid]:
            indeg[dep] -= 1
            if indeg[dep] == 0:
                queue.append(dep)
    return sorted(cid for cid, d in indeg.items() if d > 0)


def _resolve_times(cues: list[dict]) -> dict[str, int]:
    """Compute every actual start (always an int); memoised DFS."""
    by_id = {c["id"]: c for c in cues}
    actual: dict[str, int] = {}

    for root in by_id:
        if root in actual:
            continue
        stack = [root]
        while stack:
            cid = stack[-1]
            st = by_id[cid]["start"]
            if cid in actual:
                stack.pop()
                continue
            if st["mode"] == "absolute":
                actual[cid] = st["at"]
                stack.pop()
                continue
            ref, anchor, offset = st["refId"], st["anchor"], st["offset"]
            if ref not in actual:
                stack.append(ref)
                continue
            base = actual[ref]
            if anchor == "end":
                base += by_id[ref]["duration"]
            actual[cid] = base + offset
            stack.pop()
    return actual


def _ancestors(cues: list[dict], by_id: dict[str, dict],
               actual: dict[str, int]) -> None:
    """Attach transitive reference parent info to every cue in place."""
    for cue in cues:
        st = cue["start"]
        if st["mode"] == "reference":
            parent_id = st["refId"]
            parent = by_id[parent_id]
            chain = list(parent.get("refAncestors", ()))
            if parent_id not in chain:
                chain = chain + [parent_id]
            cue["refParent"] = parent_id
            cue["refAnchor"] = st["anchor"]
            cue["refOffset"] = st["offset"]
            cue["anchorActual"] = (actual[parent_id]
                                   + (parent["duration"]
                                      if st["anchor"] == "end" else 0))
            cue["refAncestors"] = chain
        else:
            cue["refParent"] = None
            cue["refAnchor"] = None
            cue["refOffset"] = 0
            cue["anchorActual"] = st["at"]
            cue["refAncestors"] = []


# ---------------------------------------------------------------------------
# Compilation: validation + per-channel exact gradient chaining
# ---------------------------------------------------------------------------

def _fraction_pair(frac: Fraction) -> dict:
    return {"n": frac.numerator, "d": frac.denominator}


def compile_project(raw: Any) -> dict:
    """Validate the whole project and return the computed timeline.

    Raises EngineError on *any* problem; callers must not persist on error.
    """
    cues = normalize_input(raw)

    cyclic = _detect_cycles(cues)
    if cyclic:
        cyc = set(cyclic)
        raise EngineError([
            {"index": next((i for i, c in enumerate(raw["cues"])
                            if isinstance(c, dict) and c.get("id") == cid), -1),
             "field": "start", "code": "reference_cycle",
             "message": f"引用环: {' -> '.join(cyclic)}"}
            for cid in cyclic
        ])

    by_id = {c["id"]: c for c in cues}
    actual = _resolve_times(cues)
    for cid, t in actual.items():
        if t < 0:  # defensive; offsets are non-negative so this cannot happen
            raise EngineError([{"index": -1, "field": "start",
                                "code": "negative_time",
                                "message": "实际起点为负"}])
    _ancestors(cues, by_id, actual)

    errors: list[dict] = []
    seen_starts: set[tuple[str, str, int]] = set()
    for i, cue in enumerate(cues):
        cue["actualStart"] = actual[cue["id"]]
        key = (cue["fixture"], cue["channel"], cue["actualStart"])
        if key in seen_starts:
            orig_i = next(
                j for j, c in enumerate(cues)
                if (c["fixture"], c["channel"], c["actualStart"]) == key)
            errors.append({"index": i, "field": "start",
                           "code": "same_channel_same_start",
                           "message": (f"与第 {orig_i + 1} 项同属 "
                                       f"{cue['fixture']}/{cue['channel']} "
                                       f"且同时刻起步")})
        seen_starts.add(key)
    if errors:
        raise EngineError(errors)

    # Per (fixture, channel): sort by actual start and chain gradients from
    # the unrounded value of the preceding gradient at the new start.
    lanes_map: dict[tuple[str, str], list[dict]] = {}
    for cue in cues:
        lanes_map.setdefault((cue["fixture"], cue["channel"]), []).append(cue)

    lanes: list[dict] = []
    for (fixture, channel) in sorted(lanes_map):
        entries = sorted(lanes_map[(fixture, channel)],
                         key=lambda c: c["actualStart"])
        prev = None
        for cue in entries:
            s = cue["actualStart"]
            cue["interrupted"] = False
            if prev is None:
                from_value = ZERO
            else:
                # value left by the previous (possibly interrupted) gradient
                p_from, p_to, p_s, p_d = (prev["_from"], prev["target"],
                                          prev["actualStart"], prev["duration"])
                if p_d == 0:
                    from_value = Fraction(p_to)
                elif s >= p_s + p_d:
                    from_value = Fraction(p_to)
                else:
                    # positive-length previous gradient cut short at s
                    prev["interrupted"] = True
                    from_value = p_from + (Fraction(p_to) - p_from) * \
                        Fraction(s - p_s, p_d)
            cue["_from"] = from_value
            cue["fromValue"] = _fraction_pair(from_value)
            prev = cue
        lanes.append({
            "fixture": fixture,
            "channel": channel,
            "key": f"{fixture}|{channel}",
            "cues": [_public_cue(c) for c in entries],
        })

    depends: dict[str, list[str]] = {c["id"]: [] for c in cues}
    for cue in cues:
        if cue["refParent"]:
            depends[cue["refParent"]].append(cue["id"])

    return {
        "lanes": lanes,
        "cues": [_public_cue(c) for c in cues],
        "depends": depends,
    }


def _public_cue(c: dict) -> dict:
    st = c["start"]
    if st["mode"] == "absolute":
        start = {"mode": "absolute", "at": st["at"]}
    else:
        start = {"mode": "reference", "refId": st["refId"],
                 "anchor": st["anchor"], "offset": st["offset"]}
    return {
        "id": c["id"],
        "fixture": c["fixture"],
        "channel": c["channel"],
        "target": c["target"],
        "duration": c["duration"],
        "start": start,
        "actualStart": c["actualStart"],
        "fromValue": c["fromValue"],
        "interrupted": c["interrupted"],
        "refParent": c["refParent"],
        "refAnchor": c["refAnchor"],
        "refOffset": c["refOffset"],
        "anchorActual": c["anchorActual"],
        "refAncestors": c["refAncestors"],
    }


# ---------------------------------------------------------------------------
# Evaluation at any non-negative time
# ---------------------------------------------------------------------------

def round_half_up(fr: Fraction) -> int:
    """Round to nearest integer, halves away from zero (inputs are >= 0)."""
    return floor(fr + Fraction(1, 2))


def parse_time(value: Any) -> Fraction | None:
    """Accept int, finite float (exact binary value), {n,d} ints or numeric
    strings (the browser serialises BigInt as strings).

    Floats are converted via ``Fraction(float)`` so that JavaScript
    ``Number`` -> exact binary fraction round-trips identically.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return Fraction(value)
    if isinstance(value, float) and value >= 0.0 and value != float("inf"):
        return Fraction(value)
    if isinstance(value, str) and value.isdigit():  # non-negative decimal int
        return Fraction(int(value))
    if isinstance(value, dict):
        n, d = value.get("n"), value.get("d")
        if isinstance(n, str) and n.lstrip("-").isdigit():
            n = int(n)
        if isinstance(d, str) and d.lstrip("-").isdigit():
            d = int(d)
        if (isinstance(n, int) and not isinstance(n, bool)
                and isinstance(d, int) and not isinstance(d, bool)
                and n >= 0 and d > 0):
            return Fraction(n, d)
    return None


def evaluate_lane(lane: dict, t: Fraction) -> dict:
    """Value of one lane at time t using the latest-started cue."""
    cues = lane["cues"]
    chosen = None
    for cue in cues:  # lanes are few; ordered scan, binary search unnecessary
        if cue["actualStart"] <= t:
            chosen = cue
        else:
            break
    if chosen is None:
        raw = ZERO
        active = None
    else:
        active = chosen["id"]
        s = chosen["actualStart"]
        d = chosen["duration"]
        if d == 0 or t >= s + d:
            raw = Fraction(chosen["target"])
        else:
            f = chosen["fromValue"]
            start_fr = Fraction(f["n"], f["d"])
            raw = start_fr + (Fraction(chosen["target"]) - start_fr) \
                * Fraction(t - s, d)
    return {"raw": _fraction_pair(raw), "value": round_half_up(raw),
            "activeCueId": active}


def evaluate(compiled: dict, t: Fraction) -> dict:
    lanes = {f"{l['fixture']}|{l['channel']}":
             evaluate_lane(l, t) for l in compiled["lanes"]}
    return {"time": _fraction_pair(t), "lanes": lanes}
