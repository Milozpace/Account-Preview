import { loadMotionPreference, prefersReducedMotion, type MotionPreference } from './mobile-preferences';

export type MobileTransition = 'none' | 'forward' | 'back' | 'tab';

const easing = 'cubic-bezier(.22, 1, .36, 1)';
const bars = '.pool-track i, .category-track i';
const reveal = '.hero-summary, .mobile-pool, .safety-row, .mobile-chart-panel, .pool-detail-hero, .menu-group';

/** No hidden classes or inline styles: unsupported motion always leaves content visible. */
export function mountMobileMotion(root: HTMLElement, options: { transition?: MobileTransition; preference?: MotionPreference } = {}): () => void {
  const preference = options.preference ?? loadMotionPreference();
  const previousPreference = root.dataset.motionPreference;
  const effects = new Set<Animation>();
  const revealed = new WeakSet<Element>();
  let observer: IntersectionObserver | undefined;
  let disposed = false;
  let media: MediaQueryList | undefined;
  try { media = window?.matchMedia?.('(prefers-reduced-motion: reduce)'); } catch { /* No browser/media support. */ }

  const reduce = () => prefersReducedMotion(preference);
  const stop = () => {
    observer?.disconnect();observer = undefined;
    for (const effect of effects) effect.cancel();
    effects.clear();
  };
  const updatePreference = () => {
    root.dataset.motionPreference = reduce() ? 'reduce' : 'system';
    if (reduce()) stop();
  };
  const cleanup = () => {
    if (disposed) return;
    disposed = true;stop();
    media?.removeEventListener?.('change', updatePreference);
    if (previousPreference === undefined) delete root.dataset.motionPreference;
    else root.dataset.motionPreference = previousPreference;
  };
  updatePreference();
  media?.addEventListener?.('change', updatePreference);
  if (reduce() || typeof IntersectionObserver !== 'function' || typeof root.animate !== 'function') return cleanup;

  const play = (element: HTMLElement, frames: Keyframe[], duration: number, delay = 0) => {
    if (disposed || reduce() || typeof element.animate !== 'function') return;
    try {
      const effect = element.animate(frames, { duration, delay, easing });
      effects.add(effect);
      void effect.finished.then(() => effects.delete(effect), () => effects.delete(effect));
    } catch { /* Rendering continues if a WebView does not support these keyframes. */ }
  };
  observer = new IntersectionObserver(entries => {
    if (disposed || reduce()) return;
    let stagger = 0;
    for (const entry of entries) {
      if (!entry.isIntersecting || revealed.has(entry.target)) continue;
      revealed.add(entry.target);observer?.unobserve(entry.target);
      const element = entry.target as HTMLElement;
      if (element.matches(bars)) {
        play(element, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], 480, 35);
      } else {
        play(element, [{ opacity: .45, transform: 'translateY(8px)' }, { opacity: 1, transform: 'translateY(0)' }], 320, Math.min(stagger++, 3) * 25);
      }
    }
  }, { threshold: .08 });
  root.querySelectorAll<HTMLElement>(`${reveal}, ${bars}`).forEach(element => observer?.observe(element));
  const transition = options.transition ?? 'none';
  if (transition !== 'none') {
    const offset = transition === 'back' ? '-12px' : transition === 'forward' ? '12px' : '0';
    play(root, [{ opacity: .65, transform: `translateX(${offset})` }, { opacity: 1, transform: 'translateX(0)' }], transition === 'tab' ? 150 : 210);
  }
  return cleanup;
}
