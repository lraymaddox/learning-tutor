// Paper to Lesson: single-page client. All model-written text is inserted as
// text nodes (never innerHTML), so lesson content cannot inject markup.
const view = document.getElementById('view');
const SVG = 'http://www.w3.org/2000/svg';
let cleanup = null;

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k in el && k !== 'list') el[k] = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}
function s(tag, attrs) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}
async function api(path, opts = {}) {
  if (opts.json) { opts.body = JSON.stringify(opts.json); opts.headers = { 'Content-Type': 'application/json' }; }
  const r = await fetch('/api' + path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed (' + r.status + ')');
  return data;
}
const fmt = (t) => Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0');
const msg = (text, ok) => h('div', { class: 'msg' + (ok ? ' ok' : ''), role: 'status' }, text);

// ------------------------------------------------------------------ home
async function home() {
  const note = h('div');
  const file = h('input', { type: 'file', accept: 'application/pdf' });
  const url = h('input', { type: 'url', placeholder: 'https://arxiv.org/abs/…' });
  const sel = (opts) => h('select', {}, opts.map(([v, t, d]) => h('option', { value: v, selected: d }, t)));
  const audience = sel([['beginner', 'Curious beginner'], ['undergrad', 'Undergraduate in the field', true],
    ['researcher', 'Researcher in another field'], ['expert', 'Expert, just the contributions']]);
  const length = sel([['short', 'Short overview (about 5 min)'], ['standard', 'Standard (about 15 min)', true],
    ['deep', 'Deep dive (about 30 min)']]);
  const board = sel([['chalk', 'Chalkboard', true], ['white', 'Whiteboard']]);
  const review = h('input', { type: 'checkbox', checked: true });
  const go = h('button', { class: 'primary', onclick: async () => {
    const fd = new FormData();
    if (file.files[0]) fd.append('file', file.files[0]);
    fd.append('url', url.value); fd.append('audience', audience.value); fd.append('length', length.value);
    fd.append('board_style', board.value); fd.append('review', review.checked);
    go.disabled = true; note.replaceChildren(msg('Reading the PDF…', true));
    try { const r = await api('/lessons', { method: 'POST', body: fd }); location.hash = '#/lesson/' + r.id; }
    catch (e) { note.replaceChildren(msg(e.message)); go.disabled = false; }
  } }, 'Generate lesson');
  const list = h('div', { class: 'list' });
  view.replaceChildren(h('div', { class: 'narrow' },
    h('h1', {}, 'Turn a research paper into a lesson'),
    h('div', { class: 'card' },
      h('h2', {}, 'The paper'),
      h('div', { class: 'grid' }, h('label', {}, 'Upload a PDF', file), h('label', {}, 'Or paste a link (arXiv or direct PDF)', url)),
      h('h2', {}, 'The lesson'),
      h('div', { class: 'grid' }, h('label', {}, 'Who is it for', audience), h('label', {}, 'Lesson length', length), h('label', {}, 'Board', board)),
      h('label', { class: 'check' }, review, 'Let me review the plan before audio is made'),
      note, h('div', { class: 'row' }, go, h('a', { href: '#/models' }, 'Choose which model runs each step'))),
    h('div', { class: 'card' }, h('h2', {}, 'Your lessons'), list)));
  async function refresh() {
    const rows = await api('/lessons');
    list.replaceChildren(...(rows.length ? rows.map((r) => h('div', { class: 'item' },
      h('a', { class: 'grow', href: '#/lesson/' + r.id }, r.title || 'Untitled'),
      h('span', { class: 'badge ' + r.status }, r.status === 'ready' ? 'Ready' : r.status === 'failed' ? 'Failed' : r.step || 'Queued'),
      h('button', { class: 'quiet', 'aria-label': 'Delete lesson ' + r.title, onclick: async () => {
        if (confirm('Delete "' + r.title + '" and its audio? This cannot be undone.')) { await api('/lessons/' + r.id, { method: 'DELETE' }); refresh(); }
      } }, 'Delete'))) : [h('div', { class: 'muted' }, 'No lessons yet.')]));
  }
  refresh().catch((e) => list.replaceChildren(msg(e.message)));
  const timer = setInterval(() => refresh().catch(() => {}), 4000);
  cleanup = () => clearInterval(timer);
}

// ------------------------------------------------------------------ lesson (status router)
async function lesson(id, sub) {
  let L;
  try { L = await api('/lessons/' + id); } catch (e) { view.replaceChildren(h('div', { class: 'narrow' }, msg(e.message))); return; }
  if (L.status === 'ready') return sub === 'report' ? report(L) : player(L);
  if (L.status === 'awaiting_review') return planReview(L);
  const box = h('div', { class: 'card' });
  view.replaceChildren(h('div', { class: 'narrow' }, h('h1', {}, L.title || 'New lesson'), box));
  if (L.status === 'failed') {
    box.append(msg('Generation stopped: ' + L.error),
      h('div', { class: 'muted' }, 'Finished steps are kept. Retrying resumes from the step that failed.'),
      h('div', { class: 'row' }, h('button', { class: 'primary', onclick: async () => { await api('/lessons/' + id + '/retry', { method: 'POST' }); route(); } }, 'Retry'),
        h('a', { href: '#/models' }, 'Check model settings')));
    return;
  }
  box.append(h('div', { class: 'eyebrow' }, 'Generating'), h('h2', { role: 'status' }, L.step || 'Queued'),
    h('div', { class: 'muted' }, 'You can leave this page. Generation continues on the server and resumes after a restart.'));
  const timer = setTimeout(route, 2500);
  cleanup = () => clearTimeout(timer);
}

function planReview(L) {
  const note = h('div');
  const objs = L.plan.objectives.map((o) => h('input', { type: 'text', value: o, 'aria-label': 'Objective' }));
  const secs = L.plan.sections.map((sec) => ({ sec, title: h('input', { type: 'text', value: sec.title }), goal: h('textarea', { rows: 2, value: sec.goal }) }));
  view.replaceChildren(h('div', { class: 'narrow' },
    h('h1', {}, 'Review the lesson plan'), h('div', { class: 'muted' }, L.title),
    h('div', { class: 'card' }, h('h2', {}, 'The learner will be able to'), objs),
    h('div', { class: 'card' }, h('h2', {}, 'Sections'),
      secs.map((x, i) => h('div', { class: 'grid' }, h('label', {}, 'Section ' + (i + 1) + ' title', x.title), h('label', {}, 'What it should achieve', x.goal)))),
    note,
    h('div', { class: 'row' }, h('button', { class: 'primary', onclick: async (ev) => {
      ev.target.disabled = true;
      try {
        await api('/lessons/' + L.id + '/approve', { method: 'POST', json: {
          objectives: objs.map((o) => o.value.trim()),
          sections: secs.map((x) => ({ ...x.sec, title: x.title.value.trim(), goal: x.goal.value.trim() })) } });
        route();
      } catch (e) { note.replaceChildren(msg(e.message)); ev.target.disabled = false; }
    } }, 'Approve and make the lesson'))));
}

// ------------------------------------------------------------------ board rendering
const INK = { chalk: { ink: '#F4F1E8', hi: '#F6D365', cool: '#9CCBFF' }, white: { ink: '#1B2430', hi: '#B4470F', cool: '#1D4ED8' } };

function drawElement(el, theme) {
  const color = INK[theme][el.color] || INK[theme].ink;
  const stroke = { fill: 'none', stroke: color, 'stroke-width': el.w || 3, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', pathLength: 1, 'stroke-dasharray': 1 };
  if (el.t === 'text') { const t = s('text', { x: el.x, y: el.y, 'font-size': el.size || 24, fill: color }); t.textContent = el.text; return t; }
  if (el.t === 'rect') return s('rect', { x: el.x, y: el.y, width: el.w, height: el.h, rx: 6, ...stroke, 'stroke-width': 3 });
  if (el.t === 'circle') return s('circle', { cx: el.cx, cy: el.cy, r: el.r, ...stroke, 'stroke-width': 3 });
  if (el.t === 'path') return s('path', { d: el.d, ...stroke });
  let d = `M${el.x1} ${el.y1} L${el.x2} ${el.y2}`;
  if (el.t === 'arrow') {
    const a = Math.atan2(el.y2 - el.y1, el.x2 - el.x1), n = 12;
    d += ` M${el.x2 - n * Math.cos(a - 0.45)} ${el.y2 - n * Math.sin(a - 0.45)} L${el.x2} ${el.y2} L${el.x2 - n * Math.cos(a + 0.45)} ${el.y2 - n * Math.sin(a + 0.45)}`;
  }
  return s('path', { d, ...stroke });
}

// Builds the SVG for a list of cues and returns paint(localTime, full): the
// board is a pure function of time, so seeking and playing always agree.
function makeBoard(svg, cues, theme) {
  svg.replaceChildren();
  const items = [];
  cues.forEach((c) => c.elements.forEach((el, k) => {
    const node = drawElement(el, theme);
    svg.append(node);
    items.push({ node, text: el.t === 'text', t: c.t, dur: c.dur, k, n: c.elements.length });
  }));
  return (time, full) => {
    for (const it of items) {
      const p = full ? 1 : Math.max(0, Math.min(1, ((time - it.t) / it.dur) * it.n - it.k));
      if (it.text) it.node.setAttribute('opacity', p);
      else { it.node.setAttribute('stroke-dashoffset', 1 - p); it.node.setAttribute('opacity', p > 0 ? 1 : 0); }
    }
  };
}

// ------------------------------------------------------------------ player
function player(L) {
  const secs = L.sections;
  const offsets = []; let total = 0;
  secs.forEach((x) => { offsets.push(total); total += x.audio.duration; });
  secs.forEach((x) => { let w = 0; x.sentences.forEach((sn) => { sn.start = (x.audio.words[w] || { s: x.audio.duration }).s; w += sn.text.split(/\s+/).length; }); });
  const st = { sec: -1, theme: L.options.board === 'white' ? 'white' : 'chalk', full: false, tab: 'transcript', tutor: null, quizDone: new Set(), answers: {} };
  const audio = new Audio(); audio.preload = 'auto';
  let paint = () => {}, raf = 0, pendingSeek = null;

  const svg = s('svg', { viewBox: '0 0 800 520', role: 'img', 'aria-label': 'Lesson board' });
  const wrap = h('div', { class: 'boardwrap' }); wrap.append(svg);
  const title = h('h1', {});
  const playBtn = h('button', { class: 'play', onclick: () => (audio.paused ? audio.play() : audio.pause()) });
  const clock = h('div', { style: 'font-variant-numeric:tabular-nums;font-size:14px' });
  const scrub = h('input', { type: 'range', min: 0, max: total, step: 0.05, value: 0, 'aria-label': 'Seek narration', oninput: () => seek(Number(scrub.value)) });
  const speed = h('select', { 'aria-label': 'Playback speed', onchange: () => { audio.playbackRate = Number(speed.value); } },
    [0.75, 1, 1.25, 1.5].map((v) => h('option', { value: v, selected: v === 1 }, v + '×')));
  const outline = h('div', {}), pane = h('div', { class: 'pane' }), warn = h('div', {});
  const seg = (label, opts) => h('div', { class: 'seg', role: 'group', 'aria-label': label }, opts.map(([text, on, fn]) => h('button', { 'aria-pressed': String(on()), onclick: (e) => { fn(); [...e.target.parentNode.children].forEach((b, i) => b.setAttribute('aria-pressed', String(opts[i][1]()))); } }, text)));
  const tabs = h('div', { class: 'tabs', role: 'tablist' }, ['transcript', 'quiz', 'ask'].map((t) => h('button', { role: 'tab', 'data-tab': t, onclick: () => setTab(t) }, t === 'ask' ? 'Ask' : t[0].toUpperCase() + t.slice(1))));

  const now = () => (st.sec < 0 ? 0 : offsets[st.sec] + (pendingSeek ?? audio.currentTime));
  function rebuildBoard() {
    wrap.className = 'boardwrap' + (st.theme === 'white' ? ' white' : '');
    paint = st.tutor ? makeBoard(svg, [{ t: 0, dur: 1.5, elements: st.tutor }], st.theme) : makeBoard(svg, secs[st.sec].cues, st.theme);
    st.tutorAt = performance.now();
  }
  function loadSection(i, at, autoplay) {
    st.sec = i; st.tutor = null; pendingSeek = at;
    audio.src = `/api/lessons/${L.id}/audio/${secs[i].audio.file}`;
    audio.onloadedmetadata = () => { audio.currentTime = at; pendingSeek = null; audio.playbackRate = Number(speed.value); if (autoplay) audio.play(); };
    title.textContent = (i + 1) + '. ' + secs[i].title;
    rebuildBoard(); renderOutline(); renderPane(); renderWarn();
  }
  function seek(t) {
    t = Math.max(0, Math.min(total - 0.05, t));
    let i = secs.length - 1; while (i > 0 && offsets[i] > t) i--;
    if (i !== st.sec) loadSection(i, t - offsets[i], !audio.paused);
    else { audio.currentTime = t - offsets[i]; if (st.tutor) { st.tutor = null; rebuildBoard(); } }
    frame(true);
  }
  audio.onended = () => {
    const due = L.quiz.some((q) => q.after_section === st.sec) && !st.quizDone.has(st.sec);
    if (due) { st.quizDone.add(st.sec); st.quizFor = st.sec; setTab('quiz'); return; }
    if (st.sec + 1 < secs.length) loadSection(st.sec + 1, 0, true);
  };
  audio.onplay = audio.onpause = () => {
    playBtn.setAttribute('aria-label', audio.paused ? 'Play narration' : 'Pause narration');
    playBtn.textContent = audio.paused ? '▶' : '❚❚';
  };
  audio.onerror = () => warn.replaceChildren(msg('The audio for this section could not be loaded.'));

  let lastLine = -1;
  function frame(force) {
    const local = pendingSeek ?? audio.currentTime;
    if (st.tutor) paint((performance.now() - st.tutorAt) / 1000, false); else paint(local, st.full);
    const t = now();
    if (document.activeElement !== scrub) scrub.value = t;
    clock.textContent = fmt(t) + ' / ' + fmt(total);
    if (st.tab === 'transcript') {
      const sn = secs[st.sec].sentences; let cur = 0;
      for (let i = 0; i < sn.length; i++) if (sn[i].start <= local + 0.01) cur = i;
      if (cur !== lastLine || force) {
        lastLine = cur;
        [...pane.querySelectorAll('.line')].forEach((b, i) => { b.className = 'line' + (i === cur ? ' now' : i < cur ? ' past' : ''); });
      }
    }
  }
  function loop() { frame(false); raf = requestAnimationFrame(loop); }

  function renderOutline() {
    outline.replaceChildren(...secs.map((x, i) => h('button', { class: 'navbtn' + (i === st.sec ? ' on' : ''), onclick: () => seek(offsets[i]) },
      h('span', { class: 'n' }, String(i + 1)), h('span', { class: 'grow' }, x.title), h('span', { class: 't' }, fmt(offsets[i])))));
  }
  function renderWarn() {
    const fl = L.flags.filter((f) => f.section === st.sec || f.section == null);
    warn.replaceChildren(...(fl.length ? [h('details', { class: 'msg' }, h('summary', {}, fl.length + (fl.length === 1 ? ' warning' : ' warnings') + ' on this lesson'),
      h('ul', {}, fl.map((f) => h('li', {}, f.text))))] : []));
  }
  function setTab(t) { st.tab = t; renderPane(); }
  function renderPane() {
    [...tabs.children].forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === st.tab)));
    lastLine = -1;
    if (st.tab === 'transcript') {
      pane.replaceChildren(...secs[st.sec].sentences.map((sn) => h('button', { class: 'line', onclick: () => seek(offsets[st.sec] + sn.start) },
        h('span', { class: 't' }, fmt(offsets[st.sec] + sn.start)), h('span', {}, sn.text))));
    } else if (st.tab === 'quiz') {
      const of = st.quizFor ?? st.sec;
      const qs = L.quiz.map((q, i) => ({ q, i })).filter((x) => x.q.after_section === of);
      pane.replaceChildren(h('div', { class: 'eyebrow' }, 'Check-in after section ' + (of + 1)),
        ...(qs.length ? qs.map(({ q, i }) => {
          const picked = st.answers[i];
          return h('div', { style: 'display:flex;flex-direction:column;gap:8px' }, h('strong', {}, q.question),
            q.flagged ? msg('This question could not be verified against the paper.') : null,
            q.options.map((o, k) => h('button', { class: 'opt' + (picked == null ? '' : k === q.correct ? ' right' : k === picked ? ' wrong' : ''),
              onclick: () => { st.answers[i] = k; renderPane(); } }, o)),
            picked != null ? h('div', { class: 'bubble' }, (picked === q.correct ? 'Correct. ' : 'Not quite. ') + q.explain) : null);
        }) : [h('div', { class: 'muted' }, 'No questions for this section.')]),
        st.sec + 1 < secs.length && audio.ended ? h('button', { class: 'primary', onclick: () => { st.quizFor = null; st.tab = 'transcript'; loadSection(st.sec + 1, 0, true); } }, 'Continue the lesson') : null);
    } else {
      const thread = h('div', { style: 'display:flex;flex-direction:column;gap:10px' });
      const q = h('textarea', { rows: 3, placeholder: 'Pauses the lesson while you ask' });
      const draw = h('input', { type: 'checkbox', checked: true });
      const send = h('button', { class: 'primary', onclick: async () => {
        const text = q.value.trim(); if (!text) return;
        audio.pause(); send.disabled = true; thread.append(h('div', { class: 'bubble me' }, text)); q.value = '';
        const wait = h('div', { class: 'muted', role: 'status' }, 'Reading the paper…'); thread.append(wait);
        try {
          const a = await api('/lessons/' + L.id + '/ask', { method: 'POST', json: { question: text, draw: draw.checked } });
          wait.replaceWith(h('div', { class: 'bubble' }, a.answer,
            a.quote ? h('blockquote', { class: 'quote' }, (a.grounded ? 'From the paper: ' : 'Quote not found in the paper, treat with care: ') + '“' + a.quote + '”') : h('div', { class: 'quote' }, 'No supporting passage was cited.'),
            a.elements.length ? h('button', { onclick: () => { st.tutor = a.elements; rebuildBoard(); } }, 'Show drawing on the board') : null));
          if (a.elements.length) { st.tutor = a.elements; rebuildBoard(); }
        } catch (e) { wait.replaceWith(msg(e.message)); }
        send.disabled = false;
      } }, 'Ask');
      pane.replaceChildren(thread, h('label', {}, 'Ask about this paper', q), h('label', { class: 'check' }, draw, 'Draw the answer on the board'),
        h('div', { class: 'row' }, send, h('button', { onclick: () => { st.tutor = null; rebuildBoard(); } }, 'Back to lesson board')));
    }
  }

  view.replaceChildren(h('div', { class: 'player' },
    h('aside', { class: 'card' }, h('h2', {}, 'Lesson plan'), h('div', { class: 'muted' }, L.title),
      h('div', { class: 'eyebrow' }, 'You will be able to'), h('ul', { style: 'margin:0;padding-left:18px;font-size:14px' }, L.plan.objectives.map((o) => h('li', {}, o))),
      h('div', { class: 'eyebrow' }, 'Sections'), outline, h('a', { href: '#/lesson/' + L.id + '/report' }, 'Eval report')),
    h('section', {},
      h('div', { class: 'row', style: 'justify-content:space-between' }, title, h('div', { class: 'row' },
        seg('Board surface', [['Chalkboard', () => st.theme === 'chalk', () => { st.theme = 'chalk'; rebuildBoard(); }], ['Whiteboard', () => st.theme === 'white', () => { st.theme = 'white'; rebuildBoard(); }]]),
        seg('Board mode', [['Draw along', () => !st.full, () => { st.full = false; }], ['Full board', () => st.full, () => { st.full = true; }]]))),
      wrap, h('div', { class: 'bar' }, playBtn, clock, scrub, speed), warn),
    h('aside', { class: 'card side' }, tabs, pane)));
  loadSection(0, 0, false); audio.onpause(); loop();
  cleanup = () => { cancelAnimationFrame(raf); audio.pause(); audio.src = ''; };
}

// ------------------------------------------------------------------ report
async function report(L) {
  const R = await api('/lessons/' + L.id + '/report');
  view.replaceChildren(h('div', { class: 'narrow' },
    h('div', { class: 'row' }, h('h1', { class: 'grow' }, 'Eval report'), h('a', { href: '#/lesson/' + L.id }, 'Back to the lesson')),
    h('div', { class: 'muted' }, L.title),
    h('div', { class: 'grid' }, R.gates.map((g) => h('div', { class: 'card' }, h('div', { class: g.passed ? 'pass' : 'fail' }, g.passed ? 'Pass' : 'Fail'), h('h2', {}, g.title),
      h('div', { class: 'muted' }, g.checks.filter((c) => c.passed).length + ' of ' + g.checks.length + ' checks passed')))),
    R.gates.map((g) => h('div', { class: 'card' }, h('h2', {}, g.title), h('div', { class: 'scroll' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Result', 'Check', 'Section', 'Evidence'].map((t) => h('th', {}, t)))),
      h('tbody', {}, g.checks.map((c) => h('tr', {}, h('td', { class: c.passed ? 'pass' : 'fail' }, c.score != null ? c.score + '/5' : c.passed ? 'Pass' : 'Fail'),
        h('td', {}, c.name), h('td', {}, c.section == null ? 'All' : String(c.section + 1)), h('td', {}, c.evidence)))))))),
    h('div', { class: 'card' }, h('h2', {}, 'Reported, not gated'),
      h('div', { class: 'muted' }, 'Redo rounds used: ' + Object.entries(R.redos).map(([k, v]) => k + ' ' + v).join(', ') + '. Cost is shown only where the provider returns it.'),
      h('div', { class: 'scroll' }, h('table', {},
        h('thead', {}, h('tr', {}, ['Component', 'Provider', 'Model', 'Calls', 'Tokens in', 'Tokens out', 'Characters', 'Seconds', 'Cost'].map((t) => h('th', {}, t)))),
        h('tbody', {}, R.usage.map((u) => h('tr', {}, [u.component, u.provider, u.model, u.calls, u.in_tokens, u.out_tokens, u.chars || '', u.seconds.toFixed(1), u.cost_usd == null ? 'not reported' : '$' + u.cost_usd.toFixed(4)].map((v) => h('td', {}, String(v)))))))))));
}

// ------------------------------------------------------------------ models
async function models() {
  const S = await api('/settings');
  const note = h('div');
  const rows = Object.entries(S.labels).map(([id, label]) => {
    const voice = id === 'voice', cfg = S.components[id] || {};
    const list = h('datalist', { id: 'dl-' + id });
    const prov = h('select', {}, h('option', { value: '' }, 'Choose…'), (voice ? S.voice_providers : S.text_providers).map((p) => h('option', { value: p, selected: cfg.provider === p }, p === 'demo' ? 'Demo (no key)' : p)));
    const model = h('input', { type: 'text', value: cfg.model || '', placeholder: voice ? 'Voice id' : 'Model id' });
    model.setAttribute('list', 'dl-' + id);
    const hint = h('div', { class: 'muted' });
    const load = async () => {
      list.replaceChildren(); hint.textContent = '';
      if (!prov.value) return;
      if (prov.value === 'demo') { model.value = 'demo'; return; }
      try {
        const ms = await api('/models/' + prov.value + '?kind=' + (voice ? 'voice' : 'text'));
        list.replaceChildren(...ms.map((m) => h('option', { value: m.id }, m.name)));
        hint.textContent = ms.length + ' available. Start typing to pick one.';
      } catch (e) { hint.textContent = e.message; }
    };
    prov.onchange = () => { model.value = ''; load(); };
    load();
    return { id, prov, model, el: h('div', { class: 'comp' }, h('div', {}, h('strong', {}, label), hint), h('label', {}, 'Provider', prov), h('label', {}, voice ? 'Voice' : 'Model', model, list)) };
  });
  const keys = Object.entries(S.keys).map(([p, state]) => ({ p, input: h('input', { type: 'password', autocomplete: 'off', placeholder: state === 'env' ? 'Set by environment variable' : state === 'saved' ? 'Saved. Paste to replace' : 'Paste key', disabled: state === 'env' }) }));
  const save = async () => {
    const components = {}; rows.forEach((r) => { if (r.prov.value) components[r.id] = { provider: r.prov.value, model: r.model.value }; });
    const ks = {}; keys.forEach((k) => { if (k.input.value.trim()) ks[k.p] = k.input.value; });
    try { await api('/settings', { method: 'PUT', json: { components, keys: ks } }); route(); }
    catch (e) { note.replaceChildren(msg(e.message)); }
  };
  view.replaceChildren(h('div', { class: 'narrow' }, h('h1', {}, 'Models'),
    h('div', { class: 'muted' }, 'Pick a provider and model for each part of the lesson. Parts are independent. Save keys first so model lists can load.'),
    h('div', { class: 'card' }, h('h2', {}, 'API keys'), h('div', { class: 'muted' }, 'Keys are stored in the app database on the server and are never sent back to the browser.'),
      h('div', { class: 'grid' }, keys.map((k) => h('label', {}, k.p, k.input)))),
    h('div', { class: 'card' }, h('h2', {}, 'Components'), rows.map((r) => r.el),
      h('div', { class: 'muted' }, 'Voices that return no word timestamps still work; timing is then estimated and the lesson is flagged.')),
    note,
    h('div', { class: 'row' }, h('button', { class: 'primary', onclick: save }, 'Save'),
      h('button', { onclick: () => { rows.forEach((r) => { r.prov.value = 'demo'; r.model.value = 'demo'; }); save(); } }, 'Use the demo provider for everything'),
      h('span', { class: 'muted' }, 'The demo provider needs no keys and produces placeholder lessons with silent audio.'))));
}

// ------------------------------------------------------------------ router
function route() {
  if (cleanup) { cleanup(); cleanup = null; }
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('on', a.dataset.nav === (parts[0] === 'models' ? 'models' : 'home')));
  const run = parts[0] === 'lesson' && parts[1] ? lesson(parts[1], parts[2]) : parts[0] === 'models' ? models() : home();
  Promise.resolve(run).catch((e) => view.replaceChildren(h('div', { class: 'narrow' }, msg(e.message))));
}
window.addEventListener('hashchange', route);
route();
