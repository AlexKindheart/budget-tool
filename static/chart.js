// Overview "Balances" chart: end-of-day balance per account as lines (inline SVG, no libraries).
// "All accounts" = one combined line (net worth). Ticking accounts compares them, one line each,
// over the same week / month / year / all time. Each account keeps its own color whatever else is ticked.
// One line: big balance up top with the change over the range; hovering scrubs that number.
// Several lines: names and balances at the line ends, plus a tooltip on hover.
(function () {
  const root = document.querySelector('[data-trend]');
  if (!root) return;
  const data = JSON.parse(root.querySelector('.trend-data').textContent);  // {dates, accounts: [{id, name, slot, values}], all}
  const box = root.querySelector('.chart');
  const tip = root.querySelector('.tip');
  const thead = root.querySelector('.tableview thead');
  const tbody = root.querySelector('.tableview tbody');
  const subtitle = root.querySelector('[data-subtitle]');
  const hero = root.querySelector('[data-hero]');
  const heroVal = root.querySelector('[data-hero-value]');
  const heroDelta = root.querySelector('[data-hero-delta]');
  const heroWhen = root.querySelector('[data-hero-when]');
  const boxes = [...root.querySelectorAll('.acct-picks input')];
  const allBox = boxes.find((b) => b.value === 'all');
  const NS = 'http://www.w3.org/2000/svg';
  const byId = Object.fromEntries(data.accounts.map((a) => [String(a.id), a]));
  const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const RANGES = { week: 'past week', month: 'past month', year: 'past year', all: 'all time' };

  const load = (k, fallback) => { try { return localStorage.getItem(k) || fallback; } catch (e) { return fallback; } };
  const save = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } };
  let range = load('balanceRange', 'year');
  if (!(range in RANGES)) range = 'year';
  let picked = load('balanceAccounts', 'all').split(',').filter((id) => id === 'all' || byId[id]);
  if (!picked.length || picked.includes('all')) picked = ['all'];
  let animate = true;  // draw the lines in after a change of accounts or range, not on window resize

  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (v) => (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const compact = (v) => {
    const a = Math.abs(v), s = v < 0 ? '-' : '';
    if (a >= 1e6) return s + '$' + +(a / 1e6).toFixed(1) + 'M';
    if (a >= 1e4) return s + '$' + +(a / 1e3).toFixed(0) + 'K';
    if (a >= 1e3) return s + '$' + +(a / 1e3).toFixed(1) + 'K';
    return s + '$' + Math.round(a).toLocaleString();
  };
  const parse = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
  const isoOf = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const fmtDay = (iso) => parse(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  const fmtMonth = (iso, withYear) => parse(iso).toLocaleDateString(undefined, withYear ? { month: 'short', year: 'numeric' } : { month: 'short' });
  const f = (v) => v.toFixed(1);
  const el = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };
  const niceStep = (span) => {  // 1, 2, 2.5 or 5 x 10^n, about 4 intervals
    const raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    return [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw) || mag * 10;
  };
  const firstValue = (values) => values.find((v) => v != null);

  // Index of the first day in the chosen range (rolling: the last 7 days, since this day last month / last year).
  function rangeStart() {
    if (range === 'all') {
      const i = data.all.findIndex((v) => v != null);   // from the first transaction, not empty days
      return Math.max(0, i);
    }
    const t = parse(data.dates[data.dates.length - 1]);
    let s;
    if (range === 'week') {
      s = new Date(t.getFullYear(), t.getMonth(), t.getDate() - 6);
    } else {
      const back = range === 'month' ? 1 : 12;
      const first = new Date(t.getFullYear(), t.getMonth() - back, 1);
      const days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
      s = new Date(first.getFullYear(), first.getMonth(), Math.min(t.getDate(), days));
    }
    const iso = isoOf(s);
    return Math.max(0, data.dates.findIndex((d) => d >= iso));
  }

  // Smooth curve through the points that never overshoots them (monotone cubic), so a curve
  // never shows a balance the account didn't actually have.
  function curve(pts) {
    const n = pts.length;
    if (n < 3) return pts.map((p, i) => (i ? 'L' : 'M') + f(p[0]) + ',' + f(p[1])).join('');
    const dx = [], m = [], t = [];
    for (let i = 0; i < n - 1; i++) {
      dx[i] = pts[i + 1][0] - pts[i][0];
      m[i] = dx[i] ? (pts[i + 1][1] - pts[i][1]) / dx[i] : 0;
    }
    t[0] = m[0]; t[n - 1] = m[n - 2];
    for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
    for (let i = 0; i < n - 1; i++) {
      if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
      const a = t[i] / m[i], b = t[i + 1] / m[i], h = a * a + b * b;
      if (h > 9) { const s = 3 / Math.sqrt(h); t[i] = s * a * m[i]; t[i + 1] = s * b * m[i]; }
    }
    let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
    for (let i = 0; i < n - 1; i++) {
      const h = dx[i] / 3;
      d += `C${f(pts[i][0] + h)},${f(pts[i][1] + t[i] * h)} ${f(pts[i + 1][0] - h)},${f(pts[i + 1][1] - t[i + 1] * h)} ${f(pts[i + 1][0])},${f(pts[i + 1][1])}`;
    }
    return d;
  }

  // Date labels along the bottom: days for a week, weekly for a month, months (or years) beyond that.
  function dateMarks(dates, maxLabels) {
    const n = dates.length, marks = [];
    if (n <= 62) {
      const step = n <= 8 ? 1 : 7;
      for (let i = n - 1; i >= 0; i -= step) {
        marks.unshift([i, parse(dates[i]).toLocaleDateString(undefined,
          step === 1 ? { weekday: 'short', day: 'numeric' } : { month: 'short', day: 'numeric' })]);
      }
      const k = Math.ceil(marks.length / maxLabels);
      return marks.filter((_, j) => (marks.length - 1 - j) % k === 0);
    }
    const starts = [];
    dates.forEach((d, i) => { if (d.endsWith('-01')) starts.push(i); });
    const step = [1, 2, 3, 4, 6, 12].find((s) => Math.ceil(starts.length / s) <= maxLabels) || 12 * Math.ceil(starts.length / 12 / maxLabels);
    starts.forEach((i) => {
      const [y, m] = dates[i].split('-').map(Number);
      if ((y * 12 + m - 1) % step !== 0) return;
      marks.push([i, step >= 12 ? String(y) : fmtMonth(dates[i], m === 1 || i === starts[0])]);
    });
    return marks;
  }

  function syncBoxes() {
    boxes.forEach((b) => { b.checked = picked.includes(b.value); });
    save('balanceAccounts', picked.join(','));
  }
  boxes.forEach((b) => b.addEventListener('change', () => {
    if (b === allBox) {
      picked = ['all'];                                   // "All accounts" clears the individual picks
    } else {
      picked = picked.filter((id) => id !== 'all');
      picked = b.checked ? [...picked, b.value] : picked.filter((id) => id !== b.value);
      if (!picked.length) picked = ['all'];               // nothing ticked -> back to All accounts
    }
    syncBoxes();
    animate = true;
    render();
  }));

  function currentSeries(start) {
    if (picked.includes('all')) {
      return [{ name: 'All accounts', cls: 'sa', color: 'var(--series-1)', values: data.all.slice(start) }];
    }
    // Keep the accounts' own order (and colors), not the order they were ticked in.
    return data.accounts.filter((a) => picked.includes(String(a.id))).map((a) => ({
      name: a.name, cls: 's' + ((a.slot % 8) + 1), color: `var(--series-${(a.slot % 8) + 1})`,
      dashed: a.slot >= 8, values: a.values.slice(start),
    }));
  }

  // Table view: every day for a week or month, month-end balances for longer ranges.
  function renderTable(dates, series) {
    const daily = dates.length <= 62;
    thead.innerHTML = `<tr><th>${daily ? 'Day' : 'Month end'}</th>` + series.map((s) => `<th class="num">${esc(s.name)}</th>`).join('') + '</tr>';
    tbody.innerHTML = '';
    for (let i = dates.length - 1; i >= 0; i--) {
      const isLast = i === dates.length - 1;
      if (!daily && !isLast && !dates[i + 1].endsWith('-01')) continue;
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${isLast ? fmtDay(dates[i]) + ' (today)' : daily ? fmtDay(dates[i]) : fmtMonth(dates[i], true)}</td>` +
        series.map((s) => `<td class="num${s.values[i] < 0 ? ' neg' : ''}">${s.values[i] == null ? '-' : money(s.values[i])}</td>`).join('');
      tbody.appendChild(tr);
    }
  }

  // The big number: balance on day i, and the change since the start of the range (arrow + sign, not color alone).
  function setHero(s, dates, i, hovering) {
    const v = s.values[i], from = firstValue(s.values);
    if (v == null) {
      heroVal.textContent = '—';
      heroDelta.textContent = '';
      heroDelta.className = 'delta';
      heroWhen.textContent = hovering ? fmtDay(dates[i]) + ' · no history yet' : '';
      return;
    }
    heroVal.textContent = money(v);
    const diff = from == null ? 0 : v - from;
    const pct = from ? (diff / Math.abs(from)) * 100 : null;
    const dir = diff > 0.005 ? 'up' : diff < -0.005 ? 'down' : 'flat';
    heroDelta.className = 'delta ' + dir;
    heroDelta.textContent = (dir === 'up' ? '▲ +' : dir === 'down' ? '▼ -' : '± ') + money(Math.abs(diff)) +
      (pct != null && dir !== 'flat' ? ` (${Math.abs(pct) >= 100 ? Math.round(Math.abs(pct)) : Math.abs(pct).toFixed(1)}%)` : '');
    heroWhen.textContent = hovering ? fmtDay(dates[i]) : RANGES[range];
  }

  function render() {
    const start = rangeStart();
    const dates = data.dates.slice(start);
    const series = currentSeries(start);
    const n = dates.length;
    const single = series.length === 1;
    const doAnimate = animate && !calm;
    animate = false;
    renderTable(dates, series);

    hero.hidden = !single;
    if (single) setHero(series[0], dates, n - 1, false);
    subtitle.textContent = single ? '· ' + series[0].name : `· comparing ${series.length} accounts`;

    // Several lines: name + balance at each line's end, two lines of text each.
    const ends = [];
    if (!single && series.length <= 4) {
      series.forEach((s) => {
        const last = s.values[n - 1];
        if (last != null) ends.push({ s, v: last, name: s.name.length > 18 ? s.name.slice(0, 17) + '…' : s.name });
      });
    }
    const W = Math.max(box.clientWidth, 280), H = 280;
    const labelW = ends.length ? Math.min(150, Math.max(...ends.map((e) => Math.max(e.name.length, money(e.v).length))) * 6.6 + 18) : 16;
    const pad = { l: 54, r: labelW, t: 18, b: 30 };
    const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b, bottom = pad.t + plotH;
    const vals = series.flatMap((s) => s.values).filter((v) => v != null);
    let lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 100;
    if (lo > 0 && lo < (hi - lo) * 1.5) lo = 0;   // keep zero in view unless the line sits far above it
    if (hi < 0) hi = 0;
    if (hi === lo) { hi += 50; lo -= 50; }
    const step = niceStep(hi - lo);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    const x = (i) => pad.l + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const y = (v) => pad.t + plotH - ((v - lo) / (hi - lo)) * plotH;

    box.innerHTML = '';
    const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, box);
    const defs = el('defs', {}, svg);
    for (let v = lo; v <= hi + 1e-9; v += step) {
      el('line', { x1: pad.l, x2: pad.l + plotW, y1: y(v), y2: y(v), class: Math.abs(v) < 1e-9 ? 'baseline' : 'grid' }, svg);
      el('text', { x: pad.l - 10, y: y(v) + 4, class: 'tick', 'text-anchor': 'end' }, svg).textContent = compact(v);
    }
    dateMarks(dates, Math.max(2, Math.floor(plotW / 72))).forEach(([i, label]) => {
      el('text', { x: x(i), y: H - 8, class: 'tick', 'text-anchor': 'middle' }, svg).textContent = label;
    });

    // Each line's unbroken stretches (an account has no line before its first transaction).
    const runs = series.map((s) => {
      const out = [];
      let cur = null;
      s.values.forEach((v, i) => {
        if (v == null) { cur = null; return; }
        if (!cur) out.push(cur = []);
        cur.push([x(i), y(v)]);
      });
      return out;
    });

    // One line: soft gradient fill underneath it.
    if (single) {
      const g = el('linearGradient', { id: 'balfill', x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      el('stop', { offset: '0%', style: `stop-color:${series[0].color};stop-opacity:.30` }, g);
      el('stop', { offset: '100%', style: `stop-color:${series[0].color};stop-opacity:0` }, g);
      runs[0].forEach((pts) => {
        if (pts.length < 2) return;
        const a = el('path', { d: curve(pts) + `L${f(pts[pts.length - 1][0])},${f(bottom)}L${f(pts[0][0])},${f(bottom)}Z`,
          class: 'area', fill: 'url(#balfill)' }, svg);
        if (doAnimate) a.classList.add('fade-in');
      });
    }

    const drawn = [];
    series.forEach((s, si) => {
      runs[si].forEach((pts) => {
        const p = el('path', { d: curve(pts), class: 'line ' + s.cls + (s.dashed ? ' dashed' : '') }, svg);
        if (doAnimate && !s.dashed) drawn.push(p);
      });
    });
    // Draw the lines in from the left.
    if (drawn.length) {
      drawn.forEach((p) => {
        const len = p.getTotalLength();
        p.style.strokeDasharray = `${len} ${len}`;
        p.style.strokeDashoffset = len;
      });
      svg.getBoundingClientRect();
      drawn.forEach((p) => {
        p.style.transition = 'stroke-dashoffset 900ms cubic-bezier(.3,.7,.2,1)';
        p.style.strokeDashoffset = 0;
        p.addEventListener('transitionend', () => { p.style.strokeDasharray = ''; p.style.transition = ''; }, { once: true });
      });
    }

    // Today's balance: dot with a soft pulsing halo.
    const endDots = el('g', { class: doAnimate ? 'ends fade-late' : 'ends' }, svg);
    series.forEach((s) => {
      const last = s.values[n - 1];
      if (last == null) return;
      el('circle', { cx: x(n - 1), cy: y(last), r: 4, class: 'halo ' + s.cls + (calm ? '' : ' pulse') }, endDots);
      el('circle', { cx: x(n - 1), cy: y(last), r: 4, class: 'dot ' + s.cls }, endDots);
    });

    // End labels (name + balance) for up to 4 lines, nudged apart when lines end close together;
    // with more lines the checkboxes above act as the legend.
    if (ends.length) {
      const GAP = 30;
      ends.forEach((e) => { e.y = y(e.v); e.ly = e.y; });
      ends.sort((a, b) => a.y - b.y);
      for (let i = 1; i < ends.length; i++) ends[i].ly = Math.max(ends[i].ly, ends[i - 1].ly + GAP);
      const over = ends[ends.length - 1].ly - (bottom - 10);
      if (over > 0) ends.forEach((e) => { e.ly -= over; });
      for (let i = ends.length - 2; i >= 0; i--) ends[i].ly = Math.min(ends[i].ly, ends[i + 1].ly - GAP);
      ends.forEach((e) => {
        if (Math.abs(e.ly - e.y) > 3) {  // short leader from the dot to a nudged label
          el('path', { d: `M${f(x(n - 1) + 6)},${f(e.y)}L${f(x(n - 1) + 9)},${f(e.ly)}`, class: 'leader' }, endDots);
        }
        const t = el('text', { x: x(n - 1) + 12, y: e.ly - 2, class: 'endlabel' }, endDots);
        el('tspan', {}, t).textContent = e.name;
        el('tspan', { x: x(n - 1) + 12, dy: 14, class: 'endval' }, t).textContent = money(e.v);
      });
    }

    // Hover / keyboard layer.
    const cross = el('line', { y1: pad.t, y2: bottom, class: 'cross', visibility: 'hidden' }, svg);
    const dateTag = el('text', { y: pad.t - 6, class: 'datetag', 'text-anchor': 'middle', visibility: 'hidden' }, svg);
    const hoverDots = series.map((s) => {
      const g = el('g', { visibility: 'hidden' }, svg);
      el('circle', { r: 10, class: 'ring ' + s.cls }, g);
      el('circle', { r: 5, class: 'dot ' + s.cls }, g);
      return g;
    });
    const hit = el('rect', { x: pad.l - 6, y: pad.t, width: plotW + 12, height: plotH, class: 'hit', tabindex: 0,
      'aria-label': 'Balances over time. Use left and right arrows to move by a day, Page Up and Page Down by a month.' }, svg);
    let idx = -1;
    const show = (i) => {
      idx = Math.max(0, Math.min(n - 1, i));
      const cx = x(idx);
      cross.setAttribute('x1', cx); cross.setAttribute('x2', cx); cross.setAttribute('visibility', 'visible');
      const rows = [];
      series.forEach((s, si) => {
        const v = s.values[idx];
        if (v == null) { hoverDots[si].setAttribute('visibility', 'hidden'); return; }
        hoverDots[si].setAttribute('transform', `translate(${f(cx)},${f(y(v))})`);
        hoverDots[si].setAttribute('visibility', 'visible');
        rows.push({ s, v });
      });
      endDots.classList.add('dim');
      if (single) {
        // Scrub the big number instead of covering the line with a tooltip.
        setHero(series[0], dates, idx, true);
        dateTag.textContent = fmtDay(dates[idx]);
        dateTag.setAttribute('x', Math.max(pad.l + 44, Math.min(pad.l + plotW - 44, cx)));
        dateTag.setAttribute('visibility', 'visible');
      } else {
        rows.sort((a, b) => b.v - a.v);
        tip.innerHTML = `<strong>${fmtDay(dates[idx])}</strong>` + (rows.length
          ? rows.map((r) => {
            const from = firstValue(r.s.values), diff = from == null ? 0 : r.v - from;
            const dir = diff > 0.005 ? 'up' : diff < -0.005 ? 'down' : '';
            return `<div><i class="key ${r.s.cls}"></i>${esc(r.s.name)} <span>${money(r.v)}` +
              `<em class="${dir}">${dir === 'up' ? '▲' : dir === 'down' ? '▼' : '±'} ${compact(Math.abs(diff))}</em></span></div>`;
          }).join('')
          : '<div class="muted">No history yet</div>');
        tip.hidden = false;
        const right = cx + 16 + tip.offsetWidth <= W;
        tip.style.left = (box.offsetLeft + (right ? cx + 16 : cx - 16 - tip.offsetWidth)) + 'px';
        tip.style.top = (box.offsetTop + pad.t) + 'px';
      }
      hit.setAttribute('aria-label', `${fmtDay(dates[idx])}: ` + (rows.length ? rows.map((r) => `${r.s.name} ${money(r.v)}`).join(', ') : 'no history yet'));
    };
    const hide = () => {
      idx = -1;
      cross.setAttribute('visibility', 'hidden');
      dateTag.setAttribute('visibility', 'hidden');
      hoverDots.forEach((c) => c.setAttribute('visibility', 'hidden'));
      endDots.classList.remove('dim');
      tip.hidden = true;
      if (single) setHero(series[0], dates, n - 1, false);
    };
    hit.addEventListener('mousemove', (e) => {
      const r = svg.getBoundingClientRect();
      const px = (e.clientX - r.left) * (W / r.width);
      show(n <= 1 ? 0 : Math.round(((px - pad.l) / plotW) * (n - 1)));
    });
    hit.addEventListener('mouseleave', hide);
    hit.addEventListener('focus', () => show(n - 1));
    hit.addEventListener('blur', hide);
    hit.addEventListener('keydown', (e) => {
      const moves = { ArrowLeft: -1, ArrowRight: 1, PageUp: -30, PageDown: 30, Home: -n, End: n };
      if (e.key in moves) { e.preventDefault(); show((idx < 0 ? n - 1 : idx) + moves[e.key]); }
    });

    root.querySelectorAll('[data-range]').forEach((b) => b.classList.toggle('on', b.dataset.range === range));
  }

  root.querySelectorAll('[data-range]').forEach((b) => b.addEventListener('click', () => {
    range = b.dataset.range;
    save('balanceRange', range);
    animate = true;
    render();
  }));
  let t;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(render, 100); });
  syncBoxes();
  render();
})();
