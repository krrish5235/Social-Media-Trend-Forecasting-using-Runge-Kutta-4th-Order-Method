// Pure data-to-geometry helpers. No DOM, no WebGL: testable with `node --test`.

export const SURFACE = { width: 10, height: 4, depth: 3.2, slices: 11 };

export function compact(n) {
  if (n == null || !Number.isFinite(n)) return '–';
  const abs = Math.abs(n);
  const units = [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
  for (const [div, suffix] of units) {
    if (abs >= div) {
      const v = n / div;
      return `${v >= 100 ? v.toFixed(0) : v.toFixed(1).replace(/\.0$/, '')}${suffix}`;
    }
  }
  return String(Math.round(n));
}

/** "Nice" tick values from 0 up to (and possibly slightly beyond) max. */
export function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0];
  const rough = max / count;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= rough) || 10 * pow;
  const ticks = [];
  for (let v = 0; v <= max * 1.0001; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}

/** Ticks (in days) for the square-root time axis used by every chart. */
export function dayTicks(tMax) {
  return [0, 1, 3, 7, 14, 30, 60, 90, 180, 365].filter((d) => d <= tMax + 1e-9);
}

/** Position of an age along the time axis, 0..1. Square-root spacing gives the first days room. */
export const timeFraction = (age, tMax) => Math.sqrt(Math.max(age, 0) / tMax);

const lerpLog = (a, b, u) => Math.exp(Math.log(Math.max(a, 1)) * (1 - u) + Math.log(Math.max(b, 1)) * u);

/**
 * Build the "growth surface": time on X, views on Y, scenario on Z.
 * Z = -1 is the cautious end of the range (P10), 0 the median, +1 the optimistic end (P90).
 */
export function buildSurface(grid, opts = {}) {
  const { width, height, depth, slices } = { ...SURFACE, ...opts };
  const t = grid.age_days;
  const n = t.length;
  const tMax = t[n - 1] || 1;
  const vMax = Math.max(...grid.p90, 1);
  const x = (age) => (timeFraction(age, tMax) - 0.5) * width;
  const y = (views) => (views / vMax) * height;
  const valueAt = (i, q) =>
    q <= 0 ? lerpLog(grid.p10[i], grid.p50[i], q + 1) : lerpLog(grid.p50[i], grid.p90[i], q);

  const positions = new Float32Array(slices * n * 3);
  const qs = new Float32Array(slices * n); // scenario coordinate per vertex, for shading
  const cols = new Float32Array(slices * n); // time index per vertex
  let p = 0;
  for (let j = 0; j < slices; j += 1) {
    const q = -1 + (2 * j) / (slices - 1);
    for (let i = 0; i < n; i += 1) {
      const v = valueAt(i, q);
      positions[p++] = x(t[i]);
      positions[p++] = y(v);
      positions[p++] = (q * depth) / 2;
      qs[j * n + i] = q;
      cols[j * n + i] = i;
    }
  }

  const indices = [];
  for (let j = 0; j < slices - 1; j += 1) {
    for (let i = 0; i < n - 1; i += 1) {
      const a = j * n + i;
      const b = a + 1;
      const c = a + n;
      const d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }

  // Grid lines drawn over the surface: along time for every slice, across scenarios every 4th column.
  const lines = [];
  const vertex = (j, i) => [positions[(j * n + i) * 3], positions[(j * n + i) * 3 + 1], positions[(j * n + i) * 3 + 2]];
  for (let j = 0; j < slices; j += 1) {
    for (let i = 0; i < n - 1; i += 1) lines.push(...vertex(j, i), ...vertex(j, i + 1));
  }
  for (let i = 0; i < n; i += 4) {
    for (let j = 0; j < slices - 1; j += 1) lines.push(...vertex(j, i), ...vertex(j + 1, i));
  }

  const mid = (slices - 1) / 2; // slice index of the median (odd slice counts only)
  const medianJ = Math.round(mid);
  const median = [];
  for (let i = 0; i < n; i += 1) median.push(vertex(medianJ, i));

  return {
    positions,
    indices: new Uint32Array(indices),
    linePositions: new Float32Array(lines),
    qs,
    cols,
    median,
    nowIndex: grid.now_index,
    n,
    slices,
    nowX: x(t[grid.now_index]),
    dims: { width, height, depth, tMax, vMax },
    toWorld: (age, views) => [x(age), y(views), 0],
  };
}

/** Hue (0 red .. 140 green) for a video's views relative to its channel's typical Short. */
export function performanceHue(ratio) {
  if (ratio == null || !Number.isFinite(ratio) || ratio <= 0) return 215;
  const l = Math.max(-2, Math.min(2, Math.log2(ratio)));
  return 70 + 35 * l;
}

/** Lay out the channel's Shorts as a grid of towers, newest at the front. */
export function buildSkyline(peers, target, opts = {}) {
  const spacing = opts.spacing ?? 0.95;
  const maxHeight = opts.maxHeight ?? 4.2;
  const items = peers.map((p) => ({ ...p, isTarget: false }));
  items.push({
    id: target.id,
    url: target.url,
    title: target.title,
    age_days: target.age_days,
    views: target.views,
    likes: target.likes,
    comments: target.comments,
    performance_index: target.performance_index,
    isTarget: true,
  });
  items.sort((a, b) => a.age_days - b.age_days);
  const maxViews = Math.max(...items.map((i) => i.views), 1);
  const cols = Math.max(1, Math.ceil(Math.sqrt(items.length)));
  const rows = Math.ceil(items.length / cols);
  const towers = items.map((it, k) => {
    const col = k % cols;
    const row = Math.floor(k / cols);
    return {
      ...it,
      x: (col - (cols - 1) / 2) * spacing,
      z: (row - (rows - 1) / 2) * spacing,
      height: 0.12 + maxHeight * Math.sqrt(it.views / maxViews),
      hue: it.isTarget ? 28 : performanceHue(it.performance_index),
    };
  });
  return { towers, cols, rows, spacing, extentX: cols * spacing, extentZ: rows * spacing };
}

/** A believable placeholder surface shown before the first analysis. Clearly labelled "Example" in the UI. */
export function exampleGrid() {
  const age = [];
  const now = 2;
  const total = 32;
  for (let i = 0; i <= 64; i += 1) age.push((i / 64) * total);
  const F = (a, tau, beta) => 1 - Math.exp(-((Math.max(a, 0) / tau) ** beta));
  // Three plausible curves that all pass through today's views; per-time min/median/max
  // mimic the percentiles of a real forecast (so the bands are always ordered).
  const curves = [[0.9, 0.5], [1.4, 0.6], [2.6, 0.7]].map(([tau, beta]) =>
    age.map((a) => 42000 * (F(a, tau, beta) / F(now, tau, beta))),
  );
  const at = (i) => [...curves.map((c) => c[i])].sort((x, y) => x - y);
  return {
    age_days: age,
    p10: age.map((_, i) => at(i)[0]),
    p50: age.map((_, i) => at(i)[1]),
    p90: age.map((_, i) => at(i)[2]),
    now_index: age.findIndex((a) => a >= now),
  };
}
