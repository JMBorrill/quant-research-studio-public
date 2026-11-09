import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Autosave, type SaveState } from './autosave';
import type { StrategyDocument } from './contracts.gen';

const doc = (name: string): StrategyDocument => ({ name, nodes: [], edges: [] });

type Call = { id: string; revision: number; name: string; resolve: (revision: number) => void; reject: (e: Error & { status?: number }) => void };

function harness() {
  const calls: Call[] = [];
  const states: SaveState[] = [];
  const save = (id: string, revision: number, d: StrategyDocument) =>
    new Promise<{ revision: number }>((resolve, reject) => calls.push({ id, revision, name: d.name, resolve: (r) => resolve({ revision: r }), reject }));
  return { calls, states, autosave: new Autosave(save, (s) => states.push(s), { delay: 400, retryDelay: 3000 }) };
}

const settle = () => vi.advanceTimersByTimeAsync(0);

describe('Autosave', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('flush waits for an edit that a concurrent write picked up', async () => {
    const { calls, autosave } = harness();
    await autosave.open('a', 1, doc('v1'));
    autosave.edit(doc('edit1'));
    await vi.advanceTimersByTimeAsync(400); // autosave of edit1 is now in flight
    autosave.edit(doc('edit2'));
    let flushed: number | undefined;
    void autosave.flush().then((r) => (flushed = r));
    calls[0].resolve(2);
    await settle();
    expect(calls.map((c) => [c.name, c.revision])).toEqual([['edit1', 1], ['edit2', 2]]);
    expect(flushed).toBeUndefined(); // edit2 is not saved yet
    calls[1].resolve(3);
    await settle();
    expect(flushed).toBe(3);
  });

  it('saves a pending edit under its own strategy before switching, and ignores late responses', async () => {
    const { calls, autosave } = harness();
    await autosave.open('a', 4, doc('a'));
    autosave.edit(doc('a edited'));
    const opening = autosave.open('b', 1, doc('b')); // switch before the autosave timer fires
    await settle();
    expect(calls.map((c) => [c.id, c.revision, c.name])).toEqual([['a', 4, 'a edited']]);
    calls[0].resolve(5);
    await opening;
    autosave.edit(doc('b edited'));
    await vi.advanceTimersByTimeAsync(400);
    expect(calls[1]).toMatchObject({ id: 'b', revision: 1, name: 'b edited' });
  });

  it('stops saving after a conflict and refuses to flush', async () => {
    const { calls, states, autosave } = harness();
    await autosave.open('a', 1, doc('v1'));
    autosave.edit(doc('mine'));
    await vi.advanceTimersByTimeAsync(400);
    calls[0].reject(Object.assign(new Error('saved elsewhere'), { status: 409 }));
    await settle();
    expect(states.at(-1)).toEqual({ kind: 'stale', message: 'saved elsewhere' });
    autosave.edit(doc('more'));
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toHaveLength(1);
    await expect(autosave.flush()).rejects.toThrow('saved elsewhere');
  });

  it('retries a failed save after a delay instead of immediately', async () => {
    const { calls, autosave } = harness();
    await autosave.open('a', 1, doc('v1'));
    autosave.edit(doc('edit'));
    await vi.advanceTimersByTimeAsync(400);
    calls[0].reject(new Error('offline'));
    await settle();
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(3000);
    expect(calls).toHaveLength(2);
    const flushing = autosave.flush();
    calls[1].reject(new Error('still offline'));
    await expect(flushing).rejects.toThrow('The latest edit is not saved: still offline');
  });
});
