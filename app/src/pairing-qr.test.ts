import { describe, expect, it } from 'vitest';
import { PairingQrController } from './pairing-qr';

function deferred() {
  let resolve!: (dataUri: string) => void;
  const promise = new Promise<string>((complete) => { resolve = complete; });
  return { promise, resolve };
}

function fixture() {
  let visible = true;
  let image: string | undefined;
  const shown: string[] = [];
  const controller = new PairingQrController(
    () => visible,
    (dataUri) => { image = dataUri; shown.push(dataUri); },
    () => { image = undefined; },
  );
  return {
    controller, shown,
    setVisible: (value: boolean) => { visible = value; },
    image: () => image,
  };
}

describe('ephemeral pairing QR lifecycle', () => {
  it('shows a completed native QR while the same request remains visible', async () => {
    const view = fixture();
    const native = deferred();
    const completion = view.controller.display(() => native.promise);
    native.resolve('data:image/svg+xml;base64,synthetic-current');
    expect(await completion).toBe(true);
    expect(view.image()).toBe('data:image/svg+xml;base64,synthetic-current');
    view.controller.hide();
    expect(view.image()).toBeUndefined();
  });

  it('does not reveal a delayed QR after backgrounding even when foregrounded again', async () => {
    const view = fixture();
    const native = deferred();
    const completion = view.controller.display(() => native.promise);
    view.setVisible(false);
    view.controller.hide();
    view.setVisible(true);
    native.resolve('data:image/svg+xml;base64,synthetic-stale');
    expect(await completion).toBe(false);
    expect(view.shown).toEqual([]);
    expect(view.image()).toBeUndefined();
  });

  it('invalidates a pending QR on explicit hide or pagehide while visibility still reports true', async () => {
    const view = fixture();
    const native = deferred();
    const completion = view.controller.display(() => native.promise);
    view.controller.hide();
    native.resolve('data:image/svg+xml;base64,synthetic-hidden');
    expect(await completion).toBe(false);
    expect(view.shown).toEqual([]);
  });

  it('checks current visibility immediately before revealing a completed native QR', async () => {
    const view = fixture();
    const native = deferred();
    const completion = view.controller.display(() => native.promise);
    view.setVisible(false);
    native.resolve('data:image/svg+xml;base64,synthetic-background');
    expect(await completion).toBe(false);
    expect(view.shown).toEqual([]);
  });

  it('replaced requests cannot reveal stale QR data after the latest request completed', async () => {
    const view = fixture();
    const older = deferred();
    const newer = deferred();
    const oldCompletion = view.controller.display(() => older.promise);
    const newCompletion = view.controller.display(() => newer.promise);
    newer.resolve('data:image/svg+xml;base64,synthetic-new');
    expect(await newCompletion).toBe(true);
    older.resolve('data:image/svg+xml;base64,synthetic-old');
    expect(await oldCompletion).toBe(false);
    expect(view.shown).toEqual(['data:image/svg+xml;base64,synthetic-new']);
    expect(view.image()).toBe('data:image/svg+xml;base64,synthetic-new');
  });
});
