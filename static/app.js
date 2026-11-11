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
