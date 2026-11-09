// Loads the selected strategy and connects edits to revision-safe autosave (autosave.ts), undo/redo
// (history.ts) and failed-save recovery (recovery.ts). Undo and redo are edits too, so each one is
// saved as a new revision like any other change.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
import { Autosave, type SaveState } from './autosave';
import type { StrategyDocument } from './contracts.gen';
import { History } from './history';
import { clearRecovery, readRecovery, recoveryStore, type Recovery } from './recovery';

export type Update = (edit: (d: StrategyDocument) => StrategyDocument, key?: string) => void;

export function useStrategy(id: string | null) {
  const [doc, setDoc] = useState<StrategyDocument | null>(null);
  const [openedRevision, setOpenedRevision] = useState(0);
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [status, setStatus] = useState<SaveState>({ kind: 'saved' });
  const [generation, setGeneration] = useState(0);
  const history = useRef(new History<StrategyDocument>());
  const autosave = useMemo(() => new Autosave(api.save, setStatus, { unsaved: recoveryStore(localStorage) }), []);

  useEffect(() => {
    let active = true;
    history.current = new History(); // at once, so nothing undoes the previous strategy while this loads
    setDoc(null);
    setRecovery(null);
    if (id) {
      void api.strategy(id).then(async (record) => {
        if (!active) return;
        await autosave.open(record.id, record.revision, record.document);
        if (!active) return;
        setDoc(record.document);
        setOpenedRevision(record.revision);
        const kept = readRecovery(localStorage, record.id);
        // A kept copy equal to the saved document was saved after all.
        if (kept && JSON.stringify(kept.document) === JSON.stringify(record.document)) clearRecovery(localStorage, record.id);
        else setRecovery(kept);
      });
    }
    return () => {
      active = false;
    };
  }, [id, generation, autosave]);

  const apply = useCallback(
    (next: StrategyDocument) => {
      autosave.edit(next);
      setDoc(next);
    },
    [autosave],
  );

  const update = useCallback<Update>(
    (edit, key) => {
      const current = autosave.document;
      if (!current) return;
      const next = edit(current);
      if (next === current) return;
      history.current.record(current, key);
      apply(next);
    },
    [autosave, apply],
  );

  const step = useCallback(
    (direction: 'undo' | 'redo') => {
      const current = autosave.document;
      const target = current && history.current[direction](current);
      if (target) apply(target);
    },
    [autosave, apply],
  );

  const settleRecovery = (restore: boolean) => {
    if (!id || !recovery) return;
    if (restore) update(() => recovery.document);
    clearRecovery(localStorage, id);
    setRecovery(null);
  };

  return {
    doc,
    openedRevision,
    update,
    undo: useCallback(() => step('undo'), [step]),
    redo: useCallback(() => step('redo'), [step]),
    canUndo: history.current.canUndo,
    canRedo: history.current.canRedo,
    status,
    flush: () => autosave.flush(),
    reload: () => setGeneration((g) => g + 1),
    recovery,
    restoreRecovery: () => settleRecovery(true),
    discardRecovery: () => settleRecovery(false),
  };
}

export function describeSave(s: SaveState): string {
  switch (s.kind) {
    case 'saved':
      return 'Saved';
    case 'pending':
      return 'Edited';
    case 'saving':
      return 'Saving…';
    case 'stale':
      return s.message;
    case 'error':
      return `Not saved: ${s.message} Retrying.`;
  }
}
