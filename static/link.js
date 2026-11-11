// "Connect bank" / "Reconnect": opens Plaid's sign-in in your web browser, then waits
// for it to finish and refreshes the app window.
(function () {
  const csrf = document.querySelector('meta[name="csrf"]').content;

  function showError(msg) {
    const box = document.getElementById('js-error');
    box.textContent = msg;
    box.hidden = false;
    window.scrollTo(0, 0);
  }

  async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Request failed (' + res.status + ')');
    return data;
  }

  async function connect(button, itemId) {
    const label = button.textContent;
    try {
      const { version } = await api('/api/open-link', itemId ? { item_id: itemId } : {});
      button.disabled = true;
      button.textContent = 'Finish in your browser…';
      const timer = setInterval(async () => {
        try {
          const now = await api('/api/data-version');
          if (now.version !== version) { clearInterval(timer); window.location.reload(); }
        } catch (e) { /* keep waiting */ }
      }, 2000);
      // Let them retry if they closed the browser tab.
      setTimeout(() => { button.disabled = false; button.textContent = label; }, 15000);
    } catch (e) {
      showError(e.message);
    }
  }

  document.querySelectorAll('[data-connect]').forEach((b) =>
    b.addEventListener('click', () => connect(b, null)));
  document.querySelectorAll('[data-reconnect]').forEach((b) =>
    b.addEventListener('click', () => connect(b, b.dataset.reconnect)));
})();
