# Reuse manifest

Code adapted from AI Workflow Studio. Both repositories belong to the same owner; AIWS has no
licence file and its runtime package is marked UNLICENSED. That is fine for internal use; check
before any external distribution. Nothing is shared at runtime: QRS has its own IDs, storage,
styles and branding.

Donor: `JMBorrill/ai-workflow-studio`, commit `00cb766b24b45384d7d0073b781e45a97b82560b`
(the snapshot pinned by the Stage 1 audit).

| QRS file | Donor file | Scope copied | Local changes |
| --- | --- | --- | --- |
| `apps/studio/src/graph.ts` | `studio/src/features/editor/ops.ts` | Pure document edits: unique IDs, add, remove, connect, move | Typed ports. Replaces the existing connection *into an input* rather than *from an output*, so fan-out works. Layout is stored separately from nodes. Parameter set/clear. No tools, fixtures or node-type switch. |
| `apps/studio/src/Canvas.tsx` | `studio/src/features/editor/Canvas.tsx` | The document as source of truth, a local node copy for smooth dragging, drag-end and keyboard moves, delete keys, drag-and-drop from the palette | Generic cards drawn from operator port declarations, typed handles with connection validation, issue counts per node. No test-run overlay, icons or AIWS branding. |
| `apps/studio/src/autosave.ts`, `useStrategy.ts` | `studio/src/state/useDocument.ts`, `studio/src/features/editor/useWorkflowDoc.ts` | Debounced, serialised autosave with the base revision; delayed retry on failure; pending edits saved before switching documents | Moved out of React into a plain class with its own tests. Saves go through the Python API. A stale revision stops saving and offers Reload, instead of retrying. `flush()` waits until no edit is pending or in flight, and responses for a document that is no longer open are ignored. |
| `apps/studio/src/history.ts`, `recovery.ts` (Stage 3) | `studio/src/features/editor/useWorkflowDoc.ts` | Undo/redo over whole documents, coalescing edits with the same key within a second, at most 200 steps; failed-save recovery in `localStorage` with the base revision, cleared by a successful save | Both moved out of React into small modules with their own tests. Autosave hands the copy every unsaved edit, including edits made after a save was refused (409), which are never sent. A save clears only copies its own tab wrote, since tabs share storage. Recovery is offered whenever a kept copy differs from the opened strategy, not only at the same revision, so refused edits survive a reload; the notice says when restoring would replace later changes. |

Not reused: the AIWS schema, validator and runtime, which are rebuilt in Python (decisions 2–4);
the Node engine and Tauri wrapper; and the LLM providers, which are out of scope.
