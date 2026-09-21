// UI controller. All server-provided text (titles, channel names) is inserted with textContent only.
import { Stage } from './scene.js';
import { renderChart } from './chart2d.js';
import { compact } from './geometry.js';

const $ = (id) => document.getElementById(id);
const nf = new Intl.NumberFormat('en');

function el(tag, { cls, text, attrs } = {}, ...kids) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  n.append(...kids.filter(Boolean));
  return n;
}

const full = (n) => (n == null ? '–' : nf.format(n));
const pct = (x, d = 1) => (x == null ? '–' : `${(x * 100).toFixed(d)}%`);
const times = (x) => (x == null ? '–' : `${x.toFixed(x >= 10 ? 0 : 1)}x`);

function ageText(hours) {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} minutes`;
  if (hours < 48) return `${Math.round(hours)} hours`;
  return `${(hours / 24).toFixed(hours < 240 ? 1 : 0)} days`;
}

function etaText(days) {
  return days < 1.5 ? `${Math.max(1, Math.round(days * 24))} hours` : `${days.toFixed(1)} days`;
}

const LEGEND = {
  surface: (r) =>
    `Cool colours show the path so far, ${r?.meta.past_observed ? 'from recorded view counts' : "as the model reconstructs it from the channel's other Shorts"}; warm colours are the forecast. The surface widens from the low case to the high case, and the glowing dot is today. Time is stretched so the first days are easier to read.`,
  skyline: () =>
    "Each tower is one Short from this channel; taller means more views. Green is ahead of the channel's usual pace for its age, red is behind. The glowing tower is this video. Click a tower to open it.",
  chart: () =>
    'The line is the likely path. The shaded band spans the low case to the high case. Time is stretched so the first days are easier to read.',
};
const HINT_3D = 'Drag to orbit, scroll or pinch to zoom.';

// -- state ---------------------------------------------------------------------
let report = null;
let view = 'surface';
const stageEl = $('stage');
const tooltip = $('tooltip');

let stage;
try {
  stage = new Stage($('gl'), {
    onHover(item, x, y) {
      if (!item) {
        tooltip.hidden = true;
        return;
      }
      tooltip.replaceChildren(
        el('strong', { text: item.title || 'Untitled' }),
        el('span', { text: `${compact(item.views)} views, ${item.age_days < 2 ? `${Math.round(item.age_days * 24)} hours` : `${item.age_days.toFixed(1)} days`} old` }),
        item.performance_index != null ? el('span', { text: `${times(item.performance_index)} the channel's typical pace` }) : null,
      );
      tooltip.hidden = false;
      const box = tooltip.parentElement.getBoundingClientRect();
      tooltip.style.setProperty('--x', `${Math.min(x + 14, box.width - tooltip.offsetWidth - 8)}px`);
      tooltip.style.setProperty('--y', `${Math.min(y + 14, box.height - tooltip.offsetHeight - 8)}px`);
    },
    onSelect(item) {
      if (item.url?.startsWith('https://www.youtube.com/') && document.body.dataset.mode === 'live') {
        window.open(item.url, '_blank', 'noopener,noreferrer');
      }
    },
    onAutoRotate(on) {
      $('btn-rotate').setAttribute('aria-pressed', String(on));
    },
    onFallback() {
      stageEl.classList.add('no-webgl');
      setView('chart');
    },
  });
} catch (err) {
  stage = { supported: false };
}

if (!stage.supported) {
  stageEl.classList.add('no-webgl');
  $('stage-note').textContent = 'The 3D view needs WebGL, which is unavailable in this browser. The 2D chart shows the same forecast.';
  $('tab-surface').disabled = true;
  $('tab-skyline').disabled = true;
  $('btn-rotate').hidden = true;
  $('btn-reset').hidden = true;
}
$('tab-skyline').disabled = true; // needs data
$('tab-chart').disabled = true;

// -- tabs ----------------------------------------------------------------------
function setView(name) {
  view = name;
  for (const tab of document.querySelectorAll('.tabs [role=tab]')) {
    tab.setAttribute('aria-selected', String(tab.dataset.view === name));
  }
  const is3d = name !== 'chart';
  $('gl').hidden = !is3d || !stage.supported;
  $('chart2d').hidden = is3d;
  $('btn-rotate').disabled = !is3d;
  $('btn-reset').disabled = !is3d;
  tooltip.hidden = true;
  if (is3d && stage.supported) stage.show(name);
  if (name === 'chart' && report) renderChart($('chart2d'), report);
  $('legend').textContent = `${LEGEND[name](report)}${is3d && stage.supported ? ` ${HINT_3D}` : ''}`;
}

for (const tab of document.querySelectorAll('.tabs [role=tab]')) {
  tab.addEventListener('click', () => !tab.disabled && setView(tab.dataset.view));
  tab.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const tabs = [...document.querySelectorAll('.tabs [role=tab]')].filter((t) => !t.disabled);
    const next = tabs[(tabs.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
    next.focus();
    setView(next.dataset.view);
  });
}
$('btn-reset').addEventListener('click', () => stage.resetView?.());
$('btn-rotate').addEventListener('click', (e) => {
  const on = e.currentTarget.getAttribute('aria-pressed') !== 'true';
  e.currentTarget.setAttribute('aria-pressed', String(on));
  stage.setAutoRotate?.(on);
});

// -- rendering -----------------------------------------------------------------
function renderVideoCard(r) {
  const v = r.video;
  $('thumb').replaceChildren(
    v.thumbnail ? el('img', { attrs: { src: v.thumbnail, alt: '', referrerpolicy: 'no-referrer', loading: 'lazy' } }) : el('span', { cls: 'thumb-ph' }),
  );
  const title = $('vc-title');
  title.textContent = v.title || 'Untitled Short';
  if (r.meta.source === 'youtube') title.href = v.url;
  else title.removeAttribute('href');
  $('vc-meta').textContent = `${v.channel_title}, ${ageText(v.age_hours)} old, ${compact(v.views)} views`;
  $('video-card').hidden = false;
}

function renderScore(r) {
  $('ring-fg').setAttribute('stroke-dasharray', `${r.score.value} 100`);
  $('score-value').textContent = Math.round(r.score.value);
  $('score-tier').textContent = r.score.tier;
  $('score-card').dataset.tier = r.score.tier.toLowerCase();
  $('score-card').hidden = false;
}

function renderForecast(r) {
  const f = r.forecast;
  $('forecast-sub').textContent = `From ${full(f.current_views)} views, ${ageText(r.video.age_hours)} after upload. Confidence: ${r.meta.confidence}, based on ${r.meta.peers_used} recent Shorts from this channel.`;
  const body = $('ladder-body');
  body.replaceChildren();
  for (const h of f.horizons) {
    body.append(
      el('tr', {}, el('th', { text: h.days === 1 ? '1 day' : `${h.days} days`, attrs: { scope: 'row' } }), el('td', { cls: 'strong', text: full(h.p50) }),
        el('td', { text: `${compact(h.p10)} to ${compact(h.p90)}` }), el('td', { text: h.growth_pct == null ? '–' : `+${h.growth_pct.toFixed(h.growth_pct >= 100 ? 0 : 1)}%` })),
    );
  }
  const lt = f.lifetime;
  body.append(el('tr', { cls: 'lifetime' }, el('th', { text: 'Lifetime', attrs: { scope: 'row' } }), el('td', { cls: 'strong', text: full(lt.p50) }),
    el('td', { text: `${compact(lt.p10)} to ${compact(lt.p90)}` }), el('td', { text: lt.p50 && f.current_views ? `+${(((lt.p50 / f.current_views) - 1) * 100).toFixed(0)}%` : '–' })));

  const miles = $('milestones');
  miles.replaceChildren();
  for (const m of f.milestones) {
    const text = m.status === 'eta' ? `in about ${etaText(m.eta_days)}` : m.status === 'later' ? 'more than 30 days away' : 'unlikely on the current path';
    miles.append(el('li', { cls: `m-${m.status}` }, el('strong', { text: `${compact(m.target)} views` }), el('span', { text })));
  }

  const daily = $('daily');
  daily.replaceChildren();
  const maxHi = Math.max(...f.daily.map((d) => d.views_p90), 1);
  for (const d of f.daily) {
    const bar = el('i', { cls: 'bar' });
    bar.style.setProperty('--hi', ((d.views_p90 / maxHi) * 100).toFixed(1)); // unitless 0..100, CSS turns it into px
    bar.style.setProperty('--h', ((d.views / maxHi) * 100).toFixed(1));
    const cell = el('div', { cls: 'day', attrs: { title: `Day ${d.day}: about ${full(d.views)} views (${full(d.views_p10)} to ${full(d.views_p90)})` } }, bar, el('span', { text: String(d.day) }));
    daily.append(cell);
  }
}

function renderSignals(r) {
  const s = r.signals;
  const v = r.video;
  const rows = [
    ['Views', full(v.views)],
    ['Likes', v.likes == null ? 'Hidden' : full(v.likes)],
    ['Comments', v.comments == null ? 'Off' : full(v.comments)],
    ['Like rate', s.like_rate == null ? '–' : `${pct(s.like_rate)}${s.channel_median_like_rate ? ` (channel median ${pct(s.channel_median_like_rate)})` : ''}`],
    ['Comment rate', s.comment_rate == null ? '–' : pct(s.comment_rate, 2)],
    ['Views per hour, average', full(Math.round(s.views_per_hour))],
    ['Views in the last 24 hours', s.views_last_24h == null ? 'Not tracked yet' : full(s.views_last_24h)],
    ['Views compared with subscribers', s.reach_ratio == null ? 'Hidden' : times(s.reach_ratio)],
    ['Typical Short from this channel at this age', s.expected_views_now == null ? '–' : full(s.expected_views_now)],
    ['Pace against the channel', s.performance_index == null ? '–' : times(s.performance_index)],
  ];
  const dl = $('signals');
  dl.replaceChildren();
  for (const [k, val] of rows) dl.append(el('div', {}, el('dt', { text: k }), el('dd', { text: val })));

  const sent = s.sentiment;
  $('sentiment').hidden = !sent;
  if (sent) {
    const bar = $('sent-bar');
    bar.querySelector('.s-pos').style.setProperty('--w', `${sent.positive * 100}%`);
    bar.querySelector('.s-neu').style.setProperty('--w', `${sent.neutral * 100}%`);
    bar.querySelector('.s-neg').style.setProperty('--w', `${sent.negative * 100}%`);
    const label = `${Math.round(sent.positive * 100)}% positive, ${Math.round(sent.neutral * 100)}% neutral, ${Math.round(sent.negative * 100)}% negative`;
    bar.setAttribute('aria-label', label);
    $('sent-text').textContent = `Comment tone across ${sent.n} comments: ${label}.`;
  }
}

function renderInsights(r) {
  const ul = $('insights');
  ul.replaceChildren(...r.insights.map((i) => el('li', { cls: `tone-${i.tone}`, text: i.text })));

  const m = r.model;
  const share = r.forecast.curve_share;
  const paras = [
    `The forecast compares this Short with ${r.meta.peers_used} other recent Shorts from the same channel. Because they were uploaded at different times, together they show how this channel's Shorts collect views as they age. A typical one here earns about ${Math.round(share.day1 * 100)}% of its lifetime views in the first day and ${Math.round(share.day7 * 100)}% within a week.`,
    'This video is then moved along that curve from where it stands today. The range comes from how uncertain the curve is and from how much single videos differ from their channel average.',
  ];
  if (m.cohort.prior_only) paras.push('Too few peers were found to fit this channel, so a typical Shorts curve was used and the range is wide.');
  if (m.live) {
    paras.push(`It is also blended, at ${Math.round(m.live.weight * 100)}% weight, with a growth curve fitted to ${m.live.points} recorded view counts for this video (Runge-Kutta 4 integration of dV/dt = r V (1 - V/K), r = ${m.live.r_per_day} per day, saturation near ${compact(m.live.saturation_views)}).`);
  } else {
    paras.push('No view history has been recorded for this video yet. Every analysis stores a reading, so forecasts sharpen as you come back to it.');
  }
  $('how-body').replaceChildren(...paras.map((t) => el('p', { text: t })));
}

function render(r) {
  report = r;
  $('stage-note').hidden = true;
  $('tab-skyline').disabled = !stage.supported;
  $('tab-chart').disabled = false;
  renderVideoCard(r);
  renderScore(r);
  renderForecast(r);
  renderSignals(r);
  renderInsights(r);
  $('results').hidden = false;
  if (stage.supported) stage.setReport(r);
  const next = !stage.supported ? 'chart' : view;
  setView(next);
  if (stage.supported) stage.setAutoRotate(true);
  $('btn-rotate').setAttribute('aria-pressed', String(stage.supported));
  $('gl').setAttribute('aria-label', `3D growth surface for ${r.video.title}. Views now ${compact(r.forecast.current_views)}, about ${compact(r.forecast.horizons.find((h) => h.days === 7).p50)} in 7 days.`);
}

// -- data loading --------------------------------------------------------------
function showError(msg) {
  const box = $('error');
  box.textContent = msg;
  box.hidden = false;
}

function setBusy(busy) {
  stageEl.setAttribute('aria-busy', String(busy));
  stageEl.classList.toggle('is-busy', busy);
  const btn = $('analyze-btn');
  btn.disabled = busy;
  btn.textContent = busy ? 'Analyzing' : 'Forecast growth';
}

async function analyze(input) {
  $('error').hidden = true;
  setBusy(true);
  try {
    const res = await fetch(`/api/analyze?video=${encodeURIComponent(input)}`, { headers: { Accept: 'application/json' } });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(data?.error?.message || `The request failed (${res.status}).`);
    render(data);
    history.replaceState(null, '', `?v=${encodeURIComponent(data.video.id)}`);
  } catch (err) {
    showError(err instanceof TypeError ? 'Could not reach the server. Check your connection and try again.' : err.message);
  } finally {
    setBusy(false);
  }
}

$('analyze-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const value = $('video-input').value.trim();
  if (value) analyze(value);
});
for (const chip of document.querySelectorAll('.chip')) {
  chip.addEventListener('click', () => {
    $('video-input').value = chip.dataset.video;
    analyze(chip.dataset.video);
  });
}

setView('surface');
const initial = new URLSearchParams(location.search).get('v');
if (initial) {
  $('video-input').value = initial;
  analyze(initial);
}
