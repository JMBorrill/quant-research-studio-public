// Undo/redo over whole documents, adapted from AI Workflow Studio's useWorkflowDoc and kept free of
// React so it can be tested directly. Edits that carry the same key within a second (typing in one
// field, nudging a node with the arrow keys) coalesce into one step.
const COALESCE_MS = 1000;
const LIMIT = 200;

export class History<T> {
  private past: T[] = [];
  private future: T[] = [];
  private last: { key?: string; at: number } = { at: 0 };

  constructor(private now: () => number = Date.now) {}

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  /** Record that an edit replaced `previous`. */
  record(previous: T, key?: string): void {
    const at = this.now();
    if (!(key && key === this.last.key && at - this.last.at < COALESCE_MS)) {
      this.past.push(previous);
      if (this.past.length > LIMIT) this.past.shift();
    }
    this.last = { key, at };
    this.future = [];
  }

  /** The document before the last step, or undefined if there is none. */
  undo(current: T): T | undefined {
    return this.step(this.past, this.future, current);
  }

  /** The document the last undo replaced, or undefined if there is none. */
  redo(current: T): T | undefined {
    return this.step(this.future, this.past, current);
  }

  private step(from: T[], to: T[], current: T): T | undefined {
    const target = from.pop();
    if (target === undefined) return undefined;
    to.push(current);
    this.last = { at: 0 }; // the next edit starts a new step
    return target;
  }
}
