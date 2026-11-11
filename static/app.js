// Live totals on the Budget grid, and the "copy January across" button.
(function () {
  const form = document.querySelector('[data-budget-grid]');
  if (!form) return;

  const money = (v) => {  // whole dollars without ".00", like the page itself
    const r = Math.round(v * 100) / 100, d = Number.isInteger(r) ? 0 : 2;
    return (r < 0 ? '-' : '') + '$' + Math.abs(r).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  };
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

// Add / edit a transaction: only offer categories that fit. A deposit gets income categories, a payment
// expense categories, and non-budget ones ([Transfer]...) fit both. The server checks the same rule.
document.querySelectorAll('[data-catfilter]').forEach((form) => {
  const sel = form.querySelector('select[name="category_id"]');
  const pay = form.querySelector('input[name="payment"]');
  const dep = form.querySelector('input[name="deposit"]');
  if (!sel || !pay || !dep) return;
  const num = (i) => parseFloat((i.value || '').replace(/[$,]/g, '')) || 0;
  const sideOf = () => { const a = num(dep) - num(pay); return a > 0 ? 'income' : a < 0 ? 'expense' : ''; };
  const original = sel.value, originalSide = sideOf();  // an older transaction keeps the category it already has
  function apply() {
    const side = sideOf();
    sel.querySelectorAll('optgroup').forEach((g) => {
      let any = false;
      g.querySelectorAll('option').forEach((o) => {
        const show = !side || g.dataset.kind === side || g.dataset.kind === 'transfer'
          || (o.value === original && side === originalSide);
        o.hidden = !show; o.disabled = !show;
        any = any || show;
      });
      g.hidden = !any; g.disabled = !any;
    });
    if (sel.selectedOptions[0] && sel.selectedOptions[0].disabled) sel.value = '';  // no longer fits
  }
  [pay, dep].forEach((i) => i.addEventListener('input', apply));
  apply();
});

// Register: changing a category saves in place (no reload, so the page doesn't jump to the top).
// If it can't be saved, the dropdown goes back and a message pops up where you are.
(function () {
  function toast(msg, kind) {
    const t = document.createElement('div');
    t.className = 'toast ' + (kind || 'ok');
    t.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.classList.add('gone'), kind === 'error' ? 6000 : 3500);
    setTimeout(() => t.remove(), kind === 'error' ? 6600 : 4100);
  }
  document.querySelectorAll('select[data-autosave]').forEach((sel) => {
    let last = sel.value;
    sel.addEventListener('change', () => {
      const form = sel.form;
      const body = new FormData(form);
      sel.classList.add('saving');
      fetch(form.action, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } })
        .then((r) => r.json().catch(() => ({})).then((d) => {
          if (!r.ok || !d.ok) throw new Error(d.error || "Couldn't save that category. Refresh the page and try again.");
        }))
        .then(() => {
          last = sel.value;
          sel.classList.toggle('missing', !sel.value);
          sel.classList.add('saved');
          setTimeout(() => sel.classList.remove('saved'), 1200);
        })
        .catch((e) => { sel.value = last; toast(e.message, 'error'); })
        .finally(() => sel.classList.remove('saving'));
    });
  });
})();
