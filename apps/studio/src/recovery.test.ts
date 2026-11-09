import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Autosave } from './autosave';
import type { StrategyDocument } from './contracts.gen';
import { clearRecovery, readRecovery, recoveryStore } from './recovery';

const doc = (name: string): StrategyDocument => ({ name, nodes: [], edges: [] });
const NOW = new Date('2026-10-31T12:00:00Z');
const REFUSED = Object.assign(new Error('saved elsewhere'), { status: 409 });

function memoryStore() {
  const items = new Map<string, string>();
  return { getItem: (k: string) => items.get(k) ?? null, setItem: (k: string, v: string) => void items.set(k, v), removeItem: (k: string) => void items.delete(k) };
}

/** A studio (one tab): an Autosave with browser recovery, whose saves the test answers. */
function studio(store = memoryStore()) {
  const answers: ((error?: Error) => void)[] = [];
  let revision = 1;
  const save = () => new Promise<{ revision: number }>((resolve, reject) => answers.push((error) => (error ? reject(error) : resolve({ revision: ++revision }))));
  const autosave = new Autosave(save, () => {}, { delay: 400, retryDelay: 3000, unsaved: recoveryStore(store, () => NOW) });
  return { store, answers, autosave };
}

/** Make an edit and answer the save it triggers. */
async function editAndAnswer(tab: ReturnType<typeof studio>, name: string, error?: Error) {
  tab.autosave.edit(doc(name));
  await vi.advanceTimersByTimeAsync(400);
  tab.answers.at(-1)!(error);
  await vi.advanceTimersByTimeAsync(0);
}

describe('failed-save recovery', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('keeps an edit whose save failed, with its base revision, until a save succeeds', async () => {
    const tab = studio();
    await tab.autosave.open('a', 1, doc('v1'));
    await editAndAnswer(tab, 'mine', new Error('offline'));
    expect(readRecovery(tab.store, 'a')).toEqual({ baseRevision: 1, savedAt: NOW.toISOString(), document: doc('mine') });
    tab.autosave.edit(doc('mine, then more')); // before the retry
    expect(readRecovery(tab.store, 'a')?.document).toEqual(doc('mine, then more'));
    await vi.advanceTimersByTimeAsync(400);
    tab.answers.at(-1)!();
    await vi.advanceTimersByTimeAsync(0);
    expect(readRecovery(tab.store, 'a')).toBeNull();
  });

  it('keeps every edit made after a save was refused, though nothing more is sent', async () => {
    const tab = studio();
    await tab.autosave.open('a', 4, doc('v4'));
    await editAndAnswer(tab, 'mine', REFUSED);
    tab.autosave.edit(doc('mine, and more after the refusal'));
    await vi.advanceTimersByTimeAsync(5000);
    expect(tab.answers).toHaveLength(1);
    expect(readRecovery(tab.store, 'a')).toMatchObject({ baseRevision: 4, document: doc('mine, and more after the refusal') });
  });

  it("does not drop a copy when another tab saves, or when this tab saves before it is restored", async () => {
    const shared = memoryStore();
    const first = studio(shared);
    const second = studio(shared);
    await first.autosave.open('a', 1, doc('v1'));
    await second.autosave.open('a', 1, doc('v1'));
    await editAndAnswer(first, 'first');
    await editAndAnswer(second, 'second', REFUSED);
    await editAndAnswer(first, 'first again'); // another tab's successful save
    expect(readRecovery(shared, 'a')?.document).toEqual(doc('second'));

    // Reopened (Reload) with the copy offered, an edit saved before the user decides keeps it too.
    await second.autosave.open('a', 3, doc('first again'));
    await editAndAnswer(second, 'an edit before deciding');
    expect(readRecovery(shared, 'a')?.document).toEqual(doc('second'));

    // A copy left by an earlier session stays until the user restores or discards it.
    const later = studio(shared);
    await later.autosave.open('a', 3, doc('first again'));
    await editAndAnswer(later, 'an unrelated edit');
    expect(readRecovery(shared, 'a')?.document).toEqual(doc('second'));
    clearRecovery(shared, 'a');
    expect(readRecovery(shared, 'a')).toBeNull();
  });

  it('survives unreadable or unwritable storage', async () => {
    const store = memoryStore();
    store.setItem('qrs.recovery.b', '{not json');
    expect(readRecovery(store, 'b')).toBeNull();
    const full = { ...store, setItem: () => { throw new Error('QuotaExceededError'); } };
    const tab = studio(full);
    await tab.autosave.open('a', 1, doc('v1'));
    await editAndAnswer(tab, 'mine', new Error('offline')); // no exception escapes
    expect(readRecovery(store, 'a')).toBeNull();
  });
});
