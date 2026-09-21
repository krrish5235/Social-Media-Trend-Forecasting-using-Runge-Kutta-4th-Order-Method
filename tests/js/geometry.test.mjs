import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSurface, buildSkyline, niceTicks, compact, performanceHue, exampleGrid, dayTicks, timeFraction, SURFACE } from '../../app/static/js/geometry.js';

test('compact formats large numbers', () => {
  assert.equal(compact(999), '999');
  assert.equal(compact(1500), '1.5K');
  assert.equal(compact(1_000_000), '1M');
  assert.equal(compact(12_345_678), '12.3M');
  assert.equal(compact(120_000_000), '120M');
  assert.equal(compact(null), '–');
});

test('niceTicks start at zero and are increasing', () => {
  const t = niceTicks(184_000, 4);
  assert.equal(t[0], 0);
  for (let i = 1; i < t.length; i += 1) assert.ok(t[i] > t[i - 1]);
  assert.ok(t[t.length - 1] <= 184_000 * 1.0001);
  assert.deepEqual(niceTicks(0), [0]);
});

test('surface geometry is finite, indexed correctly and centred', () => {
  const g = exampleGrid();
  const s = buildSurface(g);
  assert.equal(s.positions.length, s.slices * s.n * 3);
  assert.ok(Array.from(s.positions).every(Number.isFinite));
  const maxIndex = Math.max(...s.indices);
  assert.equal(maxIndex, s.slices * s.n - 1);
  assert.equal(s.indices.length, (s.slices - 1) * (s.n - 1) * 6);
  // median slice sits on z = 0, extremes at +/- depth/2
  assert.ok(s.median.every((p) => Math.abs(p[2]) < 1e-6));
  const zs = Array.from({ length: s.slices * s.n }, (_, k) => s.positions[k * 3 + 2]);
  assert.ok(Math.abs(Math.max(...zs) - SURFACE.depth / 2) < 1e-4);
  // x spans the configured width, y never exceeds the configured height
  const xs = Array.from({ length: s.slices * s.n }, (_, k) => s.positions[k * 3]);
  assert.ok(Math.abs(Math.max(...xs) - SURFACE.width / 2) < 1e-4);
  const ys = Array.from({ length: s.slices * s.n }, (_, k) => s.positions[k * 3 + 1]);
  assert.ok(Math.max(...ys) <= SURFACE.height + 1e-4);
});

test('surface: optimistic scenario is never below cautious scenario', () => {
  const s = buildSurface(exampleGrid());
  for (let i = 0; i < s.n; i += 1) {
    const yLow = s.positions[(0 * s.n + i) * 3 + 1];
    const yMid = s.positions[(5 * s.n + i) * 3 + 1];
    const yHigh = s.positions[((s.slices - 1) * s.n + i) * 3 + 1];
    assert.ok(yLow <= yMid + 1e-6 && yMid <= yHigh + 1e-6);
  }
});

test('surface: today marker matches nowIndex and toWorld is consistent', () => {
  const g = exampleGrid();
  const s = buildSurface(g);
  const [x] = s.toWorld(g.age_days[g.now_index], g.p50[g.now_index]);
  assert.ok(Math.abs(x - s.nowX) < 1e-6);
});

test('skyline places every video once and marks the target', () => {
  const peers = Array.from({ length: 12 }, (_, i) => ({
    id: `p${i}`, title: `t${i}`, age_days: i + 1, views: 1000 * (i + 1), performance_index: 0.5 + i / 10,
  }));
  const target = { id: 'me', title: 'me', age_days: 2.5, views: 50_000, performance_index: 2 };
  const sky = buildSkyline(peers, target);
  assert.equal(sky.towers.length, 13);
  assert.equal(sky.towers.filter((t) => t.isTarget).length, 1);
  const keys = new Set(sky.towers.map((t) => `${t.x.toFixed(3)}|${t.z.toFixed(3)}`));
  assert.equal(keys.size, 13); // no two towers share a cell
  const tallest = Math.max(...sky.towers.map((t) => t.height));
  assert.equal(sky.towers.find((t) => t.height === tallest).id, 'me');
});

test('performanceHue maps slow to red and fast to green', () => {
  assert.ok(performanceHue(0.25) < performanceHue(1));
  assert.ok(performanceHue(4) > performanceHue(1));
  assert.equal(performanceHue(null), 215);
  assert.ok(performanceHue(1000) <= 140 && performanceHue(1e-9) >= 0);
});

test('time axis is monotonic, spans 0..1 and gives early days more room than a linear axis', () => {
  assert.equal(timeFraction(0, 30), 0);
  assert.equal(timeFraction(30, 30), 1);
  assert.ok(timeFraction(3, 30) > 3 / 30);
  assert.deepEqual(dayTicks(32.6), [0, 1, 3, 7, 14, 30]);
  assert.deepEqual(dayTicks(0.5), [0]);
});
