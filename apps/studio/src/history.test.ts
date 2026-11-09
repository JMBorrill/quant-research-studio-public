import { describe, expect, it } from 'vitest';
import { History } from './history';

function clock() {
  let t = 0;
  return { now: () => t, advance: (ms: number) => (t += ms) };
}

describe('History', () => {
  it('undoes and redoes whole steps in order', () => {
    const history = new History<string>();
    history.record('a');
    history.record('b'); // the document is now 'c'
    expect(history.undo('c')).toBe('b');
    expect(history.undo('b')).toBe('a');
    expect(history.canUndo).toBe(false);
    expect(history.undo('a')).toBeUndefined();
    expect(history.redo('a')).toBe('b');
    expect(history.redo('b')).toBe('c');
    expect(history.canRedo).toBe(false);
  });

  it('coalesces edits with the same key within a second into one step', () => {
    const time = clock();
    const history = new History<string>(time.now);
    history.record('', 'param:ema_1:period'); // typing 2, 20, 200 in one field
    time.advance(300);
    history.record('2', 'param:ema_1:period');
    time.advance(300);
    history.record('20', 'param:ema_1:period');
    expect(history.undo('200')).toBe('');
    expect(history.canUndo).toBe(false);
  });

  it('starts a new step after a pause, for another key, or for an unkeyed edit', () => {
    const time = clock();
    const history = new History<string>(time.now);
    history.record('a', 'name');
    time.advance(1000);
    history.record('b', 'name'); // a pause of a second
    history.record('c', 'param:x:k'); // another field
    history.record('d'); // e.g. adding a node
    history.record('e');
    expect(['f', 'e', 'd', 'c', 'b'].map((current) => history.undo(current))).toEqual(['e', 'd', 'c', 'b', 'a']);
  });

  it('does not coalesce across an undo, and a new edit clears redo', () => {
    const time = clock();
    const history = new History<string>(time.now);
    history.record('a', 'name');
    expect(history.undo('ab')).toBe('a');
    history.record('a', 'name'); // same key straight after undo: still a new step
    expect(history.canRedo).toBe(false);
    expect(history.undo('ax')).toBe('a');
    expect(history.canUndo).toBe(false);
  });

  it('keeps the latest 200 steps', () => {
    const history = new History<number>();
    for (let i = 0; i < 250; i++) history.record(i);
    let current = 250;
    let steps = 0;
    for (let previous = history.undo(current); previous !== undefined; previous = history.undo(current)) {
      current = previous;
      steps++;
    }
    expect([steps, current]).toEqual([200, 50]);
  });
});
