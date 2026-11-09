// Experiments over the open strategy (/api/experiments): choose the parameters and their values, the
// search, the objective and the holdout start; follow the trials as they finish; rank them and compare
// the top ones in the compare view; cancel; and evaluate the holdout after a confirmation, seeing every
// evaluation and whether it was a repeat.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type Comparison, type ExperimentOverview, type ExperimentSummary, type HoldoutEvaluation } from './api';
import { Results } from './CompareDialog';
import type { OperatorInfo, StrategyDocument } from './contracts.gen';
import { Dialog } from './Dialog';
import { type AxisText, buildExperiment, earlierEvaluations, type Form, holdoutCandidate, isActive, MAX_TRIALS, OBJECTIVES, parseValues, searchable, trialCount } from './experiment';
import { METRICS, money } from './RunsPanel';

const describe = (values: ExperimentSummary['trials'][number]['values']) => values.map((v) => `${v.node}.${v.param} = ${String(v.value)}`).join(', ');
const counts = (experiment: ExperimentOverview) => {
  const total = Object.values(experiment.counts).reduce((sum, n) => sum + n, 0);
  const parts = Object.entries(experiment.counts).map(([status, n]) => `${n} ${status}`);
  return `${total} trials: ${parts.join(', ')}`;
};
const metric = (key: string) => METRICS.find(([k]) => k === key);

export function ExperimentDialog({
  strategyId,
  doc,
  ops,
  flush,
  onClose,
}: {
  strategyId: string;
  doc: StrategyDocument;
  ops: OperatorInfo[];
  flush: () => Promise<number>;
  onClose: () => void;
}) {
  const [experiments, setExperiments] = useState<ExperimentOverview[] | null>(null);
  const [view, setView] = useState<string>('list'); // 'list', 'new' or an experiment ID
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(
    () =>
      api.experiments(strategyId).then(
        (listed) => {
          setExperiments(listed);
          setError(null);
        },
        (e: Error) => setError(e.message),
      ),
    [strategyId],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const back = () => {
    setView('list');
    void refresh();
  };

  return (
    <Dialog title="Experiments" onClose={onClose} wide>
      {view === 'new' ? (
        <NewExperiment strategyId={strategyId} doc={doc} ops={ops} flush={flush} onCreated={setView} onBack={back} />
      ) : view !== 'list' ? (
        <ExperimentView key={view} id={view} onBack={back} onClose={onClose} />
      ) : (
        <>
          {experiments?.length === 0 && <p className="muted">No experiments on this strategy yet.</p>}
          {!!experiments?.length && (
            <table className="compare">
              <thead>
                <tr>
                  <th scope="col">Experiment</th>
                  <th scope="col">Trials</th>
                  <th scope="col">Status</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {experiments.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <button type="button" className="link" onClick={() => setView(e.id)}>
                        {e.experiment.name}
                      </button>{' '}
                      <code>{e.id}</code>
                      {e.exploratory && <small className="muted"> exploratory</small>}
                    </td>
                    <td>{counts(e)}</td>
                    <td>{e.status}</td>
                    <td>{new Date(e.created_at).toLocaleString('en-GB')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog__actions">
            <button type="button" onClick={onClose}>
              Close
            </button>
            <button type="button" className="primary" onClick={() => setView('new')}>
              New experiment…
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function NewExperiment({
  strategyId,
  doc,
  ops,
  flush,
  onCreated,
  onBack,
}: {
  strategyId: string;
  doc: StrategyDocument;
  ops: OperatorInfo[];
  flush: () => Promise<number>;
  onCreated: (id: string) => void;
  onBack: () => void;
}) {
  const parameters = searchable(doc, ops);
  const [form, setForm] = useState<Form>({
    name: `${doc.name} experiment`,
    axes: [{ key: '', text: '' }],
    search: 'grid',
    trials: 20,
    seed: 1,
    objective: 'net_pnl',
    direction: 'maximize',
    holdoutStart: '',
    exploratory: false,
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const change = (changes: Partial<Form>) => setForm({ ...form, ...changes });
  const setAxis = (k: number, axis: Partial<AxisText>) => change({ axes: form.axes.map((a, i) => (i === k ? { ...a, ...axis } : a)) });
  const listed = form.axes.map((a) => {
    const parameter = parameters.find((p) => `${p.node}.${p.param}` === a.key);
    const values = parameter ? parseValues(a.text, parameter.type) : [];
    return typeof values === 'string' ? 0 : values.length;
  });
  const count = trialCount(form.search, listed, form.trials);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const experiment = buildExperiment(form, parameters, strategyId, await flush());
      if (typeof experiment === 'string') setError(experiment);
      else onCreated((await api.createExperiment(experiment)).id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label>
        Name
        <input value={form.name} onChange={(e) => change({ name: e.target.value })} />
      </label>
      <fieldset>
        <legend>Parameters and the values to try, separated by commas</legend>
        {form.axes.map((axis, k) => (
          <div key={k} className="axis">
            <select aria-label={`Parameter ${k + 1}`} value={axis.key} onChange={(e) => setAxis(k, { key: e.target.value })}>
              <option value="">Choose a parameter…</option>
              {parameters.map((p) => (
                <option key={`${p.node}.${p.param}`} value={`${p.node}.${p.param}`}>
                  {p.node}.{p.param}
                </option>
              ))}
            </select>
            <input aria-label={`Values ${k + 1}`} value={axis.text} placeholder="10, 20, 30" onChange={(e) => setAxis(k, { text: e.target.value })} />
            <button type="button" aria-label={`Remove parameter ${k + 1}`} onClick={() => change({ axes: form.axes.filter((_, i) => i !== k) })}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" onClick={() => change({ axes: [...form.axes, { key: '', text: '' }] })}>
          Add parameter
        </button>
      </fieldset>
      <label>
        Search
        <select value={form.search} onChange={(e) => change({ search: e.target.value as Form['search'] })}>
          <option value="grid">Grid: every combination</option>
          <option value="random">Random: distinct combinations drawn with a seed</option>
        </select>
      </label>
      {form.search === 'random' && (
        <>
          <label>
            Trials
            <input type="number" min={1} max={MAX_TRIALS} value={form.trials} onChange={(e) => change({ trials: Number(e.target.value) })} />
          </label>
          <label>
            Seed
            <input type="number" value={form.seed} onChange={(e) => change({ seed: Number(e.target.value) })} />
          </label>
        </>
      )}
      <label>
        Objective
        <select value={form.objective} onChange={(e) => change({ objective: e.target.value as Form['objective'] })}>
          {OBJECTIVES.map((o) => (
            <option key={o} value={o}>
              {metric(o)?.[1] ?? o}
            </option>
          ))}
        </select>
      </label>
      <label>
        Direction
        <select value={form.direction} onChange={(e) => change({ direction: e.target.value as Form['direction'] })}>
          <option value="maximize">Maximise</option>
          <option value="minimize">Minimise</option>
        </select>
      </label>
      <label>
        Holdout start
        <input type="date" value={form.holdoutStart} onChange={(e) => change({ holdoutStart: e.target.value })} />
        <small>Trials read only the sessions before it; the holdout is evaluated only when you ask.</small>
      </label>
      <label className="checkbox">
        <input type="checkbox" checked={form.exploratory} onChange={(e) => change({ exploratory: e.target.checked })} />
        Exploratory analysis
        <small>
          Needed when the strategy reads exploratory data, such as features a model extracted after the fact. Its ranking and holdout are labelled exploratory and
          never counted as verified.
        </small>
      </label>
      <p className="muted">
        {count} trial{count === 1 ? '' : 's'}; at most {MAX_TRIALS}. They run one at a time in the run queue.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog__actions">
        <button type="button" onClick={onBack} disabled={busy}>
          Back
        </button>
        <button type="submit" className="primary" disabled={busy}>
          Run experiment
        </button>
      </div>
    </form>
  );
}

/** A holdout window's evaluations of one kind, in every experiment: verified and exploratory are
 * never listed together. */
function WindowEvaluations({ kind, evaluations }: { kind: 'verified' | 'exploratory'; evaluations: HoldoutEvaluation[] }) {
  if (!evaluations.length) return null;
  return (
    <table className="compare" aria-label={kind === 'verified' ? 'Verified evaluations of this holdout' : 'Exploratory evaluations of this holdout'}>
      <caption>{kind === 'verified' ? 'Verified' : 'Exploratory (never counted as verified)'}</caption>
      <thead>
        <tr>
          <th scope="col">Experiment</th>
          <th scope="col">Trial</th>
          <th scope="col">Requested</th>
          <th scope="col">Net P&amp;L</th>
        </tr>
      </thead>
      <tbody>
        {evaluations.map((h) => (
          <tr key={h.run.id}>
            <td>{h.experiment_name}</td>
            <td>{h.trial}</td>
            <td>{new Date(h.requested_at).toLocaleString('en-GB')}</td>
            <td>{h.result ? money(h.result.metrics.net_pnl) : h.run.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ExperimentView({ id, onBack, onClose }: { id: string; onBack: () => void; onClose: () => void }) {
  const [summary, setSummary] = useState<ExperimentSummary | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [holdoutTrial, setHoldoutTrial] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null); // the trial, fixed when the confirmation opens
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false); // an action is waiting for the service
  const polling = useRef(false);
  const acting = useRef(false); // no poll is sent while an action waits, so its answer is the newest
  const latest = useRef(0); // the newest request: an answer to an older one is not shown
  const show = useCallback(async (request: Promise<ExperimentSummary>) => {
    const sent = ++latest.current;
    const next = await request;
    if (sent === latest.current) setSummary(next);
  }, []);
  const act = async (action: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    acting.current = true;
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      acting.current = false;
      setBusy(false);
    }
  };
  const refresh = useCallback(async () => {
    if (polling.current || acting.current) return; // a request is still answering
    polling.current = true;
    try {
      await show(api.experiment(id));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      polling.current = false;
    }
  }, [id, show]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const active = !!summary && isActive(summary);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refresh(), 1000);
    return () => clearInterval(timer);
  }, [active, refresh]);

  if (comparison) return <Results comparison={comparison} onBack={() => setComparison(null)} onClose={onClose} />;
  if (!summary)
    return (
      <>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )}
        <div className="dialog__actions">
          <button type="button" onClick={onBack}>
            All experiments
          </button>
        </div>
      </>
    );
  const { experiment, ranking } = summary;
  const ranked = ranking && 'runs' in ranking ? ranking.runs : [];
  const candidate = holdoutCandidate(summary, holdoutTrial);
  const objective = metric(experiment.objective);

  return (
    <>
      <p>
        <b>{experiment.name}</b> · {experiment.search === 'grid' ? 'grid' : `random, ${experiment.trials} trials with seed ${experiment.seed}`} ·{' '}
        {experiment.direction === 'maximize' ? 'maximising' : 'minimising'} {experiment.objective} · holdout from {experiment.holdout_start} ·{' '}
        <span className="status">{summary.status}</span> · {counts(summary)}
      </p>
      {summary.exploratory && (
        <p className="notice notice--warn" role="note">
          Exploratory analysis: this experiment may read exploratory data, so its ranking and holdout are labelled exploratory and never counted as verified.
        </p>
      )}
      <h3>Trials</h3>
      <div className="wide-table">
        <table className="compare" aria-label="Trials">
          <thead>
            <tr>
              <th scope="col">Trial</th>
              <th scope="col">Values</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {summary.trials.map((t) => (
              <tr key={t.trial}>
                <td>{t.trial}</td>
                <td>{describe(t.values)}</td>
                <td>
                  {t.run.status}
                  {t.run.error && <span className="muted"> · {t.run.error}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Ranking</h3>
      {ranking && 'error' in ranking && <p className="error">{ranking.error}</p>}
      {ranking && 'runs' in ranking && (
        <>
          {ranking.mismatches.length > 0 && (
            <p className="notice notice--warn" role="alert">
              <b>Not like for like.</b> These settings differ between trials, so the ranking may reflect them: {ranking.mismatches.map((m) => m.name).join(', ')}.
              Compare trials to see each value.
            </p>
          )}
          <p className="muted">
            Over {ranking.window.start} to {ranking.window.end}, {ranking.window.sessions} sessions, from the latest warm-up. Best of {ranked.length} ranked trials
            ({summary.trials.length} in all): the best of many is optimistic, and the holdout is the check.
          </p>
          <div className="wide-table">
            <table className="compare" aria-label="Ranking">
              <thead>
                <tr>
                  <th scope="col">Compare</th>
                  <th scope="col">Rank</th>
                  <th scope="col">Trial</th>
                  <th scope="col">Values</th>
                  <th scope="col">{objective?.[1] ?? experiment.objective} (objective)</th>
                  <th scope="col">Evidence</th>
                  {METRICS.slice(0, 3).map(([key, label]) => (
                    <th key={key} scope="col">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ranked.map((r, k) => (
                  <tr key={r.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Compare trial ${r.trial}`}
                        checked={chosen.includes(r.id)}
                        onChange={(e) => setChosen(e.target.checked ? [...chosen, r.id] : chosen.filter((c) => c !== r.id))}
                      />
                    </td>
                    <td>{k + 1}</td>
                    <td>{r.trial}</td>
                    <td>{describe(summary.trials.find((t) => t.trial === r.trial)!.values)}</td>
                    <td>{(objective?.[2] ?? String)(r.metrics[experiment.objective])}</td>
                    <td>{r.evidence_class}</td>
                    {METRICS.slice(0, 3).map(([key, , format]) => (
                      <td key={key}>{format(r.metrics[key])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button type="button" disabled={chosen.length < 2 || busy} onClick={() => void act(async () => setComparison(await api.compare(chosen)))}>
            Compare selected
          </button>
        </>
      )}
      {!ranking && <p className="muted">No trial has succeeded yet.</p>}
      <h3>Holdout</h3>
      {summary.holdout.length > 0 && (
        <table className="compare" aria-label="Holdout evaluations">
          <thead>
            <tr>
              <th scope="col">Trial</th>
              <th scope="col">Requested</th>
              <th scope="col">Status</th>
              <th scope="col">Kind</th>
              <th scope="col">Repeat</th>
              <th scope="col">Holdout window</th>
              <th scope="col">Net P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {summary.holdout.map((h) => (
              <tr key={h.run.id}>
                <td>{h.trial}</td>
                <td>{new Date(h.requested_at).toLocaleString('en-GB')}</td>
                <td>{h.run.status}</td>
                <td>{h.exploratory ? 'Exploratory' : 'Verified'}</td>
                <td>{earlierEvaluations(h)}</td>
                <td>{h.window ? `${h.window.start} to ${h.window.end}, ${h.window.sessions} sessions` : '—'}</td>
                <td>{h.result ? money(h.result.metrics.net_pnl) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {summary.window.verified.length + summary.window.exploratory.length > summary.holdout.length && (
        <>
          <p>This holdout, from {summary.window.holdout_start} on the same bars, was also evaluated in other experiments:</p>
          {(['verified', 'exploratory'] as const).map((kind) => (
            <WindowEvaluations key={kind} kind={kind} evaluations={summary.window[kind]} />
          ))}
        </>
      )}
      {summary.holdout.length > 0 && <p className="muted">Net P&amp;L is measured from the last close before the holdout, with the position held then carried in.</p>}
      {confirming !== null ? (
        <div className="notice notice--warn" role="group" aria-label="Confirm holdout evaluation">
          Evaluate trial {confirming} on the holdout from {experiment.holdout_start}? It runs on data no trial has read. Every evaluation is recorded, and any after the
          first is flagged as a repeat.{' '}
          <button
            type="button"
            className="primary"
            onClick={() =>
              void act(async () => {
                const trial = confirming;
                setConfirming(null);
                await show(api.evaluateHoldout(id, trial));
              })
            }
          >
            Evaluate holdout
          </button>{' '}
          <button type="button" autoFocus onClick={() => setConfirming(null)}>
            Not now
          </button>
        </div>
      ) : candidate !== null ? (
        <p>
          <label>
            Trial{' '}
            <select value={candidate} onChange={(e) => setHoldoutTrial(Number(e.target.value))}>
              {ranked.map((r) => (
                <option key={r.trial} value={r.trial}>
                  {r.trial}
                </option>
              ))}
            </select>
          </label>{' '}
          <button type="button" disabled={busy} onClick={() => setConfirming(candidate)}>
            Evaluate holdout…
          </button>
        </p>
      ) : (
        summary.status === 'running' && <p className="muted">The holdout can be evaluated once every trial has finished or the experiment is cancelled.</p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog__actions">
        <button type="button" onClick={onBack}>
          All experiments
        </button>
        {summary.status === 'running' && (
          <button type="button" disabled={busy} onClick={() => void act(() => show(api.cancelExperiment(id)))}>
            Cancel experiment
          </button>
        )}
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </>
  );
}
