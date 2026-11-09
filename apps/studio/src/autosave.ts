// Revision-safe autosave for one open strategy, adapted from AI Workflow Studio's useDocument and
// kept free of React so its ordering rules can be tested directly.
//
// Edits are saved after a short pause, one write at a time, each naming the revision it was based
// on. A refused save (409: saved elsewhere) stops saving until the strategy is reopened, rather than
// overwriting newer work. Other failures keep the edit and retry after a delay. Responses that
// arrive after another strategy was opened are ignored. An optional `unsaved` hook is given the
// latest edit whenever it is known not to be saved, and told when a save succeeds (recovery.ts).
import type { StrategyDocument } from './contracts.gen';

export type SaveState = { kind: 'saved' } | { kind: 'pending' } | { kind: 'saving' } | { kind: 'stale'; message: string } | { kind: 'error'; message: string };
type Save = (id: string, revision: number, document: StrategyDocument) => Promise<{ revision: number }>;
export type Unsaved = {
  keep: (id: string, revision: number, document: StrategyDocument) => void;
  /** A save succeeded. */
  forget: (id: string) => void;
  /** The strategy was opened: copies kept before now are the user's to restore or discard. */
  opened: (id: string) => void;
};
type Options = { delay?: number; retryDelay?: number; unsaved?: Unsaved };

export class Autosave {
  private id: string | null = null;
  private revision = 0;
  private latest: StrategyDocument | null = null;
  private dirty = false;
  private stale = false;
  private failure: string | null = null;
  private inFlight: Promise<void> | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private readonly delay: number;
  private readonly retryDelay: number;
  private readonly unsaved?: Unsaved;

  constructor(
    private save: Save,
    private onStatus: (s: SaveState) => void,
    { delay = 400, retryDelay = 3000, unsaved }: Options = {},
  ) {
    this.delay = delay;
    this.retryDelay = retryDelay;
    this.unsaved = unsaved;
  }

  get document(): StrategyDocument | null {
    return this.latest;
  }

  /** Switch to another strategy after sending any pending edit of the current one. */
  async open(id: string, revision: number, document: StrategyDocument): Promise<void> {
    clearTimeout(this.timer);
    await this.write();
    Object.assign(this, { id, revision, latest: document, dirty: false, stale: false, failure: null });
    this.unsaved?.opened(id);
    this.onStatus({ kind: 'saved' });
  }

  edit(document: StrategyDocument): void {
    if (!this.id) return;
    this.latest = document;
    this.dirty = true;
    // While saves are refused or failing, this edit is not saved yet either.
    if (this.stale || this.failure) this.unsaved?.keep(this.id, this.revision, document);
    this.failure = null;
    if (!this.stale) this.onStatus({ kind: 'pending' });
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.write(), this.delay);
  }

  /** Save every edit made so far and return the revision that holds them. */
  async flush(): Promise<number> {
    clearTimeout(this.timer);
    this.failure = null;
    // Another write may pick up the pending edit while this one waits, so repeat until nothing is
    // pending or in flight.
    while (this.dirty || this.inFlight) {
      if (this.stale) throw new Error('The strategy was saved elsewhere; reload it before continuing.');
      if (this.failure) throw new Error(`The latest edit is not saved: ${this.failure}`);
      await this.write();
    }
    return this.revision;
  }

  private async write(): Promise<void> {
    if (this.inFlight) await this.inFlight;
    const { id, latest } = this;
    // After a failure only an edit, the retry timer or flush() asks for another attempt.
    if (!id || !latest || !this.dirty || this.stale || this.failure) return;
    this.dirty = false;
    this.onStatus({ kind: 'saving' });
    const saving = this.save(id, this.revision, latest).then(
      (saved) => {
        if (this.id !== id) return;
        this.revision = saved.revision;
        this.unsaved?.forget(id);
        if (!this.dirty) this.onStatus({ kind: 'saved' });
      },
      (e: Error & { status?: number }) => {
        if (this.id !== id) return;
        this.dirty = true;
        this.unsaved?.keep(id, this.revision, this.latest!);
        if (e.status === 409) {
          this.stale = true;
          this.onStatus({ kind: 'stale', message: e.message });
        } else {
          this.failure = e.message;
          this.onStatus({ kind: 'error', message: e.message });
          this.timer = setTimeout(() => {
            this.failure = null;
            void this.write();
          }, this.retryDelay);
        }
      },
    );
    this.inFlight = saving;
    await saving;
    this.inFlight = null;
    // An edit made while saving is written straight away; failures wait for the retry timer.
    if (this.dirty && !this.stale && !this.failure) void this.write();
  }
}
