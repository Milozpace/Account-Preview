import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RefreshController } from './refresh';

describe('RefreshController', () => {
  let clock: number;

  function makeController(
    trigger: () => Promise<void>,
    minAutoIntervalMs = 10_000,
  ): { controller: RefreshController; tick: (ms: number) => void } {
    const controller = new RefreshController(
      { minAutoIntervalMs, trigger },
      () => clock,
    );
    return {
      controller,
      tick: (ms: number) => {
        clock += ms;
      },
    };
  }

  beforeEach(() => {
    clock = 1_000;
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function deferred(): { promise: Promise<void>; resolve: () => void } {
    let resolve!: () => void;
    const promise = new Promise<void>((done) => {
      resolve = done;
    });
    return { promise, resolve };
  }

  it('runs a manual request immediately', async () => {
    const gate = deferred();
    const trigger = vi.fn(async () => {
      await gate.promise;
    });
    const { controller } = makeController(trigger);
    controller.requestManual();
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(1);
    expect(controller.inFlight).toBe(true);
    gate.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.inFlight).toBe(false);
  });

  it('merges concurrent manual requests into exactly one follow-up', async () => {
    const gates = [deferred(), deferred()];
    let call = 0;
    const trigger = vi.fn(async () => {
      await gates[Math.min(call++, gates.length - 1)].promise;
    });
    const { controller } = makeController(trigger);
    controller.requestManual();
    controller.requestManual();
    controller.requestManual();
    gates[0].resolve();
    await vi.advanceTimersByTimeAsync(0);
    // First run finished; the three queued manuals collapse into one rerun.
    expect(trigger).toHaveBeenCalledTimes(2);
    expect(controller.inFlight).toBe(true);
    gates[1].resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(2);
    expect(controller.inFlight).toBe(false);
  });

  it('does not stack follow-ups from repeated manual requests', async () => {
    const gate = deferred();
    const trigger = vi.fn(async () => {
      await gate.promise;
    });
    const { controller } = makeController(trigger);
    controller.requestManual();
    controller.requestManual();
    gate.resolve();
    await vi.advanceTimersByTimeAsync(0);
    controller.requestManual();
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(3);
  });

  it('drops auto requests inside the debounce window', async () => {
    const trigger = vi.fn(async () => undefined);
    const { controller, tick } = makeController(trigger, 10_000);
    controller.requestAuto();
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(1);
    tick(3_000);
    controller.requestAuto();
    controller.requestAuto();
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(1);
    tick(7_001);
    controller.requestAuto();
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(2);
  });

  it('does not schedule an auto request behind an in-flight run', async () => {
    const gate = deferred();
    const trigger = vi.fn(async () => {
      await gate.promise;
    });
    const { controller, tick } = makeController(trigger, 10_000);
    controller.requestAuto();
    tick(20_000);
    controller.requestAuto();
    gate.resolve();
    await vi.advanceTimersByTimeAsync(0);
    // The second auto call arrived while the first was running; it must not
    // queue another trigger.
    expect(trigger).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(1);
  });

  it('a trigger error does not block later requests', async () => {
    let fail = true;
    const trigger = vi.fn(async () => {
      if (fail) throw new Error('network down');
    });
    const { controller } = makeController(trigger);
    controller.requestManual();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.inFlight).toBe(false);
    fail = false;
    controller.requestManual();
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(2);
    expect(controller.inFlight).toBe(false);
  });

  it('a manual request during an in-flight auto run earns one rerun', async () => {
    const gate = deferred();
    const trigger = vi.fn(async () => {
      await gate.promise;
    });
    const { controller, tick } = makeController(trigger, 10_000);
    controller.requestAuto();
    gate.resolve();
    tick(1_000);
    controller.requestManual();
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(0);
    expect(trigger).toHaveBeenCalledTimes(2);
  });
});
