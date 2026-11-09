// Failed-save recovery, adapted from AI Workflow Studio's useWorkflowDoc. While saves are failing or
// refused, Autosave hands each unsaved edit to this store, which copies it to browser storage with
// the revision it was based on; a later successful save removes the copy. When the strategy is next
// opened, the studio offers to restore it, so edits survive a closed browser or a save refused
// because the strategy changed elsewhere.
import type { Unsaved } from './autosave';
import type { StrategyDocument } from './contracts.gen';

export type Recovery = { baseRevision: number; savedAt: string; document: StrategyDocument };
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

const key = (id: string) => `qrs.recovery.${id}`;

/** Copies in `store`. A save removes only copies this tab wrote since it opened the strategy: tabs
 *  share browser storage, and a copy offered for restoring stays until the user decides. */
export function recoveryStore(store: Store, now: () => Date = () => new Date()): Unsaved {
  const written = new Set<string>();
  return {
    keep(id, baseRevision, document) {
      try {
        store.setItem(key(id), JSON.stringify({ baseRevision, savedAt: now().toISOString(), document } satisfies Recovery));
        written.add(id);
      } catch {
        // Storage full or unavailable: the edit is still kept in memory and retried.
      }
    },
    forget(id) {
      if (written.delete(id)) store.removeItem(key(id));
    },
    opened(id) {
      written.delete(id);
    },
  };
}

export function readRecovery(store: Store, id: string): Recovery | null {
  try {
    return JSON.parse(store.getItem(key(id)) ?? 'null') as Recovery | null;
  } catch {
    return null;
  }
}

export const clearRecovery = (store: Store, id: string) => store.removeItem(key(id));
