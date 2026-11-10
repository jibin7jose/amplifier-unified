// Installation is offered by the browser. Registration never reloads an active chat.
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', {scope: '/', updateViaCache: 'none'})
      .catch(() => { /* The online app continues to work if offline support is unavailable. */ });
  }, {once: true});
}

// Only the public fallback document may navigate automatically. An active chat
// recovers its event stream in place so unsaved work is never discarded here.
if (document.querySelector('[data-offline-recovery]')) {
  const status = document.querySelector('[data-offline-status]');
  const retry = document.querySelector('[data-offline-retry]');
  document.querySelector('[data-offline-origin]').textContent = location.origin;
  let timer, controller, pending = false, active = true, leaving = false, delay = 2000;
  const schedule = () => {
    clearTimeout(timer);
    if (active && !document.hidden && !leaving) timer = setTimeout(check, delay);
  };
  async function check() {
    clearTimeout(timer);
    if (pending || !active || document.hidden || leaving) return;
    pending = true;
    retry.disabled = true;
    retry.textContent = 'Checking…';
    status.textContent = 'Checking the connection…';
    controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/health', {cache:'no-store', credentials:'same-origin', signal:controller.signal});
      const health = response.ok ? await response.json() : null;
      const destination = location.pathname === '/login' ? '/login' : '/';
      // Health may be reachable while the app/login route is not. Check that
      // route with a normal GET (not a navigation) to avoid fallback reload loops.
      const entry = health?.ok === true && health.app === 'amplifier-unified'
        ? await fetch(destination, {cache:'no-store', credentials:'same-origin', signal:controller.signal}) : null;
      if (entry?.ok && entry.headers.get('content-type')?.includes('text/html') && active && !document.hidden) {
        leaving = true;
        status.textContent = 'Connected. Opening Amplifier…';
        location.replace(destination);
      } else {
        status.textContent = 'Still waiting for Amplifier. We’ll check again shortly.';
      }
    } catch {
      status.textContent = 'Still waiting for Amplifier. We’ll check again shortly.';
    } finally {
      clearTimeout(timeout);
      controller = null;
      pending = false;
      retry.disabled = leaving;
      retry.textContent = 'Try again';
      schedule();
      delay = Math.min(delay * 2, 30000);
    }
  }
  const resume = () => { delay = 2000; check(); };
  retry.addEventListener('click', resume);
  window.addEventListener('online', resume);
  window.addEventListener('pageshow', () => { active = true; resume(); });
  window.addEventListener('pagehide', () => { active = false; clearTimeout(timer); controller?.abort(); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(timer); controller?.abort(); }
    else resume();
  });
  check();
}
