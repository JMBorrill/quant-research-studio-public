// Compare two or more succeeded runs on the same dataset (POST /api/compare). The service chooses the
// common window, recomputes each run's metrics over it, and lists differing costs, assumptions and
// parameters; it refuses runs it cannot compare, and this dialog shows why.
import { useEffect, useState } from 'react';
import { api, type ComparedRun, type Comparison, type StrategySummary } from './api';
import type { RunInfo } from './contracts.gen';
import { Dialog } from './Dialog';
import { LineChart, METRICS, money, SERIES } from './RunsPanel';

const show = (value: unknown) => (value === null || value === undefined ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value));

function Swatch({ series }: { series: number }) {
  return (
    <svg className="swatch" viewBox="0 0 24 8" aria-hidden="true">
      <line x1="0" x2="24" y1="4" y2="4" className="chart__line" data-series={series % SERIES} />
    </svg>
  );
}

export function Results({ comparison, onBack, onClose }: { comparison: Comparison; onBack: () => void; onClose: () => void }) {
  const { window: span, runs, parameters, mismatches } = comparison;
  const heads = runs.map((r, k) => (
    <th key={r.id} scope="col">
      {k + 1}. <Swatch series={k} /> {r.name} rev {r.revision} <code>{r.id}</code>
    </th>
  ));
  const row = (label: string, cell: (r: ComparedRun) => string) => (
    <tr key={label}>
      <th scope="row">{label}</th>
      {runs.map((r) => (
        <td key={r.id}>{cell(r)}</td>
      ))}
    </tr>
  );
  return (
    <>
      {mismatches.length > 0 && (
        <div className="notice notice--warn" role="alert">
          <b>Not like for like.</b> These settings differ, so differences in results may come from them:
          <table className="compare">
            <thead>
              <tr>
                <th scope="col">Setting</th>
                {heads}
              </tr>
            </thead>
            <tbody>
              {mismatches.map((m) => (
                <tr key={m.name}>
                  <th scope="row">{m.name}</th>
                  {m.values.map((v, k) => (
                    <td key={k}>{show(v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p>
        Common window: {span.start} to {span.end}, {span.sessions} sessions. It starts at the latest effective warm-up, so every run can have a signal
        throughout; each run's metrics are recomputed over it from its own ledger.
      </p>
      <div className="wide-table">
        <table className="compare">
          <thead>
            <tr>
              <th />
              {heads}
            </tr>
          </thead>
          <tbody>
            {row('Evidence', (r) => r.evidence_class)}
            {row('Own warm-up (sessions)', (r) => String(r.warm_up))}
            {row('Sessions excluded', (r) => String(r.excluded_sessions))}
            {row('Position at window start', (r) => String(r.position_at_start))}
            {row('Equity at window start', (r) => money(r.metrics.initial_cash))}
            {METRICS.map(([key, label, format]) => row(label, (r) => format(r.metrics[key])))}
          </tbody>
        </table>
      </div>
      <h3>Equity change since {span.start}</h3>
      <LineChart lines={runs.map((r) => r.equity_change)} baseline={0} label={`Equity change of ${runs.length} runs from ${span.start} to ${span.end}`} numbered />
      <h3>Parameter differences</h3>
      {parameters.length ? (
        <div className="wide-table">
          <table className="compare">
            <thead>
              <tr>
                <th scope="col">Node</th>
                <th scope="col">Parameter</th>
                {heads}
              </tr>
            </thead>
            <tbody>
              {parameters.map((p) => (
                <tr key={`${p.node}.${p.field}`}>
                  <td>{p.node}</td>
                  <td>{p.field}</td>
                  {p.values.map((v, k) => (
                    <td key={k}>{show(v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">None: every node has the same operator and parameters.</p>
      )}
      <div className="dialog__actions">
        <button type="button" onClick={onBack}>
          Choose other runs
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </>
  );
}

export function CompareDialog({ strategies, onClose }: { strategies: StrategySummary[]; onClose: () => void }) {
  const [runs, setRuns] = useState<RunInfo[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const names = new Map(strategies.map((s) => [s.id, s.name]));

  useEffect(() => {
    api.runs().then(
      (all) => setRuns(all.filter((r) => r.status === 'succeeded')),
      (e: Error) => setError(e.message),
    );
  }, []);

  const compare = async () => {
    setError(null);
    try {
      setComparison(await api.compare(chosen));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Dialog title="Compare runs" onClose={onClose} wide>
      {comparison ? (
        <Results comparison={comparison} onBack={() => setComparison(null)} onClose={onClose} />
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void compare();
          }}
        >
          <fieldset>
            <legend>Succeeded runs on one dataset; choose two or more</legend>
            {runs?.length === 0 && <p className="muted">No succeeded runs yet.</p>}
            {runs?.map((r) => (
              <label key={r.id} className="choice">
                <input type="checkbox" checked={chosen.includes(r.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, r.id] : chosen.filter((id) => id !== r.id))} />
                {names.get(r.strategy_id) ?? r.strategy_id} · rev {r.revision} · {new Date(r.created_at).toLocaleString('en-GB')} · run <code>{r.id}</code>
                {r.predecessor && ` · re-run of ${r.predecessor}`}
              </label>
            ))}
          </fieldset>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog__actions">
            <button type="button" onClick={onClose}>
              Close
            </button>
            <button type="submit" className="primary" disabled={chosen.length < 2}>
              Compare
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
