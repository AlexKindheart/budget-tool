// Live totals on the Budget grid, and the "copy January across" button.
(function () {
  const form = document.querySelector('[data-budget-grid]');
  if (!form) return;

  const money = (v) => (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const num = (input) => parseFloat((input.value || '').replace(/[$,]/g, '')) || 0;

  function recalc() {
    const sums = { income: Array(12).fill(0), expense: Array(12).fill(0) };
    form.querySelectorAll('tr[data-row]').forEach((tr) => {
      const inputs = tr.querySelectorAll('input.money');
      let total = 0;
      inputs.forEach((inp, i) => { const v = num(inp); total += v; sums[tr.dataset.kind][i] += v; });
      tr.querySelector('[data-total]').textContent = money(total);
      tr.querySelector('[data-avg]').textContent = money(total / 12);
    });
    const set = (name, col, v) => {
      const cell = form.querySelector(`[data-sum="${name}"][data-col="${col}"]`);
      if (cell) cell.textContent = money(v);
    };
    let bal = num(form.querySelector('[data-start]'));
    let ti = 0, te = 0;
    for (let i = 0; i < 12; i++) {
      const inc = sums.income[i], exp = sums.expense[i];
      ti += inc; te += exp; bal += inc - exp;
      set('income', i, inc); set('expense', i, exp); set('net', i, inc - exp); set('proj', i, bal);
    }
    set('income', 'total', ti); set('expense', 'total', te); set('net', 'total', ti - te);
  }

  form.addEventListener('input', recalc);
  form.querySelectorAll('[data-fill]').forEach((btn) => btn.addEventListener('click', () => {
    const inputs = btn.closest('tr').querySelectorAll('input.money');
    inputs.forEach((inp) => { inp.value = inputs[0].value; });
    recalc();
  }));
})();

// Export screen: quick date ranges, All / None for accounts, and a live count of what will be exported.
(function () {
  const form = document.querySelector('[data-export]');
  if (!form) return;
  const from = form.querySelector('input[name="from"]');
  const to = form.querySelector('input[name="to"]');
  const presets = [...form.querySelectorAll('[data-from]')];
  const out = form.querySelector('[data-preview]');
  const money = (v) => '$' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function markPreset() {
    presets.forEach((b) => b.classList.toggle('on', b.dataset.from === from.value && b.dataset.to === to.value));
  }
  let timer;
  function refresh() {
    markPreset();
    clearTimeout(timer);
    timer = setTimeout(() => {
      const q = new URLSearchParams(new FormData(form));
      q.delete('csrf');
      if (!q.has('account')) q.append('account', '');  // none ticked: count nothing, not everything
      fetch(form.dataset.previewUrl + '?' + q.toString())
        .then((r) => r.json())
        .then((d) => {
          out.textContent = `${d.count.toLocaleString()} transaction${d.count === 1 ? '' : 's'} · ${money(d.money_in)} in · ${money(d.money_out)} out`;
        })
        .catch(() => {});
    }, 150);
  }
  presets.forEach((b) => b.addEventListener('click', () => { from.value = b.dataset.from; to.value = b.dataset.to; refresh(); }));
  form.querySelectorAll('[data-check]').forEach((a) => a.addEventListener('click', (e) => {
    e.preventDefault();
    form.querySelectorAll(`input[name="${a.dataset.check}"]`).forEach((c) => { c.checked = a.dataset.to === '1'; });
    refresh();
  }));
  form.addEventListener('change', refresh);
})();
