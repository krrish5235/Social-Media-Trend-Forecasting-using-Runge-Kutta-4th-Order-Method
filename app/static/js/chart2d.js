// Plain SVG forecast chart. Built with DOM APIs only (never innerHTML) so titles from YouTube stay inert text.
import { compact, dayTicks, niceTicks, timeFraction } from './geometry.js';

const NS = 'http://www.w3.org/2000/svg';
const W = 860;
const H = 380;
const M = { l: 56, r: 20, t: 18, b: 40 };

function s(tag, attrs = {}, text) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  if (text != null) el.textContent = text;
  return el;
}

export function renderChart(container, report) {
  container.replaceChildren();
  const g = report.forecast.grid;
  const t = g.age_days;
  const tMax = t[t.length - 1] || 1;
  const vMax = Math.max(...g.p90, 1);
  const x = (age) => M.l + timeFraction(age, tMax) * (W - M.l - M.r);
  const y = (views) => H - M.b - (views / vMax) * (H - M.t - M.b);
  const now = g.now_index;

  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', class: 'chart2d' });
  const h7 = report.forecast.horizons.find((h) => h.days === 7);
  svg.append(
    s('title', {}, `Views forecast. Now ${compact(report.forecast.current_views)}; in 7 days about ${compact(h7.p50)}, likely between ${compact(h7.p10)} and ${compact(h7.p90)}.`),
  );

  for (const v of niceTicks(vMax, 4)) {
    svg.append(s('line', { x1: M.l, x2: W - M.r, y1: y(v), y2: y(v), class: 'c-grid' }));
    svg.append(s('text', { x: M.l - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'c-text' }, compact(v)));
  }
  for (const d of dayTicks(tMax)) {
    svg.append(s('text', { x: x(d), y: H - M.b + 20, 'text-anchor': 'middle', class: 'c-text' }, `${d}d`));
  }
  svg.append(s('text', { x: (M.l + W - M.r) / 2, y: H - 4, 'text-anchor': 'middle', class: 'c-text c-title' }, 'Days since upload'));

  // Likely range (P10 to P90) as a band, drawn in the forecast portion only.
  const idx = t.map((_, i) => i).filter((i) => i >= now);
  const top = idx.map((i) => `${x(t[i])},${y(g.p90[i])}`);
  const bottom = idx.map((i) => `${x(t[i])},${y(g.p10[i])}`).reverse();
  svg.append(s('polygon', { points: [...top, ...bottom].join(' '), class: 'c-band' }));

  const line = (from, to, cls) => {
    const pts = t.slice(from, to + 1).map((a, k) => `${x(a)},${y(g.p50[from + k])}`);
    return s('polyline', { points: pts.join(' '), class: cls, fill: 'none' });
  };
  svg.append(line(0, now, 'c-past'), line(now, t.length - 1, 'c-future'));

  svg.append(s('line', { x1: x(t[now]), x2: x(t[now]), y1: M.t, y2: H - M.b, class: 'c-now' }));
  svg.append(s('text', { x: x(t[now]) + 6, y: M.t + 12, class: 'c-text c-strong' }, 'Today'));

  for (const p of report.history) svg.append(s('circle', { cx: x(p.age_days), cy: y(p.views), r: 3, class: 'c-obs' }));
  svg.append(s('circle', { cx: x(t[now]), cy: y(report.forecast.current_views), r: 5, class: 'c-current' }));
  container.append(svg);
}
