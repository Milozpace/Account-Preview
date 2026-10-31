import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { startPageSession } from './lifecycle';

describe('page service lifetime', () => {
  let page: EventTarget;
  let doc: EventTarget;
  let dispose: (() => void) | undefined;
  const fetcher = vi.fn().mockResolvedValue({ ok: true });
  const beacon = vi.fn().mockReturnValue(true);
  beforeEach(() => {
    vi.useFakeTimers();fetcher.mockClear();beacon.mockClear();beacon.mockReturnValue(true);
    page = new EventTarget();doc = Object.assign(new EventTarget(), { visibilityState: 'visible' });
    vi.stubGlobal('window', page);vi.stubGlobal('document', doc);
    vi.stubGlobal('fetch', fetcher);vi.stubGlobal('navigator', { sendBeacon: beacon });
  });
  afterEach(() => { dispose?.();dispose = undefined;vi.useRealTimers();vi.unstubAllGlobals(); });
  const requests = () => fetcher.mock.calls.map((call) => JSON.parse(call[1].body));

  it('registers one page and stops heartbeats when it closes', () => {
    dispose = startPageSession();
    const id = requests()[0].id;
    vi.advanceTimersByTime(15000);
    expect(requests().map((r) => r.action)).toEqual(['open', 'ping']);
    page.dispatchEvent(new Event('pagehide'));
    expect(beacon).toHaveBeenCalledTimes(1);
    expect(beacon.mock.calls[0][0]).toBe('/api/session');
    expect(requests().every((r) => r.id === id)).toBe(true);
    vi.advanceTimersByTime(120000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('restores a cached page using a new session and renews on visibility', () => {
    dispose = startPageSession();
    const oldId = requests()[0].id;
    page.dispatchEvent(new Event('pagehide'));
    page.dispatchEvent(Object.assign(new Event('pageshow'), { persisted: true }));
    expect(requests().at(-1).action).toBe('open');
    expect(requests().at(-1).id).not.toBe(oldId);
    doc.dispatchEvent(new Event('visibilitychange'));
    expect(requests().at(-1).action).toBe('ping');
  });

  it('falls back to a keepalive request if closing beacon cannot be sent', () => {
    beacon.mockReturnValue(false);
    dispose = startPageSession();
    page.dispatchEvent(new Event('pagehide'));
    expect(requests().at(-1).action).toBe('close');
    expect(fetcher.mock.calls.at(-1)![1].keepalive).toBe(true);
  });

  it('also releases on beforeunload and sends only one close notification', () => {
    dispose = startPageSession();
    page.dispatchEvent(new Event('beforeunload'));
    expect(beacon).toHaveBeenCalledTimes(1);
    page.dispatchEvent(new Event('pagehide'));
    expect(beacon).toHaveBeenCalledTimes(1);
  });
});
