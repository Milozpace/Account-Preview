import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadThemePreference, saveThemePreference, resolveTheme, mountMobileTheme } from './mobile-theme';
import * as appearance from './mobile-theme';

describe('mobile appearance', () => {
  let values: Map<string, string>;
  let dark: boolean;
  let media: EventTarget & { matches: boolean };
  let root: { dataset: Record<string, string>; style: Record<string, string> };
  let native: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    values = new Map(); dark = false;
    media = Object.defineProperty(new EventTarget(), 'matches', { get: () => dark }) as typeof media;
    root = { dataset: {}, style: {} }; native = vi.fn();
    vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } });
    vi.stubGlobal('window', Object.assign(new EventTarget(), { matchMedia: () => media, AccountAppearance: { setAppearance: native } }));
    vi.stubGlobal('document', Object.assign(new EventTarget(), { documentElement: root, hidden: false, querySelector: () => null }));
    saveThemePreference('system'); values.clear();
  });
  afterEach(() => { vi.unstubAllGlobals(); });
  it('defaults to system and remembers each valid preference', () => {
    expect(loadThemePreference()).toBe('system');
    for (const value of ['light', 'dark', 'system'] as const) {
      saveThemePreference(value); expect(loadThemePreference()).toBe(value);
    }
    expect(values.size).toBe(1);
  });
  it('rejects malformed preferences without overwriting a valid selection', () => {
    saveThemePreference('dark'); saveThemePreference('invalid' as 'dark');
    expect(loadThemePreference()).toBe('dark');
    values.set([...values.keys()][0], 'invalid'); expect(loadThemePreference()).toBe('system');
  });
  it('resolves explicit themes ahead of the system', () => {
    expect(resolveTheme('system', true)).toBe('dark'); expect(resolveTheme('system', false)).toBe('light');
    expect(resolveTheme('light', true)).toBe('light'); expect(resolveTheme('dark', false)).toBe('dark');
  });
  it('applies before UI rendering and follows live system changes', () => {
    const changed = vi.fn(); const stop = mountMobileTheme(true, changed);
    expect(root.dataset.theme).toBe('light'); expect(root.style.colorScheme).toBe('light');
    dark = true; media.dispatchEvent(new Event('change'));
    expect(root.dataset.theme).toBe('dark'); expect(changed).toHaveBeenCalledOnce();
    expect(native).toHaveBeenLastCalledWith('system'); stop();
    dark = false; media.dispatchEvent(new Event('change')); expect(root.dataset.theme).toBe('dark');
  });
  it('switches immediately and pins explicit choices against system changes', () => {
    const changed = vi.fn(); const stop = mountMobileTheme(true, changed);
    saveThemePreference('dark'); expect(root.dataset.theme).toBe('dark');
    dark = true; media.dispatchEvent(new Event('change')); saveThemePreference('light');
    expect(root.dataset.theme).toBe('light'); expect(native).toHaveBeenLastCalledWith('light');
    saveThemePreference('system'); expect(root.dataset.theme).toBe('dark');
    expect(changed).toHaveBeenCalledTimes(3); stop();
  });
  it('rechecks system preference when returning to the foreground', () => {
    const stop = mountMobileTheme(true); dark = true; document.dispatchEvent(new Event('visibilitychange'));
    expect(root.dataset.theme).toBe('dark'); stop();
  });
  it('restores a saved preference on remount', () => {
    saveThemePreference('dark'); const stop = mountMobileTheme(true);
    expect(root.dataset.theme).toBe('dark'); expect(native).toHaveBeenCalledWith('dark'); stop();
  });
  it('does not apply mobile appearance to the desktop', () => {
    saveThemePreference('dark'); const stop = mountMobileTheme(false);
    expect(root.dataset.theme).toBeUndefined(); expect(native).not.toHaveBeenCalled(); stop();
  });
  it('mounts desktop appearance and follows system changes without an Android bridge', () => {
    const changed = vi.fn();
    const stop = (appearance as any).mountDesktopTheme?.(changed);
    expect(root.dataset.layout).toBe('desktop');
    expect(root.dataset.theme).toBe('light');
    dark = true; media.dispatchEvent(new Event('change'));
    expect(root.dataset.theme).toBe('dark'); expect(changed).toHaveBeenCalledOnce();
    expect(native).not.toHaveBeenCalled(); stop?.();
    dark = false; media.dispatchEvent(new Event('change')); expect(root.dataset.theme).toBe('dark');
  });
  it('retains an explicit desktop choice across remounts and returns to system', () => {
    let stop = (appearance as any).mountDesktopTheme?.();
    saveThemePreference('dark'); expect(root.dataset.theme).toBe('dark'); stop?.();
    stop = (appearance as any).mountDesktopTheme?.();
    expect(root.dataset.theme).toBe('dark');
    saveThemePreference('system'); expect(root.dataset.theme).toBe('light'); stop?.();
  });
  it('uses native Windows appearance when WebView media remains light and refreshes after unpinning', async () => {
    let current = 'dark' as 'light' | 'dark';
    const host = { theme: async () => current, setTheme: vi.fn(async (value: 'light' | 'dark' | null) => { current = value ?? 'dark'; }), onThemeChanged: vi.fn(async () => () => undefined) };
    const binding = await (appearance as any).mountDesktopWindowTheme?.(host);
    expect(binding?.readTheme()).toBe('dark');
    const stop = (appearance as any).mountDesktopTheme(undefined, binding.readTheme);
    expect(media.matches).toBe(false); expect(root.dataset.theme).toBe('dark');
    saveThemePreference('light'); await vi.waitFor(() => expect(current).toBe('light'));
    expect(root.dataset.theme).toBe('light');
    saveThemePreference('system'); await vi.waitFor(() => expect(root.dataset.theme).toBe('dark'));
    stop(); binding.dispose(); const calls = host.setTheme.mock.calls.length;
    saveThemePreference('light'); expect(host.setTheme).toHaveBeenCalledTimes(calls);
  });
  it('keeps current-session choices usable if preference storage is denied', () => {
    vi.stubGlobal('localStorage', { getItem() { throw Error('denied'); }, setItem() { throw Error('denied'); } });
    const stop = mountMobileTheme(true); expect(() => saveThemePreference('dark')).not.toThrow();
    expect(root.dataset.theme).toBe('dark'); expect(loadThemePreference()).toBe('dark'); stop();
  });
  it('uses actual Android appearance when WebView media queries remain light', () => {
    const getSystemTheme = vi.fn(() => 'dark');
    Object.assign(window, { AccountAppearance: { setAppearance: native, getSystemTheme } });
    const changed = vi.fn(); const stop = mountMobileTheme(true, changed);
    expect(media.matches).toBe(false); expect(root.dataset.theme).toBe('dark');
    getSystemTheme.mockReturnValue('light'); window.dispatchEvent(new Event('account-system-appearance'));
    expect(root.dataset.theme).toBe('light'); expect(changed).toHaveBeenCalledOnce();
    saveThemePreference('dark'); window.dispatchEvent(new Event('account-system-appearance'));
    expect(root.dataset.theme).toBe('dark'); stop();
  });
  it('falls back to browser appearance when the native host cannot return a valid theme', () => {
    dark = true;
    const getSystemTheme = vi.fn(() => 'unknown');
    Object.assign(window, { AccountAppearance: { setAppearance: native, getSystemTheme } });
    const stop = mountMobileTheme(true); expect(root.dataset.theme).toBe('dark');
    getSystemTheme.mockImplementation(() => { throw Error('old host'); });
    dark = false; media.dispatchEvent(new Event('change')); expect(root.dataset.theme).toBe('light'); stop();
  });
  it('restores Android preference before stale WebView storage can overwrite it', () => {
    values.set('account-app:theme-preference', 'light');
    let persisted = 'dark';
    const setAppearance = vi.fn((value: string) => { persisted = value; return true; });
    Object.assign(window, { AccountAppearance: { setAppearance, getAppearancePreference: () => persisted } });
    const stop = mountMobileTheme(true);
    expect(root.dataset.theme).toBe('dark'); expect(loadThemePreference()).toBe('dark');
    saveThemePreference('light'); expect(root.dataset.theme).toBe('light'); expect(persisted).toBe('light'); stop();
  });
  it('keeps a choice usable in-session if Android cannot persist it', () => {
    Object.assign(window, { AccountAppearance: { setAppearance: () => false, getAppearancePreference: () => 'system' } });
    const stop = mountMobileTheme(true); saveThemePreference('dark');
    expect(root.dataset.theme).toBe('dark'); expect(loadThemePreference()).toBe('dark'); stop();
  });
  it('works without matchMedia or the native appearance interface', () => {
    vi.stubGlobal('window', new EventTarget());
    expect(() => { const stop = mountMobileTheme(true); stop(); }).not.toThrow();
  });
  it('applies the choice even when reads work but persistence is full', () => {
    vi.stubGlobal('localStorage', { getItem: () => 'light', setItem() { throw Error('quota'); } });
    const stop = mountMobileTheme(true); saveThemePreference('dark');
    expect(root.dataset.theme).toBe('dark'); stop();
  });
});
