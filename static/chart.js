// Overview "Balances" chart: end-of-day balance per account as lines (inline SVG, no libraries).
// "All accounts" = one combined line (net worth). Ticking accounts compares them, one line each,
// over the same 6 / 12 / 24 months. Each account keeps its own color whatever else is ticked.
(function () {
  const root = document.querySelector('[data-trend]');
  if (!root) return;
  const data = JSON.parse(root.querySelector('.trend-data').textContent);  // {dates, accounts: [{id, name, slot, values}], all}
  const box = root.querySelector('.chart');
  const tip = root.querySelector('.tip');
  const thead = root.querySelector('.tableview thead');
  const tbody = root.querySelector('.tableview tbody');
  const subtitle = root.querySelector('[data-subtitle]');
  const boxes = [...root.querySelectorAll('.acct-picks input')];
  const allBox = boxes.find((b) => b.value === 'all');
  const NS = 'http://www.w3.org/2000/svg';
  const byId = Object.fromEntries(data.accounts.map((a) => [String(a.id), a]));

  const load = (k, fallback) => { try { return localStorage.getItem(k) || fallback; } catch (e) { return fallback; } };
  const save = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } };
  let range = parseInt(load('balanceRange', '12'), 10) || 12;
  let picked = load('balanceAccounts', 'all').split(',').filter((id) => id === 'all' || byId[id]);
  if (!picked.length || picked.includes('all')) picked = ['all'];

  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (v) => (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const compact = (v) => {
    const a = Math.abs(v), s = v < 0 ? '-' : '';
    if (a >= 1e6) return s + '$' + +(a / 1e6).toFixed(1) + 'M';
    if (a >= 1e4) return s + '$' + +(a / 1e3).toFixed(0) + 'K';
    return s + '$' + a.toLocaleString();
  };
  const parse = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
  const fmtDay = (iso) => parse(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  const fmtMonth = (iso, withYear) => parse(iso).toLocaleDateString(undefined, withYear ? { month: 'short', year: 'numeric' } : { month: 'short' });
  const el = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };
  const niceStep = (span) => {  // 1, 2, 2.5 or 5 x 10^n, about 4 intervals
    const raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    return [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw) || mag * 10;
  };

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
    render();
  }));

  function currentSeries(start) {
    if (picked.includes('all')) {
      return [{ name: 'All accounts', cls: 'sa', values: data.all.slice(start) }];
    }
    // Keep the accounts' own order (and colors), not the order they were ticked in.
    return data.accounts.filter((a) => picked.includes(String(a.id))).map((a) => ({
      name: a.name, cls: 's' + ((a.slot % 8) + 1), dashed: a.slot >= 8, values: a.values.slice(start),
    }));
  }

  function renderTable(dates, series) {
    thead.innerHTML = '<tr><th>Month end</th>' + series.map((s) => `<th class="num">${esc(s.name)}</th>`).join('') + '</tr>';
    tbody.innerHTML = '';
    for (let i = dates.length - 1; i >= 0; i--) {
      const isMonthEnd = i === dates.length - 1 || dates[i + 1].endsWith('-01');
      if (!isMonthEnd) continue;
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${i === dates.length - 1 ? fmtDay(dates[i]) + ' (today)' : fmtMonth(dates[i], true)}</td>` +
        series.map((s) => `<td class="num${s.values[i] < 0 ? ' neg' : ''}">${s.values[i] == null ? '-' : money(s.values[i])}</td>`).join('');
      tbody.appendChild(tr);
    }
  }

  function render() {
    const firstOfRange = data.dates.findIndex((d) => d >= (() => {
      const t = parse(data.dates[data.dates.length - 1]);
      const s = new Date(t.getFullYear(), t.getMonth() - (range - 1), 1);
      return `${s.getFullYear()}-${String(s.getMonth() + 1).padStart(2, '0')}-01`;
    })());
    const start = Math.max(0, firstOfRange);
    const dates = data.dates.slice(start);
    const series = currentSeries(start);
    const n = dates.length;
    renderTable(dates, series);

    const latest = series.map((s) => s.values[n - 1]).filter((v) => v != null);
    subtitle.textContent = series.length === 1 && latest.length
      ? `${series[0].name}: ${money(latest[0])} today`
      : `Comparing ${series.length} accounts`;

    const W = Math.max(box.clientWidth, 280), H = 260;
    const labelW = Math.min(150, Math.max(...series.map((s) => s.name.length)) * 6.5 + 16);
    const pad = { l: 58, r: labelW, t: 14, b: 28 };
    const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
    const vals = series.flatMap((s) => s.values).filter((v) => v != null);
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    if (!vals.length || hi === lo) hi = lo + 100;
    const step = niceStep(hi - lo);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    const x = (i) => pad.l + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const y = (v) => pad.t + plotH - ((v - lo) / (hi - lo)) * plotH;

    box.innerHTML = '';
    const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, box);
    for (let v = lo; v <= hi + 1e-9; v += step) {
      el('line', { x1: pad.l, x2: pad.l + plotW, y1: y(v), y2: y(v), class: Math.abs(v) < 1e-9 ? 'baseline' : 'grid' }, svg);
      el('text', { x: pad.l - 8, y: y(v) + 4, class: 'tick', 'text-anchor': 'end' }, svg).textContent = compact(v);
    }
    // Month labels on the 1st of each month (every 3rd month when showing 24).
    let monthNo = 0;
    dates.forEach((d, i) => {
      if (!d.endsWith('-01')) return;
      if (monthNo++ % (range > 12 ? 3 : 1) !== 0) return;
      el('text', { x: x(i), y: H - 8, class: 'tick', 'text-anchor': 'middle' }, svg)
        .textContent = fmtMonth(d, d.slice(5, 7) === '01' || i === 0);
    });

    // Lines (gaps where an account has no history yet), end dots, direct labels if they don't collide.
    const ends = [];
    series.forEach((s) => {
      let path = '', pen = false;
      s.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        path += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      if (path) el('path', { d: path, class: 'line ' + s.cls + (s.dashed ? ' dashed' : '') }, svg);
      const last = s.values[n - 1];
      if (last != null) {
        el('circle', { cx: x(n - 1), cy: y(last), r: 4, class: 'dot ' + s.cls }, svg);
        ends.push({ y: y(last), name: s.name });
      }
    });
    ends.sort((a, b) => a.y - b.y);
    const fits = ends.every((e, i) => i === 0 || e.y - ends[i - 1].y >= 15);
    if (fits && ends.length <= 4) {
      ends.forEach((e) => {
        el('text', { x: x(n - 1) + 10, y: e.y + 4, class: 'endlabel' }, svg).textContent =
          e.name.length > 20 ? e.name.slice(0, 19) + '…' : e.name;
      });
    }

    // Hover / keyboard layer.
    const cross = el('line', { y1: pad.t, y2: pad.t + plotH, class: 'cross', visibility: 'hidden' }, svg);
    const hoverDots = series.map((s) => el('circle', { r: 4, class: 'dot ' + s.cls, visibility: 'hidden' }, svg));
    const hit = el('rect', { x: pad.l - 6, y: pad.t, width: plotW + 12, height: plotH, class: 'hit', tabindex: 0,
      'aria-label': 'Balances over time. Use left and right arrows to move by a day, Page Up and Page Down by a month.' }, svg);
    let idx = -1;
    const show = (i) => {
      idx = Math.max(0, Math.min(n - 1, i));
      cross.setAttribute('x1', x(idx)); cross.setAttribute('x2', x(idx)); cross.setAttribute('visibility', 'visible');
      const rows = [];
      series.forEach((s, si) => {
        const v = s.values[idx];
        if (v == null) { hoverDots[si].setAttribute('visibility', 'hidden'); return; }
        hoverDots[si].setAttribute('cx', x(idx)); hoverDots[si].setAttribute('cy', y(v));
        hoverDots[si].setAttribute('visibility', 'visible');
        rows.push({ s, v });
      });
      rows.sort((a, b) => b.v - a.v);
      tip.innerHTML = `<strong>${fmtDay(dates[idx])}</strong>` + (rows.length
        ? rows.map((r) => `<div><i class="key ${r.s.cls}"></i>${esc(r.s.name)} <span>${money(r.v)}</span></div>`).join('')
        : '<div class="muted">No history yet</div>');
      tip.hidden = false;
      const right = x(idx) + 14 + tip.offsetWidth <= W;
      tip.style.left = (box.offsetLeft + (right ? x(idx) + 14 : x(idx) - 14 - tip.offsetWidth)) + 'px';
      tip.style.top = (box.offsetTop + pad.t) + 'px';
      hit.setAttribute('aria-label', `${fmtDay(dates[idx])}: ` + rows.map((r) => `${r.s.name} ${money(r.v)}`).join(', '));
    };
    const hide = () => {
      idx = -1;
      cross.setAttribute('visibility', 'hidden');
      hoverDots.forEach((c) => c.setAttribute('visibility', 'hidden'));
      tip.hidden = true;
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

    root.querySelectorAll('[data-range]').forEach((b) => b.classList.toggle('on', +b.dataset.range === range));
  }

  root.querySelectorAll('[data-range]').forEach((b) => b.addEventListener('click', () => {
    range = +b.dataset.range;
    save('balanceRange', String(range));
    render();
  }));
  let t;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(render, 100); });
  syncBoxes();
  render();
})();
