import { useCallback, useEffect, useState } from 'react';
import { ACTIVE_STATUSES, api, ApiError, type Compiled, type Dataset, type ListedDataset, type StrategySummary, type Template } from './api';
import { Canvas } from './Canvas';
import { CompareDialog } from './CompareDialog';
import { ExperimentDialog } from './ExperimentDialog';
import type { Issue, OperatorInfo, RunInfo } from './contracts.gen';
import { findOperator, removeNodes, setParam } from './graph';
import { ImportDialog } from './ImportDialog';
import { NewStrategyDialog } from './NewStrategyDialog';
import { ParamsForm } from './ParamsForm';
import { ResearchDialog } from './ResearchDialog';
import { RunsPanel } from './RunsPanel';
import { describeSave, useStrategy } from './useStrategy';

type Message = { kind: 'error' | 'info'; text: string; issues?: Issue[] };

export function App() {
  const [ops, setOps] = useState<OperatorInfo[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [datasets, setDatasets] = useState<ListedDataset[]>([]);
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(location.hash.slice(1) || null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<Compiled | null>(null);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const [dialog, setDialog] = useState<'new' | 'import' | 'compare' | 'experiments' | 'research' | null>(null);
  const { doc, openedRevision, update, undo, redo, canUndo, canRedo, status, flush, reload, recovery, restoreRecovery, discardRecovery } = useStrategy(strategyId);

  useEffect(() => {
    void Promise.all([api.operators(), api.templates(), api.datasets(), api.strategies()]).then(([o, t, d, s]) => {
      setOps(o);
      setTemplates(t);
      setDatasets(d);
      setStrategies(s);
      setStrategyId((current) => current ?? s[0]?.id ?? null);
    });
  }, []);

  useEffect(() => {
    if (strategyId) history.replaceState(null, '', `#${strategyId}`);
    setSelectedNode(null);
    setSelectedRun(null);
    setCompiled(null); // the previous strategy's issues and warm-ups no longer apply
  }, [strategyId]);

  // Validate and compile on the server after each pause in editing.
  useEffect(() => {
    if (!doc) return;
    const timer = setTimeout(() => void api.compile(doc).then(setCompiled), 300);
    return () => clearTimeout(timer);
  }, [doc]);

  // Undo and redo shortcuts, except in fields and dialogs, where the browser's own undo applies.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || (e.target as HTMLElement).closest('input, textarea, select, dialog')) return;
      const key = e.key.toLowerCase();
      if (key === 'z' && !e.shiftKey) undo();
      else if ((key === 'z' && e.shiftKey) || key === 'y') redo();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [undo, redo]);

  const refreshRuns = useCallback(() => {
    if (strategyId) void api.runs(strategyId).then(setRuns);
  }, [strategyId]);
  useEffect(refreshRuns, [refreshRuns]);
  useEffect(() => {
    if (!runs.some((r) => ACTIVE_STATUSES.has(r.status))) return;
    const timer = setInterval(refreshRuns, 1000);
    return () => clearInterval(timer);
  }, [runs, refreshRuns]);

  const act = async (action: () => Promise<void>) => {
    setMessage(null);
    try {
      await action();
    } catch (e) {
      const detail = e instanceof ApiError ? (e.detail as { issues?: Issue[] } | undefined) : undefined;
      setMessage({ kind: 'error', text: (e as Error).message, issues: detail?.issues });
    }
  };

  const create = (templateId: string | null, chosen: Record<string, string>) =>
    act(async () => {
      setDialog(null); // first, so an error shows on the page rather than behind the closed dialog
      const created = templateId ? await api.createFromTemplate(templateId, chosen) : await api.createStrategy({ name: 'Untitled strategy', nodes: [], edges: [] });
      setStrategies(await api.strategies());
      setStrategyId(created.id);
    });

  const imported = async (dataset: Dataset) => {
    setDialog(null);
    setDatasets(await api.datasets());
    const report = dataset.import_report;
    const missing = report ? ` ${report.missing_sessions.length} expected sessions are missing (${report.calendar}); they are reported, never filled.` : '';
    setMessage({ kind: 'info', text: `Imported ${dataset.name}: ${dataset.rows} sessions from ${dataset.start} to ${dataset.end}.${missing}` });
  };

  const run = () =>
    act(async () => {
      const started = await api.startRun(strategyId!, await flush());
      setSelectedRun(started.id);
      refreshRuns();
    });

  const exportBundle = () =>
    act(async () => {
      location.href = api.exportUrl(strategyId!, await flush());
    });

  const node = doc?.nodes?.find((n) => n.id === selectedNode);
  const op = node && findOperator(ops, node.operator, node.version);
  const issues = compiled?.issues ?? [];

  return (
    <div className="app">
      <header className="bar">
        <strong className="brand">Quant Research Studio</strong>
        <select aria-label="Strategy" value={strategyId ?? ''} onChange={(e) => setStrategyId(e.target.value || null)}>
          {!strategyId && <option value="">No strategy</option>}
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.id})
            </option>
          ))}
        </select>
        <button onClick={() => setDialog('new')}>New strategy…</button>
        <button onClick={() => setDialog('import')}>Import CSV…</button>
        <button onClick={() => setDialog('compare')}>Compare runs…</button>
        <button onClick={() => setDialog('research')}>Research…</button>
        <button onClick={() => setDialog('experiments')} disabled={!doc}>
          Experiments…
        </button>
        {doc && <input aria-label="Strategy name" className="name" value={doc.name} onChange={(e) => update((d) => ({ ...d, name: e.target.value }), 'name')} />}
        <span className="save" data-status={status.kind} role={status.kind === 'stale' || status.kind === 'error' ? 'alert' : undefined}>
          {describeSave(status)}
          {status.kind === 'stale' && <button onClick={() => void reload()}>Reload</button>}
        </span>
        <button onClick={undo} disabled={!canUndo} aria-keyshortcuts="Control+Z Meta+Z">
          Undo
        </button>
        <button onClick={redo} disabled={!canRedo} aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z Control+Y">
          Redo
        </button>
        <span className="spacer" />
        <button className="primary" onClick={() => void run()} disabled={!doc || issues.length > 0}>
          Run
        </button>
        <button onClick={() => void exportBundle()} disabled={!doc || issues.length > 0}>
          Export Python
        </button>
      </header>

      {!datasets.length && (
        <p className="notice">
          No datasets yet. Import a CSV file, or run <code>qrs demo</code> to create the synthetic dataset and an EMA crossover strategy.
        </p>
      )}
      {recovery && (
        <div className="notice notice--warn" role="alert">
          Edits from {new Date(recovery.savedAt).toLocaleString('en-GB')}, based on revision {recovery.baseRevision}, could not be saved.
          {recovery.baseRevision < openedRevision && ` The strategy has since been saved as revision ${openedRevision}; restoring replaces those changes.`}{' '}
          <button onClick={restoreRecovery}>Restore</button> <button onClick={discardRecovery}>Discard</button>
        </div>
      )}
      {message && (
        <div className={`notice notice--${message.kind}`} role={message.kind === 'error' ? 'alert' : 'status'}>
          {message.text}
          {message.issues?.map((i, k) => (
            <div key={k}>
              {i.node && <b>{i.node}: </b>}
              {i.message}
            </div>
          ))}
        </div>
      )}

      <main className="main">
        {doc ? (
          <Canvas doc={doc} ops={ops} issues={issues} warmUp={compiled?.warm_up ?? null} selectedId={selectedNode} onSelect={setSelectedNode} update={update} />
        ) : (
          <div className="canvas canvas--empty">{!strategyId && <p className="muted">Choose New strategy… to start from a template or an empty canvas.</p>}</div>
        )}
        <aside className="side">
          {node ? (
            <section aria-label="Node">
              <h2>
                {op?.title ?? node.operator} <code>{node.id}</code>
              </h2>
              {op ? (
                <>
                  <p className="muted">{op.description}</p>
                  <ParamsForm schema={op.params_schema} params={node.params} datasets={datasets} onChange={(name, value) => update((d) => setParam(d, node.id, name, value), `param:${node.id}:${name}`)} />
                </>
              ) : (
                <p className="error">Operator {node.operator}@{node.version} is not registered.</p>
              )}
              <button
                onClick={() => {
                  update((d) => removeNodes(d, [node.id]));
                  setSelectedNode(null);
                }}
              >
                Delete node
              </button>
            </section>
          ) : (
            <section aria-label="Strategy">
              <h2>Strategy</h2>
              {!compiled ? (
                <p className="muted">Checking…</p>
              ) : issues.length ? (
                <ul className="issues">
                  {issues.map((i, k) => (
                    <li key={k}>
                      {i.node && <b>{i.node}: </b>}
                      {i.message}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">Valid. Compiled code <code>{compiled?.code_sha256?.slice(0, 12)}</code></p>
              )}
              {compiled?.code && (
                <details>
                  <summary>Generated Python</summary>
                  <pre className="code">{compiled.code}</pre>
                </details>
              )}
            </section>
          )}
          <RunsPanel
            runs={runs}
            selected={selectedRun}
            onSelect={setSelectedRun}
            onCancel={(id) =>
              void act(async () => {
                await api.cancel(id);
                refreshRuns();
              })
            }
            onRerun={(id) =>
              void act(async () => {
                setSelectedRun((await api.rerun(id)).id);
                refreshRuns();
              })
            }
          />
        </aside>
      </main>

      {dialog === 'new' && <NewStrategyDialog templates={templates} datasets={datasets} onClose={() => setDialog(null)} onCreate={(t, d) => void create(t, d)} />}
      {dialog === 'compare' && <CompareDialog strategies={strategies} onClose={() => setDialog(null)} />}
      {dialog === 'experiments' && doc && strategyId && (
        <ExperimentDialog
          strategyId={strategyId}
          doc={doc}
          ops={ops}
          flush={flush}
          onClose={() => {
            setDialog(null);
            refreshRuns(); // trials and holdout evaluations are runs of this strategy
          }}
        />
      )}
      {dialog === 'research' && <ResearchDialog datasets={datasets} onClose={() => setDialog(null)} />}
      {dialog === 'import' && <ImportDialog onClose={() => setDialog(null)} onImported={(d) => void act(() => imported(d))} />}
    </div>
  );
}
