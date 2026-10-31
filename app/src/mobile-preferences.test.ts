import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadMotionPreference, prefersReducedMotion, saveMotionPreference } from './mobile-preferences';

describe('mobile display preferences', () => {
  let values: Map<string, string>;
  beforeEach(() => {
    values = new Map();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    });
    vi.stubGlobal('window', { matchMedia: () => ({ matches: false }) });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('defaults to system and roundtrips only a display preference', () => {
    expect(loadMotionPreference()).toBe('system');
    saveMotionPreference('reduce');
    expect(loadMotionPreference()).toBe('reduce');
    expect([...values.values()]).toEqual(['reduce']);
    saveMotionPreference('system');
    expect(loadMotionPreference()).toBe('system');
    expect(values.size).toBe(1);
  });
  it('ignores malformed persisted values', () => {
    saveMotionPreference('reduce');
    const key = [...values.keys()][0];
    for (const value of ['false', 'normal', '"reduce"', '', '{}']) {
      values.set(key, value);
      expect(loadMotionPreference()).toBe('system');
    }
  });
  it('rejects invalid runtime input without overwriting a valid preference', () => {
    saveMotionPreference('reduce');
    saveMotionPreference('normal' as 'system');
    expect(loadMotionPreference()).toBe('reduce');
    expect([...values.values()]).toEqual(['reduce']);
  });
  it('continues when storage access is unavailable or denied', () => {
    vi.stubGlobal('localStorage', undefined);
    expect(loadMotionPreference()).toBe('system');
    expect(() => saveMotionPreference('reduce')).not.toThrow();
    vi.stubGlobal('localStorage', {
      getItem() { throw new Error('denied'); },
      setItem() { throw new Error('quota'); },
    });
    expect(loadMotionPreference()).toBe('system');
    expect(() => saveMotionPreference('reduce')).not.toThrow();
  });
  it('always honors system reduction and can additionally reduce motion by choice', () => {
    expect(prefersReducedMotion('system')).toBe(false);
    expect(prefersReducedMotion('reduce')).toBe(true);
    saveMotionPreference('reduce');
    expect(prefersReducedMotion()).toBe(true);
    vi.stubGlobal('window', { matchMedia: () => ({ matches: true }) });
    expect(prefersReducedMotion('system')).toBe(true);
  });
  it('works without matchMedia or a browser window', () => {
    vi.stubGlobal('window', undefined);
    expect(prefersReducedMotion('system')).toBe(false);
    expect(prefersReducedMotion('reduce')).toBe(true);
    vi.stubGlobal('window', {});
    expect(prefersReducedMotion('system')).toBe(false);
  });
  it('can read preferences when browser privacy controls reject media queries', () => {
    vi.stubGlobal('window', { matchMedia() { throw new Error('unavailable'); } });
    expect(prefersReducedMotion('system')).toBe(false);
    expect(prefersReducedMotion('reduce')).toBe(true);
  });
});
