// Each browser document owns one lease; no transaction data leaves the page.
export function startPageSession(): () => void {
  let pageId = crypto.randomUUID();
  let closed = false;
  let timer: ReturnType<typeof setInterval>;
  const body = (action: string) => JSON.stringify({ id: pageId, action });
  const notify = (action: string) => {
    void fetch('/api/session', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-Dashboard-Request': '1' },
      body: body(action), keepalive: action === 'close',
    }).catch(() => { /* A missed heartbeat expires on the server. */ });
  };
  const open = () => {
    notify('open');
    timer = setInterval(() => { if (!closed) notify('ping'); }, 15000);
  };
  const close = () => {
    if (closed) return;
    closed = true;clearInterval(timer);
    try {
      if (navigator.sendBeacon('/api/session', new Blob([body('close')], { type: 'text/plain;charset=UTF-8' }))) return;
    } catch { /* Use keepalive when the browser cannot queue a beacon. */ }
    notify('close');
  };
  const restore = (event: PageTransitionEvent) => {
    if (event.persisted && closed) { pageId = crypto.randomUUID();closed = false;open(); }
  };
  const visible = () => { if (!closed && document.visibilityState === 'visible') notify('ping'); };
  window.addEventListener('pagehide', close);
  window.addEventListener('beforeunload', close);
  window.addEventListener('pageshow', restore);
  document.addEventListener('visibilitychange', visible);
  open();
  return () => {
    close();
    window.removeEventListener('pagehide', close);
    window.removeEventListener('beforeunload', close);
    window.removeEventListener('pageshow', restore);
    document.removeEventListener('visibilitychange', visible);
  };
}
