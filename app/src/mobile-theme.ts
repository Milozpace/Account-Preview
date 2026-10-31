export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';
type NativeAppearance = { setAppearance(value: string): boolean | void; getSystemTheme?(): string; getAppearancePreference?(): string };
const nativeAppearance = () => (window as Window & { AccountAppearance?: NativeAppearance }).AccountAppearance;
const key = 'account-app:theme-preference';
const preferenceEvent = 'account-appearance-preference';
let sessionPreference: ThemePreference = 'system';
let unsavedPreference: ThemePreference | undefined;
const valid = (value: unknown): value is ThemePreference => value === 'system' || value === 'light' || value === 'dark';

export function loadThemePreference(): ThemePreference {
  if (unsavedPreference !== undefined) return unsavedPreference;
  try { const value = nativeAppearance()?.getAppearancePreference?.(); if (valid(value)) return value; }
  catch { /* Browser/older host: use WebView storage. */ }
  try { const value = localStorage.getItem(key); return valid(value) ? value : 'system'; }
  catch { return sessionPreference; }
}

export function saveThemePreference(value: ThemePreference): void {
  if (!valid(value)) return;
  sessionPreference = value;
  try { localStorage.setItem(key, value); unsavedPreference = undefined; }
  catch { unsavedPreference = value; }
  // Android stores the display choice before publishing the change. WebView storage
  // may not flush before process termination, so it is not Android's authority.
  try {
    const appearance = nativeAppearance();
    if (appearance?.getAppearancePreference && appearance.setAppearance(value) === false) unsavedPreference = value;
  }
  catch { unsavedPreference = value; }
  window.dispatchEvent(new Event(preferenceEvent));
}

export function resolveTheme(preference: ThemePreference, systemDark: boolean): ResolvedTheme {
  return preference === 'system' ? systemDark ? 'dark' : 'light' : preference;
}

/** Mounted before phone UI imports. CSS changes preserve forms, filters and open dialogs. */
export function mountMobileTheme(mobile: boolean, onChange?: () => void): () => void {
  if (!mobile) return () => undefined;
  return mountAppearance('mobile', onChange);
}

export function mountDesktopTheme(onChange?: () => void, nativeTheme?: () => ResolvedTheme | undefined): () => void {
  return mountAppearance('desktop', onChange, nativeTheme);
}

/** WebView2 can report light media in a dark Windows window. Read the native host. */
export async function mountDesktopWindowTheme(host: {
  theme(): Promise<ResolvedTheme | null>;
  setTheme(value: ResolvedTheme | null): Promise<void>;
  onThemeChanged(changed: (event: { payload: ResolvedTheme }) => void): Promise<() => void>;
}): Promise<{ readTheme: () => ResolvedTheme | undefined; dispose: () => void }> {
  let nativeTheme: ResolvedTheme | undefined;
  let active = true, revision = 0;
  const publish = (value: ResolvedTheme | null) => {
    if (!active || (value !== 'dark' && value !== 'light')) return;
    nativeTheme = value;
    window.dispatchEvent(new Event('account-system-appearance'));
  };
  const sync = async () => {
    const turn = ++revision;
    const preference = loadThemePreference();
    try {
      await host.setTheme(preference === 'system' ? null : preference);
      const value = await host.theme();
      if (turn === revision) publish(value);
    } catch { /* Older host: keep the browser fallback and usable page. */ }
  };
  let unlisten: (() => void) | undefined;
  try { unlisten = await host.onThemeChanged(event => publish(event.payload)); }
  catch { /* Theme reads still support older hosts without native events. */ }
  window.addEventListener(preferenceEvent, sync);
  await sync();
  return { readTheme: () => nativeTheme, dispose: () => {
    active = false; revision++; unlisten?.(); window.removeEventListener(preferenceEvent, sync);
  } };
}

function mountAppearance(layout: 'mobile' | 'desktop', onChange?: () => void, nativeTheme?: () => ResolvedTheme | undefined): () => void {
  let media: MediaQueryList | undefined;
  try { media = window.matchMedia?.('(prefers-color-scheme: dark)'); } catch { /* Light fallback. */ }
  let previous: ResolvedTheme | undefined;
  const systemDark = () => {
    try {
      const value = layout === 'mobile' ? nativeAppearance()?.getSystemTheme?.() : nativeTheme?.();
      if (value === 'dark' || value === 'light') return value === 'dark';
    } catch { /* Older native hosts retain the standard browser fallback. */ }
    return Boolean(media?.matches);
  };
  const apply = () => {
    const preference = loadThemePreference();
    const theme = resolveTheme(preference, systemDark());
    const root = document.documentElement;
    root.dataset.layout = layout; root.dataset.theme = theme;
    root.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#161719' : '#f5f4f0');
    // The native interface accepts only these display values; no ledger access.
    try { if (layout === 'mobile') nativeAppearance()?.setAppearance(preference); }
    catch { /* A missing/older host does not block the page. */ }
    const changed = previous !== undefined && previous !== theme; previous = theme;
    if (changed) onChange?.();
  };
  const foreground = () => { if (!document.hidden) apply(); };
  apply();
  media?.addEventListener?.('change', apply);
  window.addEventListener(preferenceEvent, apply);
  window.addEventListener('account-system-appearance', apply);
  document.addEventListener('visibilitychange', foreground);
  return () => {
    media?.removeEventListener?.('change', apply);
    window.removeEventListener(preferenceEvent, apply);
    window.removeEventListener('account-system-appearance', apply);
    document.removeEventListener('visibilitychange', foreground);
  };
}
