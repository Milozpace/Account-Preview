/**
 * Merges and debounces snapshot refresh requests.
 *
 * Guarantees required by the sync probe:
 * - at most one network trigger is in flight at any time;
 * - concurrent requests while a trigger runs are merged into at most one
 *   follow-up (a manual request always earns exactly one rerun);
 * - repeated auto requests (e.g. rapid foreground switches) inside
 *   `minAutoIntervalMs` do not start new triggers.
 */
export interface RefreshControllerOptions {
  minAutoIntervalMs: number;
  trigger: () => Promise<void>;
}

export class RefreshController {
  private running = false;
  private pendingManual = false;
  private lastAutoStart = -Infinity;
  private readonly now: () => number;

  constructor(
    private readonly options: RefreshControllerOptions,
    now: () => number = () => Date.now(),
  ) {
    this.now = now;
  }

  get inFlight(): boolean {
    return this.running;
  }

  /** User action: always runs, or earns exactly one follow-up if busy. */
  requestManual(): void {
    if (this.running) {
      this.pendingManual = true;
      return;
    }
    void this.run();
  }

  /** Lifecycle action: skipped while busy or inside the debounce window. */
  requestAuto(): void {
    if (this.running) return;
    if (this.now() - this.lastAutoStart < this.options.minAutoIntervalMs) return;
    this.lastAutoStart = this.now();
    void this.run();
  }

  private async run(): Promise<void> {
    this.running = true;
    try {
      await this.options.trigger();
    } catch {
      // Trigger owners surface their own errors to the UI; a rejected
      // trigger must not become an unhandled rejection here.
    } finally {
      this.running = false;
      if (this.pendingManual) {
        this.pendingManual = false;
        this.requestManual();
      }
    }
  }
}
