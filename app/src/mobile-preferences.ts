export type MotionPreference = 'system' | 'reduce';

const motionKey = 'account-app:motion-preference';

export function loadMotionPreference(): MotionPreference {
  try {
    return localStorage.getItem(motionKey) === 'reduce' ? 'reduce' : 'system';
  } catch {
    return 'system';
  }
}

export function saveMotionPreference(value: MotionPreference): void {
  if (value !== 'system' && value !== 'reduce') return;
  try {
    localStorage.setItem(motionKey, value);
  } catch { /* Display preferences must not prevent read-only ledger access. */ }
}

export function prefersReducedMotion(preference: MotionPreference = loadMotionPreference()): boolean {
  if (preference === 'reduce') return true;
  try {
    return typeof window !== 'undefined' && Boolean(window?.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
  } catch {
    return false;
  }
}
