// Overview "Trends" chart: monthly Income vs Expenses as paired columns (inline SVG, no libraries).
// Hover or focus a month for income / expenses / net; click it to open that month.
(function () {
  const root = document.querySelector('[data-trend]');
  if (!root) return;
  const all = JSON.parse(root.querySelector('.trend-data').textContent);
  const box = root.querySelector('.chart');
  const tip = root.querySelector('.tip');
  const selected = root.dataset.selected;
  const NS = 'http://www.w3.org/2000/svg';
  let range = 12;
  try { range = parseInt(localStorage.getItem('trendRange'), 10) || 12; } catch (e) { /* storage unavailable */ }

  const money = (v) => (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const compact = (v) => {
    const a = Math.abs(v), s = v < 0 ? '-' : '';
    if (a >= 1e6) return s + '$' + +(a / 1e6).toFixed(1) + 'M';
    if (a >= 1e4) return s + '$' + +(a / 1e3).toFixed(0) + 'K';
    return s + '$' + a.toLocaleString();
  };
  const monthName = (key, withYear) => {
    const [y, m] = key.split('-').map(Number);
    const d = new Date(y, m - 1, 1);
    return d.toLocaleString(undefined, withYear ? { month: 'short', year: 'numeric' } : { month: 'short' });
  };
  const el = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };
  // Clean tick step: 1, 2, 2.5 or 5 × 10^n giving about 4 intervals.
  const niceStep = (span) => {
    const raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    return [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw) || mag * 10;
  };
  // Column path: 4px rounded data-end, square at the baseline (works for negative values too).
  const column = (x, w, y0, y1) => {
    const h = Math.abs(y1 - y0);
    if (h < 0.5) return '';
    const r = Math.min(4, w / 2, h);
    if (y1 < y0) {
      return `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 + r} V${y0} Z`;
    }
    return `M${x},${y0} V${y1 - r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 - r} V${y0} Z`;
  };

  function render() {
    const data = all.slice(-range);
    const W = Math.max(box.clientWidth, 280), H = 240;
    const pad = { l: 52, r: 8, t: 12, b: 28 };
    const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
    const vals = data.flatMap((d) => [d.income, d.expense]);
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    if (hi === lo) hi = lo + 100;
    const step = niceStep(hi - lo);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    const y = (v) => pad.t + plotH - ((v - lo) / (hi - lo)) * plotH;

    box.innerHTML = '';
    const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, box);

    for (let v = lo; v <= hi + 1e-9; v += step) {  // recessive gridlines + y ticks
      el('line', { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: v === 0 ? 'baseline' : 'grid' }, svg);
      el('text', { x: pad.l - 8, y: y(v) + 4, class: 'tick', 'text-anchor': 'end' }, svg).textContent = compact(v);
    }

    const band = plotW / data.length;
    const barW = Math.max(3, Math.min(24, (band - 10) / 2));  // two columns + 2px gap, capped at 24px
    const labelEvery = data.length > 12 ? 3 : 1;
    data.forEach((d, i) => {
      const cx = pad.l + band * i + band / 2;
      const g = el('g', { class: 'month' + (d.month === selected ? ' selected' : '') }, svg);
      const p1 = column(cx - 1 - barW, barW, y(0), y(d.income));
      const p2 = column(cx + 1, barW, y(0), y(d.expense));
      if (p1) el('path', { d: p1, class: 's1' }, g);
      if (p2) el('path', { d: p2, class: 's2' }, g);
      if (i % labelEvery === 0 || d.month === selected) {
        const first = i === 0 || d.month.endsWith('-01');
        el('text', { x: cx, y: H - 8, class: 'tick' + (d.month === selected ? ' strong' : ''), 'text-anchor': 'middle' }, svg)
          .textContent = monthName(d.month, first && data.length > 6);
      }
      // Hit target: the whole month band, bigger than the marks.
      const hit = el('rect', { x: pad.l + band * i, y: pad.t, width: band, height: plotH, class: 'hit', tabindex: 0,
        'aria-label': `${monthName(d.month, true)}: income ${money(d.income)}, expenses ${money(d.expense)}, net ${money(d.net)}` }, svg);
      const show = () => {
        g.classList.add('hover');
        tip.innerHTML =
          `<strong>${monthName(d.month, true)}</strong>` +
          `<div><i class="swatch s1"></i>Income <span>${money(d.income)}</span></div>` +
          `<div><i class="swatch s2"></i>Expenses <span>${money(d.expense)}</span></div>` +
          `<div class="net">Net <span>${money(d.net)}</span></div>`;
        tip.hidden = false;
        const tx = Math.min(Math.max(cx - tip.offsetWidth / 2, 0), W - tip.offsetWidth);
        tip.style.left = (box.offsetLeft + tx) + 'px';
        tip.style.top = (box.offsetTop + 4) + 'px';
      };
      const hide = () => { g.classList.remove('hover'); tip.hidden = true; };
      const open = () => { window.location.href = `${root.dataset.monthUrl}?month=${d.month}`; };
      hit.addEventListener('mouseenter', show);
      hit.addEventListener('mouseleave', hide);
      hit.addEventListener('focus', show);
      hit.addEventListener('blur', hide);
      hit.addEventListener('click', open);
      hit.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    });

    root.querySelectorAll('[data-range]').forEach((b) => b.classList.toggle('on', +b.dataset.range === range));
  }

  root.querySelectorAll('[data-range]').forEach((b) => b.addEventListener('click', () => {
    range = +b.dataset.range;
    try { localStorage.setItem('trendRange', String(range)); } catch (e) { /* storage unavailable */ }
    render();
  }));
  let t;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(render, 100); });
  render();
})();
