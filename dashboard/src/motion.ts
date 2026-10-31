export const reducedMotion = () => (typeof document!=='undefined'&&document.documentElement?.dataset.layout==='desktop'&&document.documentElement.dataset.motionPreference==='reduce')||window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const easing = 'cubic-bezier(.22, 1, .36, 1)';

export function createDrawerMotion(dialog: HTMLDialogElement, reduce = reducedMotion) {
  let effect: Animation | undefined;
  let revision = 0;
  const cancel = () => { effect?.cancel();effect = undefined; };
  return {
    open() {
      revision++;cancel();
      if (!dialog.open) dialog.showModal();
      if (!reduce() && dialog.animate) {
        effect = dialog.animate([{ opacity: .4, transform: 'translateX(24px)' }, { opacity: 1, transform: 'translateX(0)' }], { duration: 260, easing });
        void effect.finished.catch(() => {});
      }
    },
    async close(immediate = false) {
      const current = ++revision;cancel();
      if (!dialog.open) return;
      if (!immediate && !reduce() && dialog.animate) {
        effect = dialog.animate([{ opacity: 1, transform: 'translateX(0)' }, { opacity: 0, transform: 'translateX(20px)' }], { duration: 170, easing: 'ease-in' });
        let timeout: ReturnType<typeof setTimeout> | undefined;
        try {
          await Promise.race([effect.finished, new Promise<void>((resolve) => { timeout = setTimeout(resolve, 300); })]);
        } catch { /* A newer open/close owns the panel. */ }
        finally { clearTimeout(timeout); }
      }
      if (current === revision) { dialog.close();cancel(); }
    },
  };
}

export function mountMotion(root: HTMLElement): () => void {
  if (reducedMotion() || !('IntersectionObserver' in window) || !root.animate) return () => {};
  const effects = new Set<Animation>();
  const play = (element: HTMLElement, frames: Keyframe[], duration: number, delay = 0) => {
    if (reducedMotion()) return;
    const effect = element.animate(frames, { duration, delay, easing });
    effects.add(effect);
    void effect.finished.then(() => effects.delete(effect), () => effects.delete(effect));
  };
  const observer = new IntersectionObserver((entries) => {
    let stagger = 0;
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const element = entry.target as HTMLElement;
      observer.unobserve(element);
      if (element.matches('.progress-track i, .pool-track i, .category-track i')) {
        play(element, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], 580, 50);
      } else {
        play(element, [{ opacity: .35, transform: 'translateY(10px)' }, { opacity: 1, transform: 'translateY(0)' }], 420, Math.min(stagger++, 3) * 35);
      }
    }
  }, { threshold: .08 });
  root.querySelectorAll<HTMLElement>('.overview-card, .rhythm-summary, .operation-panel, .pool-band, .surface, .progress-track i, .pool-track i, .category-track i').forEach((element) => observer.observe(element));
  return () => { observer.disconnect();for (const effect of effects) effect.cancel();effects.clear(); };
}
