// Exact rational timeline engine, mirroring app/engine.py bit-for-bit.
// Fractions are {n: BigInt, d: BigInt} kept reduced with d > 0.
// Numbers (fractional play times) are decoded by their IEEE-754 double
// representation, exactly like Python's Fraction(float).

const ZERO = { n: 0n, d: 1n };

function gcd(a, b) {
  a = a < 0n ? -a : a;
  b = b < 0n ? -b : b;
  while (b) { [a, b] = [b, a % b]; }
  return a || 1n;
}

export function frac(n, d = 1n) {
  n = BigInt(n);
  d = BigInt(d);
  if (d === 0n) throw new Error("zero denominator");
  if (d < 0n) { n = -n; d = -d; }
  if (n === 0n) return { n: 0n, d: 1n };
  const g = gcd(n, d);
  return { n: n / g, d: d / g };
}

export function add(a, b) { return frac(a.n * b.d + b.n * a.d, a.d * b.d); }
export function sub(a, b) { return frac(a.n * b.d - b.n * a.d, a.d * b.d); }
export function mul(a, b) { return frac(a.n * b.n, a.d * b.d); }
export function cmp(a, b) {
  const l = a.n * b.d, r = b.n * a.d;
  return l < r ? -1 : l > r ? 1 : 0;
}
export function neg(a) { return { n: -a.n, d: a.d }; }

// Fraction.times -> double -> Fraction conversion, exact.
export function fromNumber(x) {
  if (x < 0 || !Number.isFinite(x)) return null;
  if (x === 0) return { n: 0n, d: 1n };
  const buf = new ArrayBuffer(8);
  const f64 = new Float64Array(buf);
  const u32 = new Uint32Array(buf);
  f64[0] = x;
  const low = BigInt(u32[0]);
  const high = BigInt(u32[1]);
  const bits = (high << 32n) | low;
  const sign = (bits >> 63n) & 1n;
  const exp = Number((bits >> 52n) & 0x7ffn);
  const mant = bits & ((1n << 52n) - 1n);
  let n, d;
  if (exp === 0) {
    n = mant;
    d = 2n ** 1074n;
  } else {
    n = (1n << 52n) | mant;
    d = 2n ** BigInt(1075 - exp); // 52 + 1023
  }
  if (sign) n = -n;
  return frac(n, d);
}

export function toNumber(a) { return Number(a.n) / Number(a.d); }

// Half-up (away from zero for negatives; all light values are >= 0).
export function roundHalfUp(a) {
  const q = a.n / a.d;
  const r = a.n % a.d;
  let v = q;
  if (a.n >= 0n && 2n * r >= a.d) v += 1n;
  if (a.n < 0n && 2n * (-r) > a.d) v -= 1n;
  return v;
}

// Decimal for display only ("12.375"); exact value lives in n/d.
export function toFixed(a, digits = 3) {
  const scale = 10n ** BigInt(digits);
  const scaled = roundHalfUp(frac(a.n * scale, a.d));
  const negative = scaled < 0n;
  const abs = negative ? -scaled : scaled;
  const whole = abs / scale;
  let fracPart = (abs % scale).toString().padStart(digits, "0").replace(/0+$/, "");
  const body = fracPart ? `${whole}.${fracPart}` : `${whole}`;
  return negative ? `-${body}` : body;
}

// ---------------------------------------------------------------------------
// Evaluation (mirror of engine.evaluate_lane)
// ---------------------------------------------------------------------------

export function evaluateLane(lane, t) {
  let chosen = null;
  for (const cue of lane.cues) {
    if (cmp(t, frac(BigInt(cue.actualStart))) >= 0) chosen = cue;
    else break;
  }
  if (!chosen) return { raw: ZERO, value: 0n, activeCueId: null };

  const s = frac(BigInt(chosen.actualStart));
  const d = BigInt(chosen.duration);
  let raw;
  if (d === 0n || cmp(t, add(s, frac(d))) >= 0) {
    raw = frac(BigInt(chosen.target));
  } else {
    const from = frac(BigInt(chosen.fromValue.n), BigInt(chosen.fromValue.d));
    const target = frac(BigInt(chosen.target));
    const progress = frac(t.n - s.n * t.d, t.d * d);
    raw = add(from, mul(sub(target, from), progress));
  }
  return { raw, value: roundHalfUp(raw), activeCueId: chosen.id };
}

export function evaluate(timeline, t) {
  const lanes = {};
  for (const lane of timeline.lanes) {
    lanes[lane.key] = evaluateLane(lane, t);
  }
  return { time: t, lanes };
}
