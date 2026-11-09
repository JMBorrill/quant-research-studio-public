// Run history and results for one strategy. Runs live in the service, so they survive the browser
// being closed; this view only reads them.
import { useEffect, useState } from 'react';
import { api, type BacktestOutput, type RunResult } from './api';
import type { RunInfo } from './contracts.gen';

export const money = (v: number) => v.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (v: number) => `${v.toFixed(2)}%`;
const short = (hash?: string | null) => (hash ? hash.slice(0, 12) : '—');

export const METRICS: [string, string, (v: number) => string][] = [
  ['net_pnl', 'Net P&L', money],
  ['return_pct', 'Return', pct],
  ['gross_pnl', 'Gross P&L', money],
  ['commission', 'Commission', money],
  ['slippage', 'Slippage', money],
  ['max_drawdown', 'Max drawdown', money],
  ['max_drawdown_pct', 'Max drawdown %', pct],
  ['max_drawdown_sessions', 'Longest drawdown (sessions)', String],
  ['exposure_pct', 'Exposure', pct],
  ['fills', 'Fills', String],
  ['round_trips', 'Round trips', String],
  ['final_position', 'Final position', String],
  ['final_equity', 'Final equity', money],
];

/** Lines over the same sessions on one scale, with a dashed baseline. Lines differ in colour and dash;
 *  `numbered` also writes each line's number at its end, side by side, so any number of lines can be
 *  told apart even where they end together. */
export function LineChart({ lines, baseline, label, numbered }: { lines: number[][]; baseline: number; label: string; numbered?: boolean }) {
  const values = lines.flat();
  const lo = Math.min(baseline, ...values);
  const hi = Math.max(baseline, ...values);
  const sessions = Math.max(...lines.map((l) => l.length));
  const x = (i: number) => (i / Math.max(1, sessions - 1)) * 600;
  const y = (v: number) => 150 - ((v - lo) / (hi - lo || 1)) * 140;
  return (
    <svg className="chart" viewBox="0 0 600 160" role="img" aria-label={label}>
      <line x1="0" x2="600" y1={y(baseline)} y2={y(baseline)} className="chart__base" />
      {lines.map((line, k) => (
        <polyline key={k} points={line.map((v, i) => `${x(i)},${y(v)}`).join(' ')} className="chart__line" data-series={k % SERIES} />
      ))}
      {numbered &&
        lines.map(
          (line, k) =>
            line.length > 0 && (
              <text key={k} x={x(line.length - 1) - 10 * (lines.length - 1 - k)} y={y(line[line.length - 1])} dy={-4} textAnchor="end" className="chart__label">
                {k + 1}
              </text>
            ),
        )}
    </svg>
  );
}

/** How many distinct line styles the chart has; further lines reuse them. */
export const SERIES = 4;

function Result({ output }: { output: BacktestOutput }) {
  return (
    <>
      <dl className="metrics">
        {METRICS.map(([key, label, format]) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>{format(output.metrics[key])}</dd>
          </div>
        ))}
      </dl>
      <h3>Equity</h3>
      <LineChart
        lines={[output.equity.map((r) => r.equity)]}
        baseline={output.metrics.initial_cash}
        label={`Equity from ${output.equity[0]?.session} to ${output.equity.at(-1)?.session}`}
      />
      <h3>Fills</h3>
      <div className="table">
        <table>
          <thead>
            <tr>
              <th>Session</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Commission</th>
              <th>Slippage</th>
            </tr>
          </thead>
          <tbody>
            {output.fills.map((f, i) => (
              <tr key={i}>
                <td>{f.session}</td>
                <td>{f.side}</td>
                <td>{f.quantity}</td>
                <td>{f.price}</td>
                <td>{money(f.commission)}</td>
                <td>{money(f.slippage)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        {output.first_signal_session && `Signals possible from ${output.first_signal_session}, after a ${output.warm_up_sessions}-session warm-up. `}
        {String(output.assumptions.fills)}
      </p>
    </>
  );
}

export function RunsPanel({
  runs,
  selected,
  onSelect,
  onCancel,
  onRerun,
}: {
  runs: RunInfo[];
  selected: string | null;
  onSelect: (id: string) => void;
  onCancel: (id: string) => void;
  onRerun: (id: string) => void;
}) {
  const run = runs.find((r) => r.id === selected);
  const [result, setResult] = useState<RunResult | null>(null);
  useEffect(() => {
    setResult(null);
    if (run?.status === 'succeeded') void api.result(run.id).then(setResult);
  }, [run?.id, run?.status]);

  return (
    <section className="runs" aria-label="Runs">
      <h2>Runs</h2>
      {!runs.length && <p className="muted">No runs yet.</p>}
      <ul className="runs__list">
        {runs.map((r) => (
          <li key={r.id}>
            <button className="run" data-active={r.id === selected || undefined} onClick={() => onSelect(r.id)}>
              <span className="status" data-status={r.status}>
                {r.status}
              </span>
              <span>rev {r.revision}</span>
              <span className="muted">
                {new Date(r.created_at).toLocaleString('en-GB')}
                {r.predecessor && ` · re-run of ${r.predecessor}`}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {run && (
        <div className="run__detail">
          <p className="hashes">
            Code <code>{short(run.code_sha256)}</code> · Result <code>{short(run.result_sha256)}</code> · <a href={api.logUrl(run.id)} target="_blank" rel="noreferrer">Log</a>
          </p>
          <button onClick={() => onRerun(run.id)}>Re-run</button>
          {(run.status === 'queued' || run.status === 'running') && <button onClick={() => onCancel(run.id)}>Cancel run</button>}
          {run.error && <p className="error">{run.error}</p>}
          {result && <p className="muted">Evidence: {result.evidence_class}, the weakest class among the datasets this run read.</p>}
          {result && Object.entries(result.results).map(([node, output]) => <Result key={node} output={output} />)}
        </div>
      )}
    </section>
  );
}
