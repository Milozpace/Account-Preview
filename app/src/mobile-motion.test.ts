import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountMobileMotion, type MobileTransition } from './mobile-motion';

function fixture() {
  const effects: { finished: Promise<void>; cancel: ReturnType<typeof vi.fn> }[] = [];
  const animate = () => vi.fn((_frames: Keyframe[], _options: KeyframeAnimationOptions) => {
    const effect = { finished: new Promise<void>(() => {}), cancel: vi.fn() };
    effects.push(effect);
    return effect;
  });
  const section = { animate: animate(), matches: () => false };
  const bar = { animate: animate(), matches: () => true };
  const root = { animate: animate(), dataset: {} as DOMStringMap, querySelectorAll: vi.fn(() => [section, bar]) };
  let callback!: IntersectionObserverCallback;
  const observer = { observe: vi.fn(), unobserve: vi.fn(), disconnect: vi.fn() };
  const listeners = new Set<(event: { matches: boolean }) => void>();
  const media = {
    matches: false,
    addEventListener: vi.fn((_type, fn) => listeners.add(fn)),
    removeEventListener: vi.fn((_type, fn) => listeners.delete(fn)),
  };
  vi.stubGlobal('window', { matchMedia: () => media });
  vi.stubGlobal('localStorage', { getItem: () => null });
  vi.stubGlobal('IntersectionObserver', class {
    constructor(fn: IntersectionObserverCallback) { callback = fn; }
    observe = observer.observe;
    unobserve = observer.unobserve;
    disconnect = observer.disconnect;
  });
  const enter = (targets = [section, bar], isIntersecting = true) => callback(targets.map(target => ({ target, isIntersecting })) as unknown as IntersectionObserverEntry[], observer as unknown as IntersectionObserver);
  return { root, section, bar, observer, enter, effects, media, listeners };
}

afterEach(() => vi.unstubAllGlobals());

describe('mobile viewport and page motion', () => {
  it('reveals sections and scales bars once when entering the viewport', () => {
    const f = fixture();
    const dispose = mountMobileMotion(f.root as unknown as HTMLElement);
    expect(f.section.animate).not.toHaveBeenCalled();
    expect(f.observer.observe).toHaveBeenCalledTimes(2);
    f.enter(undefined, false);
    expect(f.section.animate).not.toHaveBeenCalled();
    f.enter();f.enter();
    expect(f.section.animate).toHaveBeenCalledTimes(1);
    expect(f.bar.animate).toHaveBeenCalledTimes(1);
    expect(f.bar.animate.mock.calls[0][0]).toEqual([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }]);
    dispose();
    expect(f.observer.disconnect).toHaveBeenCalledOnce();
    expect(f.effects.every(effect => effect.cancel.mock.calls.length === 1)).toBe(true);
    f.enter();
    expect(f.section.animate).toHaveBeenCalledTimes(1);
  });
  it.each<MobileTransition>(['forward', 'back', 'tab'])('plays a short %s page transition and cancels it on cleanup', transition => {
    const f = fixture();
    const dispose = mountMobileMotion(f.root as unknown as HTMLElement, { transition });
    expect(f.root.animate).toHaveBeenCalledOnce();
    const [frames, options] = f.root.animate.mock.calls[0];
    expect(options.duration).toBeLessThanOrEqual(240);
    expect(frames.at(-1)).toMatchObject({ opacity: 1, transform: 'translateX(0)' });
    dispose();
    expect(f.effects[0].cancel).toHaveBeenCalledOnce();
  });
  it('skips page transition when none is requested', () => {
    const f = fixture();
    mountMobileMotion(f.root as unknown as HTMLElement, { transition: 'none' })();
    expect(f.root.animate).not.toHaveBeenCalled();
  });
  it.each(['user', 'system'])('leaves everything visible and still for %s reduction', source => {
    const f = fixture();f.media.matches = source === 'system';
    const dispose = mountMobileMotion(f.root as unknown as HTMLElement, { preference: source === 'user' ? 'reduce' : 'system', transition: 'forward' });
    expect(f.effects).toHaveLength(0);
    expect(f.observer.observe).not.toHaveBeenCalled();
    expect(f.root.dataset.motionPreference).toBe('reduce');
    dispose();
    expect(f.root.dataset.motionPreference).toBeUndefined();
  });
  it('cancels active motion when the system preference changes and removes its listener', () => {
    const f = fixture();
    const dispose = mountMobileMotion(f.root as unknown as HTMLElement, { transition: 'forward' });
    f.enter();f.media.matches = true;
    f.listeners.forEach(listener => listener({ matches: true }));
    expect(f.effects.every(effect => effect.cancel.mock.calls.length === 1)).toBe(true);
    expect(f.root.dataset.motionPreference).toBe('reduce');
    dispose();
    expect(f.listeners.size).toBe(0);
  });
  it.each(['observer', 'animate'])('keeps content immediately visible without %s support', missing => {
    const f = fixture();
    if (missing === 'observer') vi.stubGlobal('IntersectionObserver', undefined);
    else Object.assign(f.root, { animate: undefined });
    expect(() => mountMobileMotion(f.root as unknown as HTMLElement, { transition: 'forward' })()).not.toThrow();
    expect(f.effects).toHaveLength(0);
  });
  it('restores an existing motion attribute and allows repeated cleanup', () => {
    const f = fixture();f.root.dataset.motionPreference = 'system';
    const dispose = mountMobileMotion(f.root as unknown as HTMLElement, { preference: 'reduce' });
    dispose();dispose();
    expect(f.root.dataset.motionPreference).toBe('system');
    expect(f.listeners.size).toBe(0);
  });
  it('continues displaying sections if a WebView rejects animation keyframes', () => {
    const f = fixture();f.section.animate.mockImplementation(() => { throw new Error('unsupported'); });
    const dispose = mountMobileMotion(f.root as unknown as HTMLElement);
    expect(() => f.enter()).not.toThrow();
    expect(f.bar.animate).toHaveBeenCalledOnce();
    dispose();
  });
});
