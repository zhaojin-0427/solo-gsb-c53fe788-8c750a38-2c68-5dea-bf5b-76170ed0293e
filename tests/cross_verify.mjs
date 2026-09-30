// Cross-check the browser engine against Python-generated exact vectors.
// Run: python3 scripts/gen_vectors.py && node tests/cross_verify.mjs
import { readFileSync } from "node:fs";
import { evaluate, frac, roundHalfUp, toFixed, fromNumber } from "../static/engine.js";

const data = JSON.parse(readFileSync("/tmp/vectors.json", "utf8"));

let failures = 0;
for (const v of data.vectors) {
  const t = frac(BigInt(v.time.n), BigInt(v.time.d));
  const got = evaluate(data.timeline, t);
  for (const [key, want] of Object.entries(v.lanes)) {
    const g = got.lanes[key];
    const ok =
      g.value === BigInt(want.value) &&
      g.raw.n === BigInt(want.raw.n) && g.raw.d === BigInt(want.raw.d) &&
      g.activeCueId === want.activeCueId;
    if (!ok) {
      failures++;
      console.error("MISMATCH", key, v.time,
        "want", want, "got", { raw: g.raw, value: g.value, active: g.activeCueId });
    }
  }
}

// half-up rounding spot checks incl. exact halves
const halfCases = [
  [1, 2, 1n], [125, 10, 13n], [0, 1, 0n], [2545, 10, 255n],
  [1, 1, 1n], [2, 3, 1n], [1, 4, 0n],
];
for (const [n, d, want] of halfCases) {
  if (roundHalfUp(frac(BigInt(n), BigInt(d))) !== want) {
    failures++;
    console.error("ROUND FAIL", n, d, "want", want);
  }
}

// double decoding must equal Python Fraction(double): 0.1 case
const f = fromNumber(0.1);
if (f.n !== 3602879701896397n || f.d !== 36028797018963968n) {
  failures++;
  console.error("FLOAT DECODE FAIL", f);
}
console.log("toFixed 33.333... =", toFixed(frac(1n, 3n), 3),
            " 0.5 =", toFixed(frac(1n, 2n), 3));

if (failures) { console.error(`${failures} mismatches`); process.exit(1); }
console.log("cross-verify OK:", data.vectors.length, "time vectors, all lanes exact");
