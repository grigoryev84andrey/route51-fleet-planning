/* Route 51 — Fleet planning console (three steps).
   The page shows what the server returns; it does not recompute any scientific
   quantity. Statuses, labels and reasons come from the server's result objects. */
'use strict';

const $ = (id) => document.getElementById(id);
const S = {
  config: null, session: null, step: 1, ww: 1, economics: null,
  polling: {}, hidden: false, lastAction: null, exportOut: null,
  inspectedPlan: null, inspectedFront: null,
  exportKey: null, exporting: false,
};

/* ── formatting ─────────────────────────────────────────────────────────── */
const NBSP = ' ';
function n(v, d = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
    .replace(/,/g, NBSP);
}
function change(newV, oldV) {
  if (newV == null || oldV == null || oldV === 0) return null;
  return 100 * (newV - oldV) / oldV;
}
function changeText(p, lowerIsBetter = true) {
  if (p === null) return { text: '', cls: '' };
  if (Math.abs(p) < 0.05) return { text: 'no change', cls: '' };
  const word = p > 0 ? 'higher' : 'lower';
  const worse = lowerIsBetter ? p > 0 : p < 0;
  return { text: `${p > 0 ? '+' : '−'}${Math.abs(p).toFixed(1)}% · ${word}`, cls: worse ? 'worse' : 'better' };
}
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined) e.append(c.nodeType ? c : String(c));
  return e;
}
function kv(target, pairs) {
  target.replaceChildren(...pairs.map(([k, v]) => el('div', {}, el('dt', { text: k }), el('dd', { text: v }))));
}

const REASONS = {
  no_simulation: 'no simulation of this plan',
  simulation_not_registered: 'simulated in this session, but not a registered confirmatory series',
  simulation_not_applicable: 'the simulation does not match this plan or is incomplete',
  not_carried_over_from_morning_simulation: 'the morning simulation is not carried over to the whole day',
  no_sourced_rate: 'no sourced money rate per vehicle-km',
  ivt_not_instrumented: 'passenger in-vehicle time was not measured',
  ivt_unknown: 'passenger in-vehicle time unknown',
  not_recorded_for_frozen_matrix: 'not recorded for the frozen matrix',
  frozen_matrix_generator_config_unknown: 'the generator settings of the frozen matrix are not recorded',
};
const reason = (code) => REASONS[code] || code || 'unknown';

/* ── storage (per-viewer convenience only) ─────────────────────────────── */
function store(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ } }
function recall(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; } }

/* ── API ────────────────────────────────────────────────────────────────── */
async function api(path, body) {
  const opt = body === undefined ? {} :
    { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch('/api' + path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { /* non-JSON */ }
  if (!r.ok) {
    const d = (data && (data.detail || data)) || {};
    const err = new Error(d.message || (typeof d === 'string' ? d : `Request failed (${r.status})`));
    err.code = d.code; err.status = r.status;
    throw err;
  }
  return data;
}

/* ── banner and progress ───────────────────────────────────────────────── */
function showError(message, retry) {
  $('banner-text').textContent = message;
  $('banner-retry').hidden = !retry;
  $('banner').hidden = false;
}
function hideError() { $('banner').hidden = true; }

function showProgress(job) {
  const busy = Object.keys(S.polling).length > 0;
  if (!busy) { $('progress').hidden = true; $('progress-show').hidden = true; return; }
  if (S.hidden) { $('progress').hidden = true; $('progress-show').hidden = false; }
  else { $('progress').hidden = false; $('progress-show').hidden = true; }
  if (!job) return;
  $('progress-stage').textContent = job.stage || 'Working…';
  const units = job.total_units > 0 ? `${job.done_units} of ${job.total_units} units completed` : 'Started';
  $('progress-note').textContent = `${units} · elapsed ${Math.round(job.elapsed_s)} s${job.note ? ' · ' + job.note : ''}`;
  $('progress-bar-wrap').hidden = !(job.total_units > 0);
  $('progress-bar').style.width = job.total_units > 0 ? `${(100 * job.done_units / job.total_units).toFixed(1)}%` : '0';
}

const FAIL_TEXT = {
  data: 'Preparing the scenario failed.',
  front: 'Calculation failed. The previous result is shown for reference and does not match the edited conditions.',
  simulate: 'The simulation failed. No simulation result is shown for this plan.',
  holds: 'Re-optimising for each scenario failed.',
};

async function startJob(kind, path, body) {
  hideError();
  S.lastAction = { kind, path, body };
  try {
    const res = await api(path, body);
    track(kind, res.job);
  } catch (e) {
    showError(`${FAIL_TEXT[kind] || 'The request failed.'} ${e.message}`, true);
  }
  render();
}

function track(kind, job) {
  S.polling[kind] = job.id;
  const pend = recall('r51.pending') || {};
  pend[kind] = job.id; store('r51.pending', pend);
  showProgress(job);
  const tick = async () => {
    let j;
    try { j = await api(`/jobs/${job.id}`); }
    catch (e) {
      delete S.polling[kind];
      if (e.code === 'job_unknown') {
        showError('The server no longer knows this calculation — it was probably restarted. ' +
                  'Work in progress was lost; start the calculation again.', true);
      } else {
        showError(`Lost contact with the server: ${e.message}`, true);
      }
      showProgress(); render(); return;
    }
    if (j.state === 'running') { showProgress(j); setTimeout(tick, 800); return; }
    delete S.polling[kind];
    const p = recall('r51.pending') || {}; delete p[kind]; store('r51.pending', p);
    if (j.state === 'completed') {
      if (j.published === false) {
        $('step1-hint').textContent = 'A calculation for earlier conditions finished; it was kept as history and is not shown.';
      }
    } else if (j.state === 'failed') {
      showError(`${FAIL_TEXT[kind]} Reason: ${j.error || 'unknown'}`, true);
    } else if (j.state === 'interrupted') {
      showError(j.error || 'The calculation was interrupted.', true);
    }
    showProgress();
    await refresh();
    if (j.state === 'completed' && j.published) afterJob(kind);
  };
  setTimeout(tick, 400);
}

function afterJob(kind) {
  if (kind === 'data') {
    // continue to plans automatically only if the data were prepared as the first half of one action
    if (S.chainFront) { S.chainFront = false; runFront(); }
  } else if (kind === 'front') {
    go(2);
  } else if (kind === 'simulate') {
    loadEconomics();
  }
}

/* ── form state ─────────────────────────────────────────────────────────── */
function cond() {
  return {
    matrix: document.querySelector('input[name=entry]:checked').value,
    preset: $('in-preset').value,
    seed: Number($('in-seed').value),
    scale: Number($('in-scale').value),
    budget_idx: Number($('in-budget').value),
    delta_idx: Number($('in-delta').value),
  };
}
function dataMatches(d, c) {
  if (!d) return false;
  if (d.matrix !== c.matrix || d.scale !== c.scale) return false;
  return c.matrix === 'thesis' || (d.preset_id === c.preset && d.seed === c.seed);
}
function frontMatches(f, c) {
  return !!f && f.conditions.budget_idx === c.budget_idx && f.conditions.delta_idx === c.delta_idx &&
         f.conditions.scale === c.scale;
}
function busy(kind) { return kind ? !!S.polling[kind] : Object.keys(S.polling).length > 0; }

/* ── actions ────────────────────────────────────────────────────────────── */
function runData(chain) {
  const c = cond();
  if (!Number.isInteger(c.seed) || c.seed < 0) { showError('The random seed must be a whole number of 0 or more.'); return; }
  S.chainFront = !!chain;
  startJob('data', '/data/run', { source: 'synthetic', matrix: c.matrix, preset: c.preset, seed: c.seed, scale: c.scale });
}
function runFront() {
  const c = cond();
  startJob('front', '/plan/front', { budget_idx: c.budget_idx, delta_idx: c.delta_idx });
}
function step1Action() {
  const s = S.session || {}, c = cond();
  if (!dataMatches(s.data, c)) return runData(true);
  if (!frontMatches(s.front, c) || (s.stale && s.stale.front)) return runFront();
  go(2);
}
async function selectPlan(id) {
  try { await api('/plan/select', { id }); } catch (e) { showError(e.message); }
  await refresh();
  S.economics = null; S.exportOut = null;
}
async function loadEconomics() {
  try { S.economics = await api('/result/economics', {}); } catch (e) { S.economics = null; }
  renderEconomics();
}
async function doExport() {
  if (S.exporting) return;
  hideError();
  const key = reportKey();
  S.exporting = true;
  renderStep3();
  try {
    const result = await api('/export', { wait_weight: S.ww });
    if (key === reportKey()) { S.exportOut = result; S.exportKey = key; }
  } catch (e) { showError(`The report could not be prepared. ${e.message}`); }
  finally { S.exporting = false; renderStep3(); }
  if (S.exportOut) {
    $('report-preview-title').focus();
    $('report-preview').scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
}
async function keepPlan() {
  try { const r = await api('/scenario/save', {}); S.session.scenarios = r.scenarios; }
  catch (e) { showError(e.message); }
  renderKept();
}

/* ── navigation ─────────────────────────────────────────────────────────── */
function go(step) {
  S.step = step;
  for (const i of [1, 2, 3]) $(`step-${i}`).hidden = i !== step;
  render();
  $(`h-step-${step}`).setAttribute('tabindex', '-1');
  $(`h-step-${step}`).focus();
  if (step === 3) { loadEconomics(); }
}

/* ── rendering ──────────────────────────────────────────────────────────── */
function render() {
  const s = S.session || {};
  const f = s.front;
  $('nav-2').disabled = !f;
  $('nav-3').disabled = !(f && s.selected);
  for (const i of [1, 2, 3]) {
    const b = $(`nav-${i}`);
    if (S.step === i) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
  }
  renderStep1(); renderStep2(); renderStep3();
}

function renderStep1() {
  const s = S.session || {}, c = cond();
  $('field-preset').hidden = c.matrix !== 'generate';
  const p = (S.config.presets || []).find((x) => x.id === c.preset);
  $('preset-line').textContent = p ? p.line : '';
  const btn = $('btn-step1');
  const working = busy('data') || busy('front');
  let label;
  if (working) label = 'Working…';
  else if (!dataMatches(s.data, c)) label = c.matrix === 'thesis' ? 'Load example' : 'Prepare scenario';
  else if (!s.front) label = 'Calculate plans';
  else if (!frontMatches(s.front, c) || s.stale.front) label = 'Recalculate plans';
  else label = 'Go to plan comparison';
  btn.textContent = label;
  btn.disabled = working;
  $('step1-hint').textContent = (!working && s.front && !frontMatches(s.front, c))
    ? 'Conditions changed: the plans shown were calculated for earlier conditions.' : '';

  const d = s.data;
  $('data-card').hidden = !d;
  if (d) {
    const q = d.quality || {};
    const pairs = [
      ['Data', d.source_label],
      ['Modelled daily boardings', `${n(d.modelled_daily_boardings)} (demand × ${d.scale}, assumption)`],
      ['Alighting distribution similarity', q.alighting_distribution_similarity == null ? '—' : n(q.alighting_distribution_similarity, 3)],
      ['Largest 20 OD flows recovered', q.top20_od_matched == null ? '—' : `${q.top20_od_matched} of 20`],
    ];
    if (d.counts && d.counts.fare_taps != null) {
      pairs.push(['Input records', `${n(d.counts.fare_taps)} fare taps · ${n(d.counts.signalling_events)} signalling events`]);
    } else {
      pairs.push(['Input records', 'not recorded for the frozen matrix']);
    }
    if (d.privacy && d.privacy.k != null) {
      pairs.push(['Suppressed aggregate cells (k = ' + d.privacy.k + ')',
        `${n(d.privacy.cells_suppressed)} of ${n(d.privacy.cells_received)}`]);
    }
    kv($('data-kv'), pairs);
    $('data-note').textContent = `${q.note || ''} Similarity is measured against the synthetic ground truth of the generator.` +
      (d.privacy && d.privacy.note ? ' ' + d.privacy.note : '');
  }
}

const METRICS = [
  ['Waiting', 'wait', 'pax·min/day', 0],
  ['Crowding disutility', 'crowd_min', 'pax·min/day', 0],
  ['Waiting + crowding disutility', 'gen_cost', 'pax·min/day', 0],
  ['Operating-cost index', 'cost', 'relative veh-km', 1],
  ['Longest headway', 'maxh', 'min', 1],
  ['Vehicles needed (normative)', 'fleet_needed', 'vehicles', 0],
  ['Highest load / full capacity', 'max_phi', 'ratio', 2],
];
function metric(dm, key) {
  if (key === 'maxh') return Math.max(...Object.values(dm.effective_headways));
  return dm[key];
}

function renderStep2() {
  const s = S.session || {}, f = s.front;
  if (!f) return;
  const c = cond();
  $('compare-stale').hidden = frontMatches(f, c) && !s.stale.front;
  $('compare-lead').textContent = `Current timetable and up to three alternatives. Maximum operating-cost increase ` +
    `${f.conditions.budget_label}; maximum headway spread ${f.conditions.delta_label}; demand × ${f.conditions.scale}. ` +
    `Calculated in ${f.seconds} s with the protocol's three optimiser runs.`;
  const base = f.baseline.dispatch_metrics;
  const head = el('tr', {}, el('th', { scope: 'col', text: 'Measure' }), el('th', { scope: 'col', class: 'alt', text: 'Current timetable' }),
    ...f.alternatives.map((a) => el('th', { scope: 'col', class: 'alt' },
      el('label', {}, el('input', { type: 'radio', name: 'plan', value: a.id, checked: a.id === s.selected,
        onchange: () => selectPlan(a.id) }), ' ', a.label),
      el('span', { class: 'tag', text: `plan ${a.id}` }))));
  const rows = METRICS.map(([name, key, unit, d]) => el('tr', {},
    el('th', { scope: 'row' }, name, el('span', { class: 'chg', text: unit })),
    el('td', { text: n(metric(base, key), d) }),
    ...f.alternatives.map((a) => {
      const v = metric(a.dispatch_metrics, key);
      const ch = changeText(change(v, metric(base, key)));
      return el('td', {}, n(v, d), el('span', { class: `chg ${ch.cls}`, text: ch.text }));
    })));
  const fz = el('tr', {}, el('th', { scope: 'row', text: 'Within the limits after rounding to whole trips' }),
    el('td', { text: f.baseline.feasibility.feasible ? 'yes' : 'no (current timetable)' }),
    ...f.alternatives.map((a) => el('td', { text: a.feasibility.feasible ? 'yes' : 'no' })));
  $('compare-table').replaceChildren(el('thead', {}, head), el('tbody', {}, ...rows, fz));

  const t = s.table;
  if (t) {
    $('plan-table').replaceChildren(
      el('thead', {}, el('tr', {}, ...['Period', 'Current headway, min', 'Plan headway, min (effective)',
        'Trips per direction', '18 m trips', '18 m share: desired / actual'].map((x) => el('th', { scope: 'col', text: x })))),
      el('tbody', {}, ...t.rows.map((r) => el('tr', {},
        el('th', { scope: 'row', text: r.label }), el('td', { text: n(r.baseline_headway_min, 1) }),
        el('td', { text: n(r.plan_effective_headway_min, 2) }), el('td', { text: r.trips_per_direction }),
        el('td', { text: r.trips_18m_per_direction }),
        el('td', { text: `${n(100 * r.share_18m_desired, 0)}% / ${n(100 * (r.share_18m_actual || 0), 0)}%` })))));
  }
  renderPareto();
}

/* The plot uses the server's relaxed objectives, never rounded dispatch values. */
function renderPareto() {
  const f = S.session?.front;
  if (!f) return;
  const plans = f.all_plans.filter((p) => [p.wait, p.crowd_min, p.cost].every(Number.isFinite));
  const revision = JSON.stringify(f.revision);
  if (S.inspectedFront !== revision || !plans.some((p) => p.id === S.inspectedPlan)) {
    S.inspectedFront = revision;
    S.inspectedPlan = plans.find((p) => p.id === S.session.selected)?.id || plans[0]?.id;
  }
  const svg = $('pareto-chart');
  svg.replaceChildren();
  $('pareto-count').textContent = `${n(plans.length)} calculated plans · three optimiser runs`;
  const named = new Map(f.alternatives.map((a) => [a.id, a.keys.map((k) => ({
    recommended: 'Recommended', least_waiting: 'Least waiting', least_crowding: 'Least crowding',
  }[k] || k)).join(' / ')]));
  const inspect = (id, focusPoint = false) => {
    S.inspectedPlan = id;
    renderPareto();
    if (focusPoint) svg.querySelector('[aria-pressed="true"]')?.focus();
  };
  $('pareto-plan').replaceChildren(...plans.map((p) => el('option', {
    value: p.id, selected: p.id === S.inspectedPlan,
    text: `Plan ${p.id}${named.has(p.id) ? ' · ' + named.get(p.id) : ''}`,
  })));
  $('pareto-plan').disabled = !plans.length;
  $('pareto-legend').replaceChildren(
    el('span', { class: 'pareto-key', text: 'Calculated plan' }),
    el('span', { class: 'pareto-key named', text: 'Named alternative' }),
    el('span', { class: 'pareto-key selected', text: 'Inspected plan' }));
  const picked = plans.find((p) => p.id === S.inspectedPlan);
  $('pareto-detail-title').textContent = picked
    ? `Plan ${picked.id}${named.has(picked.id) ? ' · ' + named.get(picked.id) : ''}` : 'No calculated plans';
  kv($('pareto-metrics'), picked ? [
    ['Waiting', `${n(picked.wait)} pax·min/day`],
    ['Crowding disutility', `${n(picked.crowd_min)} pax·min/day`],
    ['Waiting + crowding disutility', `${n(picked.gen_cost)} pax·min/day`],
    ['Operating-cost index', `${n(picked.cost, 1)} relative veh-km`],
    ['Longest headway', `${n(picked.max_headway, 1)} min`],
    ['Selection-rule rank', `${picked.rank + 1} of ${plans.length}`],
  ] : []);
  if (!plans.length) return;
  const mk = (tag, attrs = {}, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const L = 105, R = 905, T = 105, B = 435;
  const extent = (key) => {
    const values = plans.map((p) => p[key]);
    const lo = Math.min(...values), hi = Math.max(...values);
    const pad = Math.max((hi - lo) * 0.09, Math.abs(hi) * 0.01, 1);
    return [Math.max(0, lo - pad), hi + pad];
  };
  const [xmin, xmax] = extent('wait'), [ymin, ymax] = extent('crowd_min');
  const sx = (v) => L + (v - xmin) / (xmax - xmin) * (R - L);
  const sy = (v) => B - (v - ymin) / (ymax - ymin) * (B - T);
  const maxCost = Math.max(...plans.map((p) => p.cost), 1);
  const radius = (p) => Math.max(3, 8 * Math.sqrt(Math.max(0, p.cost) / maxCost));
  const add = (tag, attrs, text) => { const node = mk(tag, attrs, text); svg.append(node); return node; };
  for (let i = 0; i <= 4; i++) {
    const x = L + (R - L) * i / 4, y = B - (B - T) * i / 4;
    add('line', { x1: x, x2: x, y1: T, y2: B, class: 'pareto-grid' });
    add('line', { x1: L, x2: R, y1: y, y2: y, class: 'pareto-grid' });
    add('text', { x, y: B + 25, 'text-anchor': 'middle', class: 'pareto-tick' }, n(xmin + (xmax - xmin) * i / 4));
    add('text', { x: L - 14, y: y + 5, 'text-anchor': 'end', class: 'pareto-tick' }, n(ymin + (ymax - ymin) * i / 4));
  }
  add('path', { d: `M ${L} ${T} V ${B} H ${R}`, class: 'pareto-axis' });
  add('text', { x: (L + R) / 2, y: 490, 'text-anchor': 'middle', class: 'pareto-axis-label' }, 'Passenger waiting · pax·min/day');
  add('text', { transform: 'translate(22 270) rotate(-90)', 'text-anchor': 'middle', class: 'pareto-axis-label' }, 'Crowding disutility · pax·min/day');
  // Named labels occupy separate lanes, with leaders to their exact coordinates.
  const labelled = plans.filter((p) => named.has(p.id)).sort((a, b) => a.wait - b.wait);
  labelled.forEach((p, i) => {
    const x = L + (R - L) * (i + 0.5) / labelled.length;
    add('path', { d: `M ${x} 65 V 82 L ${sx(p.wait)} ${sy(p.crowd_min)}`, class: 'pareto-leader' });
    const label = add('text', { x, y: 38, 'text-anchor': 'middle', class: 'pareto-label' });
    const names = named.get(p.id).split(' / ');
    names.forEach((name, j) => label.append(mk('tspan', { x, dy: j ? 17 : 0 }, name)));
    label.append(mk('tspan', { x, dy: 18, class: 'pareto-tick' }, `Plan ${p.id}`));
  });
  // Paint the inspected point last so it remains visible in a dense cluster.
  const priority = (p) => p.id === picked.id ? 2 : Number(named.has(p.id));
  const ordered = [...plans].sort((a, b) => priority(a) - priority(b));
  ordered.forEach((p) => {
    const selected = p.id === picked.id;
    const group = add('g', { class: `pareto-point${named.has(p.id) ? ' named' : ''}${selected ? ' selected' : ''}`,
      role: 'button', tabindex: selected ? 0 : -1, 'aria-pressed': String(selected),
      'aria-label': `Plan ${p.id}${named.has(p.id) ? ', ' + named.get(p.id) : ''}: waiting ${n(p.wait)}, crowding ${n(p.crowd_min)}, cost ${n(p.cost, 1)}` });
    group.append(mk('title', {}, `Plan ${p.id} · waiting ${n(p.wait)} · crowding ${n(p.crowd_min)} · cost ${n(p.cost, 1)}`));
    group.append(mk('circle', { cx: sx(p.wait), cy: sy(p.crowd_min), r: radius(p) + 5, class: 'pareto-hit' }));
    group.append(mk('circle', { cx: sx(p.wait), cy: sy(p.crowd_min), r: radius(p), class: 'pareto-dot' }));
    if (selected) group.append(mk('circle', { cx: sx(p.wait), cy: sy(p.crowd_min), r: radius(p) + 5, class: 'pareto-ring' }));
    group.addEventListener('keydown', (event) => {
      const index = plans.findIndex((plan) => plan.id === p.id);
      let next;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % plans.length;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + plans.length - 1) % plans.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = plans.length - 1;
      if (event.key === 'Enter' || event.key === ' ') next = index;
      if (next !== undefined) { event.preventDefault(); inspect(plans[next].id, true); }
    });
  });
  // Resolve by nearest centre rather than SVG paint order: hit areas overlap.
  svg.onclick = (event) => {
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const cursor = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    const distance = (p) => Math.hypot(sx(p.wait) - cursor.x, sy(p.crowd_min) - cursor.y);
    const nearest = plans.reduce((best, p) => distance(p) < distance(best) ? p : best);
    if (distance(nearest) <= 20) inspect(nearest.id, true);
  };
}

function currentAlt() {
  const s = S.session || {};
  return s.front && s.front.alternatives.find((a) => a.id === s.selected);
}

function renderStep3() {
  const s = S.session || {}, f = s.front, a = currentAlt();
  if (!f || !a) return;
  const base = f.baseline.dispatch_metrics, m = a.dispatch_metrics;
  $('validate-lead').textContent = `Plan ${a.id} — ${a.label}. The model result, the simulation status and the economic scope are shown separately.`;
  const cw = changeText(change(m.wait, base.wait)), cc = changeText(change(m.crowd_min, base.crowd_min));
  const co = changeText(change(m.cost, base.cost));
  kv($('model-kv'), [
    ['Waiting', `${n(m.wait)} pax·min/day (${cw.text})`],
    ['Crowding disutility', `${n(m.crowd_min)} pax·min/day (${cc.text})`],
    ['Operating-cost index', `${n(m.cost, 1)} (${co.text})`],
    ['Vehicles needed (normative)', `${m.fleet_needed} of ${S.config.corridor.fleet}`],
  ]);
  $('fleet-tag').textContent = 'Fleet availability not verified';

  const sim = s.simulation;
  const simBusy = busy('simulate');
  const registeredAvailable = !!a.registered_result_available;
  $('sim-status').textContent = simBusy
    ? (registeredAvailable ? 'Opening registered result' : 'Simulation running')
    : (sim ? sim.status_label : 'Not simulated for this plan');
  const body = [];
  if (!sim && !simBusy) {
    body.push(el('p', { class: 'muted', text: registeredAvailable
      ? 'A matching confirmatory result is saved in the accepted release. Open it without rerunning SUMO.'
      : (S.config.capabilities.simulation
        ? `Runs ${S.config.protocol.simulation_runs} + ${S.config.protocol.simulation_runs} SUMO runs (current timetable and this plan). This takes several minutes and is exploratory for a new plan.`
        : 'SUMO is not available on this machine; a new simulation cannot be run here.') }));
  }
  if (sim) {
    const pr = sim.primary || {};
    if (pr.defined) {
      body.push(el('p', {}, `Mean full-trip bus duration of the 07:00–10:00 departures, both directions: current `,
        el('strong', { text: `${n(pr.baseline_mean, 2)} min` }), ', plan ', el('strong', { text: `${n(pr.candidate_mean, 2)} min` }),
        `. Difference (current − plan) ${pr.delta_min >= 0 ? '+' : '−'}${n(Math.abs(pr.delta_min), 2)} min, 95% CI ${n(pr.ci95[0], 2)} … ${n(pr.ci95[1], 2)} min; ${pr.n_baseline} + ${pr.n_candidate} runs.`));
    }
    body.push(el('p', { class: 'muted small', text: `Evidence: ${sim.evidence_kind}; effect status: ${sim.effect_status}. ` +
      'This measures bus trip duration, not an individual passenger’s in-vehicle time. Queues and denied boarding are not modelled.' }));
    if (sim.evidence_note) body.push(el('p', { class: 'muted small', text: sim.evidence_note }));
    if (sim.invalid_runs && sim.invalid_runs.length) body.push(el('p', { text: `${sim.invalid_runs.length} run(s) invalid — no confirmatory conclusion.` }));
  }
  $('sim-body').replaceChildren(...body);

  // conditions edited on step 1 but not recalculated: nothing new may be produced here
  const edited = !frontMatches(f, cond()) || !dataMatches(s.data, cond()) || (s.stale && s.stale.front);
  $('validate-lead').textContent += edited
    ? ' Conditions changed on step 1: recalculate plans before simulating or exporting.' : '';

  // one visually primary action at a time
  const canSim = (registeredAvailable || S.config.capabilities.simulation) && !sim;
  $('btn-simulate').hidden = !canSim;
  $('btn-simulate').disabled = simBusy || edited;
  $('btn-simulate').textContent = simBusy ? 'Working…'
    : (registeredAvailable ? 'View registered result' : 'Run full simulation');
  $('btn-export').className = canSim ? 'btn btn-secondary' : 'btn btn-primary';
  $('btn-export').textContent = S.exporting ? 'Preparing report…' : (sim ? 'Preview report' : 'Preview model-only report');
  $('btn-export').disabled = S.exporting || busy() || edited;
  $('btn-holds').disabled = busy('holds');
  renderHolds(); renderEconomics(); renderExport(); renderKept();
}

function renderEconomics() {
  const e = S.economics, box = $('eco');
  if (!e) { box.replaceChildren(el('p', { class: 'muted', text: 'Loading…' })); return; }
  const w = `wait_weight_${S.ww}`;
  const am = e.scopes.am_peak.by_wait_weight[w], day = e.scopes.all_day_model.by_wait_weight[w];
  const mn = (v) => (v == null ? null : `${n(v / 1e6)} million UZS/year`);
  const ra = e.resources;
  const value = (text, important = true) => important ? el('strong', { text }) : text;
  const benefit = (v, format = n) => value(format(v), v != null && v > 0);
  box.replaceChildren(
    el('section', {}, el('h3', { text: 'Passengers' }),
      el('p', {}, 'Estimated passenger benefit — morning peak (model): ', benefit(am.passenger_model_uzs, mn)),
      el('p', { class: 'small muted' }, 'Waiting ', benefit(am.wait_benefit_h), ' h/year, crowding ', benefit(am.crowd_benefit_h), ' h/year (positive = benefit).'),
      el('p', {}, 'Estimated passenger benefit — whole day (model): ', benefit(day.passenger_model_uzs, mn)),
      el('p', { class: 'small muted' }, 'Waiting ', benefit(day.wait_benefit_h), ' h/year, crowding ', benefit(day.crowd_benefit_h), ' h/year.'),
      am.passenger_with_ivt_uzs == null
        ? el('p', { class: 'small', text: `In-vehicle time: not included — ${reason(am.passenger_with_ivt_reason)}.` })
        : el('p', { class: 'small' }, 'In-vehicle time (morning, from the registered simulation): ', benefit(am.ivt_benefit_uzs, mn), '.')),
    el('section', {}, el('h3', { text: 'Operator & fleet' }),
      el('p', {}, 'Change in 12 m-equivalent km: ', value(n(day.delta_equiv_12m_km_year)), ' per year (whole day).'),
      el('p', { class: 'small' }, 'Vehicle-km per day: current ', n(ra.baseline.all.veh_km), ', plan ', value(n(ra.candidate.all.veh_km)), ' (18 m: ', value(n(ra.candidate.all.veh_km_18m)), ').'),
      el('p', { class: 'small' }, 'Normative vehicle-hours per day: current ', n(ra.baseline.all.normative_veh_h, 1), ', plan ', value(n(ra.candidate.all.normative_veh_h, 1)), '.'),
      el('p', { class: 'small' }, 'Operator money cost: ', day.operator_delta_cost_uzs == null
        ? `unknown — ${reason(day.operator_delta_cost_reason)}` : value(mn(day.operator_delta_cost_uzs)), '.'),
      el('p', { class: 'small', text: 'Fleet availability not verified.' })),
    el('section', {}, el('h3', { text: 'Monetisation' }),
      el('p', { class: 'small' }, 'Value of time ', value(n(e.value_of_time.value_uzs_per_pax_h)), ' UZS per passenger-hour (', e.value_of_time.reference_period, '); ', e.days_per_year.value, ' days per year (assumption).'),
      el('p', { class: 'small' }, 'Sensitivity to value of time ±30% — not a confidence interval: morning ', value(mn(am.passenger_model_uzs_vot_minus_30)), ' … ', value(mn(am.passenger_model_uzs_vot_plus_30)), '.'),
      el('p', { class: 'small' }, 'Combined balance: ', day.combined_uzs == null
        ? `unknown — ${reason(day.combined_reason)}` : value(mn(day.combined_uzs)), '.'),
      el('p', { class: 'small' }, 'Money rate at which the whole-day model balance stays non-negative: ',
        day.rate_threshold_model_only.value != null ? value(n(day.rate_threshold_model_only.value)) : '',
        day.rate_threshold_model_only.value != null ? ' UZS per 12 m-equivalent km — ' : '',
        day.rate_threshold_model_only.text, '. Not a profit or payback figure.')),
  );
}

function renderHolds() {
  const h = (S.session || {}).holds, box = $('holds');
  if (!h) { box.replaceChildren(); return; }
  const tbl = (rows, title) => el('div', { class: 'table-scroll' }, el('table', {},
    el('caption', { class: 'small muted', text: title }),
    el('thead', {}, el('tr', {}, ...['Limit', 'Plan', 'Waiting + crowding vs current', 'Waiting vs current', 'Longest headway']
      .map((x) => el('th', { scope: 'col', text: x })))),
    el('tbody', {}, ...rows.map((r) => el('tr', {}, el('th', { scope: 'row', text: r.label }),
      ...(r.feasible ? [el('td', { text: r.id }), el('td', { text: changeText(r.gen_cost_change_pct).text }),
        el('td', { text: changeText(r.wait_change_pct).text }), el('td', { text: `${n(r.worst_headway_min, 1)} min` })]
        : [el('td', { colspan: 4, text: 'no plan stays within the limits' })]))))));
  const q = h.quality;
  const parts = [tbl(h.budget, 'Maximum operating-cost increase (headway-spread limit fixed)'),
    tbl(h.delta, 'Maximum headway spread across periods (cost limit fixed)')];
  if (q && q.rows) {
    parts.push(el('p', { class: 'small', text: 'Fare-tap loss → alighting distribution similarity: ' +
      q.rows.map((r) => `${r.label}: ${n(r.similarity, 3)}`).join(' · ') + '. ' + q.note }));
  } else if (q) {
    parts.push(el('p', { class: 'small muted', text: `Data-quality series: ${q.message}` }));
  }
  box.replaceChildren(...parts);
}

function reportKey() {
  const s = S.session || {};
  return JSON.stringify([s.server_instance, s.front?.revision, s.selected, s.simulation, s.holds, s.stale, cond(), S.ww]);
}

function renderExport() {
  const s = S.session || {}, sim = s.simulation;
  $('export-status').textContent = sim ? sim.status_label : 'Not simulated for this plan';
  if (S.exportKey !== reportKey() || busy()) S.exportOut = null;
  const o = S.exportOut;
  $('report-preview').hidden = !o;
  if (!o) {
    $('export-links').replaceChildren();
    $('report-frame').removeAttribute('src');
    return;
  }
  if ($('report-frame').getAttribute('src') !== o.primary_url) $('report-frame').src = o.primary_url;
  $('export-links').replaceChildren(el('div', { class: 'actions' },
    el('a', { class: 'btn btn-primary', href: o.primary_url, download: 'report.html', text: 'Download report (HTML)' }),
    el('a', { class: 'btn btn-secondary', href: o.zip_url, text: 'Download all files (ZIP)' }),
    el('a', { href: o.primary_url, target: '_blank', rel: 'noopener', text: 'Open report in new tab' })));
}

function renderKept() {
  const list = (S.session || {}).scenarios || [];
  $('kept').replaceChildren(...list.map((k) => el('li', { text: `${k.n}. plan ${k.plan} (${k.label}) — ${k.status} — ${k.storage}` })));
}

/* ── session ────────────────────────────────────────────────────────────── */
async function refresh() {
  S.session = await api('/session');
  store('r51.instance', S.session.server_instance);
  render();
}

function glossary() {
  const items = [
    ['Alighting distribution similarity', 'Similarity (cosine, weighted by boardings) between the estimated and the synthetic ground-truth alighting distribution of each origin stop and hour. Measured only for synthetic data.'],
    ['Modelled daily boardings', 'Daily boardings of the demand estimate multiplied by the demand scale; a scenario, not a count.'],
    ['Input records', 'Actual numbers of fare taps and signalling events used to estimate demand.'],
    ['Maximum operating-cost increase', 'Cap on the relative operating-cost index of a plan, as a share above the current timetable. Total Cost of Ownership (TCO) is the full cost of owning a fleet; the model computes only a relative operating-cost index, so the setting is not a TCO.'],
    ['Maximum headway spread across periods', '(longest − shortest headway) / mean headway over the four periods. It limits variation between periods and is not a measure of spatial equity.'],
    ['Crowding disutility, pax·min/day', 'Extra in-vehicle minutes perceived in crowded vehicles: (λ − 1) × passengers × segment minutes, with λ rising linearly above 80% of full capacity (λ = 2 at full capacity, scenario assumption).'],
    ['Waiting + crowding disutility', 'The variable part of passenger generalised cost used to recommend a plan. It is not a full journey time.'],
    ['Recommended for the selected priorities', 'Lowest waiting + crowding disutility among plans that stay within the limits after rounding to whole trips.'],
    ['Estimated passenger benefit', 'Annual value of the modelled change in waiting and crowding for the stated period and model; in-vehicle time only from an applicable registered simulation.'],
    ['Sensitivity to value of time ±30%', 'The result recomputed with the value of time 30% lower and higher. It is not a confidence interval.'],
    ['Evaluated under the registered simulation protocol', 'All runs of the registered design completed; it says nothing about the direction of the effect.'],
    ['Historical run — validation issues remain', 'Earlier results produced with a different model; shown only for reference.'],
    ['Fleet availability not verified', 'The vehicle count is a normative check, not a schedule of real vehicles and duties.'],
  ];
  $('gloss-list').replaceChildren(...items.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })]));
}

async function boot() {
  try {
    S.config = await api('/config');
  } catch (e) {
    showError(`The server is not reachable: ${e.message}`);
    return;
  }
  const c = S.config;
  $('corridor-line').textContent = `${c.corridor.stops} stops, ${c.corridor.length_km} km`;
  $('import-note').textContent = c.import_note;
  $('example-status').textContent = c.release ? ` Registered release ${c.release.release_id}.`
    : ' Historical run — validation issues remain for earlier published results; plans are recalculated now.';
  $('in-preset').replaceChildren(...c.presets.map((p) => el('option', { value: p.id, text: p.name })));
  $('in-scale').replaceChildren(...c.scales.map((x) => el('option', { value: x, text: `× ${x}`, selected: x === c.default_scale })));
  $('in-budget').replaceChildren(...c.budget_stops.map((b) => el('option', { value: b.idx, text: b.label, selected: b.idx === c.defaults.budget_idx })));
  $('in-delta').replaceChildren(...c.delta_stops.map((d) => el('option', { value: d.idx, text: d.label, selected: d.idx === c.defaults.delta_idx })));
  $('algo-line').textContent = `NSGA-II, population ${120}, protocol seeds ${c.protocol.seeds.join(', ')}; plan chosen by the rule above.`;
  $('crowd-line').textContent = c.protocol.crowding;
  glossary();

  const prevInstance = recall('r51.instance');
  const pending = recall('r51.pending') || {};
  await refresh();
  if (prevInstance && prevInstance !== S.session.server_instance && Object.keys(pending).length) {
    showError('The server was restarted while a calculation was running; that work and the previous results were not kept. Start again.', true);
    store('r51.pending', {});
  }
  // re-attach to jobs still running on this server (after a page reload)
  for (const [kind, j] of Object.entries(S.session.jobs || {})) {
    if (j && j.state === 'running') track(kind, j);
  }
  // restore form from the published session
  const s = S.session;
  if (s.data) {
    document.querySelector(`input[name=entry][value=${s.data.matrix}]`).checked = true;
    $('in-scale').value = s.data.scale;
    if (s.data.matrix === 'generate') { $('in-preset').value = s.data.preset_id; $('in-seed').value = s.data.seed; }
  }
  if (s.front) { $('in-budget').value = s.front.conditions.budget_idx; $('in-delta').value = s.front.conditions.delta_idx; }
  go(1);
}

/* ── wiring ─────────────────────────────────────────────────────────────── */
function wire() {
  $('pareto-plan').addEventListener('change', (event) => { S.inspectedPlan = event.target.value; renderPareto(); });
  $('btn-step1').addEventListener('click', step1Action);
  $('btn-step2').addEventListener('click', () => go(3));
  $('btn-back-1').addEventListener('click', () => go(1));
  $('btn-back-2').addEventListener('click', () => go(2));
  for (const i of [1, 2, 3]) $(`nav-${i}`).addEventListener('click', () => go(i));
  for (const id of ['in-preset', 'in-seed', 'in-scale', 'in-budget', 'in-delta']) $(id).addEventListener('change', render);
  document.querySelectorAll('input[name=entry]').forEach((r) => r.addEventListener('change', render));
  document.querySelectorAll('input[name=ww]').forEach((r) => r.addEventListener('change', (e) => {
    S.ww = Number(e.target.value);
    renderEconomics(); renderExport();
  }));
  $('btn-simulate').addEventListener('click', () => startJob('simulate', '/result/simulate', {}));
  $('btn-holds').addEventListener('click', () => startJob('holds', '/result/holds', {}));
  $('btn-export').addEventListener('click', doExport);
  $('btn-keep').addEventListener('click', keepPlan);
  $('banner-close').addEventListener('click', hideError);
  $('banner-retry').addEventListener('click', () => { const a = S.lastAction; hideError(); if (a) startJob(a.kind, a.path, a.body); });
  $('progress-hide').addEventListener('click', () => { S.hidden = true; showProgress(); $('progress-show').focus(); });
  $('progress-show').addEventListener('click', () => { S.hidden = false; showProgress(); $('progress-hide').focus(); });
  const dlg = $('glossary');
  $('btn-glossary').addEventListener('click', () => dlg.showModal());
  $('gloss-close').addEventListener('click', () => dlg.close());
  dlg.addEventListener('close', () => $('btn-glossary').focus());
}

wire();
boot();
