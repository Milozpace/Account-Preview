import { describe, expect, it, vi } from 'vitest';
import { createDrawerMotion } from './motion';

function drawerFixture(reduced = false) {
  const effects: { finished: Promise<void>; finish: () => void; cancel: ReturnType<typeof vi.fn> }[] = [];
  const dialog = {
    open: false,
    showModal() { this.open = true; },
    close: vi.fn(function(this: { open: boolean }) { this.open = false; }),
    animate: vi.fn(() => {
      let finish!: () => void;
      const finished = new Promise<void>((resolve) => { finish = resolve; });
      const effect = { finished, finish, cancel: vi.fn(finish) };
      effects.push(effect);
      return effect;
    }),
  };
  return { dialog, effects, motion: createDrawerMotion(dialog as unknown as HTMLDialogElement, () => reduced) };
}

describe('detail panel transitions', () => {
  it('keeps the modal open until its exit animation completes', async () => {
    const { dialog, effects, motion } = drawerFixture();
    motion.open();
    const closing = motion.close();
    expect(dialog.open).toBe(true);
    effects.at(-1)!.finish();await closing;
    expect(dialog.open).toBe(false);
  });

  it('cannot close a newly reopened panel through an old animation', async () => {
    const { dialog, effects, motion } = drawerFixture();
    motion.open();const closing = motion.close();
    const oldExit = effects.at(-1)!;
    motion.open();oldExit.finish();await closing;
    expect(dialog.open).toBe(true);
    expect(dialog.close).not.toHaveBeenCalled();
  });

  it('closes immediately for reduced motion and cancels a forced close', async () => {
    const reduced = drawerFixture(true);
    reduced.motion.open();await reduced.motion.close();
    expect(reduced.dialog.open).toBe(false);
    expect(reduced.dialog.animate).not.toHaveBeenCalled();
    const normal = drawerFixture();
    normal.motion.open();await normal.motion.close(true);
    expect(normal.dialog.open).toBe(false);
    expect(normal.effects[0].cancel).toHaveBeenCalled();
  });

  it('still closes when rendering stalls without finishing the animation', async () => {
    vi.useFakeTimers();
    try {
      const { dialog, motion } = drawerFixture();
      motion.open();const closing = motion.close();
      await vi.advanceTimersByTimeAsync(350);
      expect(dialog.open).toBe(false);
      await closing;
    } finally { vi.useRealTimers(); }
  });
});
