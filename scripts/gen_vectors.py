"""Generate exact evaluation vectors consumed by tests/cross_verify.mjs."""
import json
from fractions import Fraction
from pathlib import Path

from app import engine
from app.seed import DEMO_PROJECT

# Some tricky times as rationals (denominator intentionally non-decimal)
times = [Fraction(0), Fraction(999), Fraction(1000), Fraction(1250),
         Fraction(1500), Fraction(2999, 2), Fraction(3000),
         Fraction(10000, 3), Fraction(4000), Fraction(4001),
         Fraction(1, 3), Fraction(7000), Fraction(7500), Fraction(9999, 7)]

compiled = engine.compile_project(DEMO_PROJECT)
vectors = []
for t in times:
    ev = engine.evaluate(compiled, t)
    vectors.append({"time": {"n": t.numerator, "d": t.denominator},
                    "lanes": {k: {"raw": v["raw"], "value": v["value"],
                                  "activeCueId": v["activeCueId"]}
                              for k, v in ev["lanes"].items()}})

Path("/tmp/vectors.json").write_text(
    json.dumps({"timeline": compiled, "vectors": vectors}, ensure_ascii=False))
print("wrote", len(vectors), "vectors")
