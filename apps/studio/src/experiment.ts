// The experiment dialog's own logic, apart from its rendering: which parameters a search can vary,
// the typed values listed for each, how many trials that makes, and the request the service takes.
import { ACTIVE_STATUSES, type ExperimentSummary, type HoldoutEvaluation } from './api';
import type { Experiment, OperatorInfo, StrategyDocument } from './contracts.gen';
import { findOperator } from './graph';

export const MAX_TRIALS = 200; // shown to the user; the service enforces it
export const OBJECTIVES: Experiment['objective'][] = ['net_pnl', 'return_pct', 'max_drawdown', 'max_drawdown_pct', 'exposure_pct', 'round_trips', 'final_equity'];

export type Parameter = { node: string; param: string; type: 'integer' | 'number' | 'boolean' | 'string' };
export type AxisText = { key: string; text: string }; // key is "node.param"

type Schema = { type?: string; 'x-qrs-dataset'?: string };

/** Each node parameter a search can vary, with its type; dataset references are left out. */
export function searchable(doc: StrategyDocument, ops: OperatorInfo[]): Parameter[] {
  return (doc.nodes ?? []).flatMap((node) => {
    const op = findOperator(ops, node.operator, node.version);
    const properties = (op?.params_schema.properties ?? {}) as Record<string, Schema>;
    return Object.entries(properties)
      .filter(([, schema]) => !schema['x-qrs-dataset'] && ['integer', 'number', 'boolean', 'string'].includes(schema.type ?? ''))
      .map(([param, schema]) => ({ node: node.id, param, type: schema.type as Parameter['type'] }));
  });
}

/** The comma-separated values in `text`, typed as the parameter is, or why they are not. The
 * service checks the rest, such as repeated values and the trial limit. */
export function parseValues(text: string, type: Parameter['type']): unknown[] | string {
  const items = text
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  if (!items.length) return 'List at least one value.';
  const values: unknown[] = [];
  for (const item of items) {
    if (type === 'integer' || type === 'number') {
      const value = Number(item);
      if (!Number.isFinite(value) || (type === 'integer' && !Number.isInteger(value))) return `${item} is not ${type === 'integer' ? 'a whole number' : 'a number'}.`;
      values.push(value);
    } else if (type === 'boolean') {
      if (item !== 'true' && item !== 'false') return `${item} is not true or false.`;
      values.push(item === 'true');
    } else values.push(item);
  }
  return values;
}

/** How many trials the search makes: every grid point, or the random trials asked for. */
export function trialCount(search: Experiment['search'], valueCounts: number[], randomTrials: number): number {
  return search === 'grid' ? valueCounts.reduce((total, count) => total * count, 1) : randomTrials;
}

export type Form = {
  name: string;
  axes: AxisText[];
  search: Experiment['search'];
  trials: number;
  seed: number;
  objective: Experiment['objective'];
  direction: Experiment['direction'];
  holdoutStart: string;
  exploratory: boolean;
};

/** The request for the form, or the first reason the form is incomplete. */
export function buildExperiment(form: Form, parameters: Parameter[], strategyId: string, revision: number): Experiment | string {
  if (!form.axes.length) return 'Choose at least one parameter.';
  if (!form.holdoutStart) return 'Choose the holdout start.';
  const axes = [];
  for (const axis of form.axes) {
    const parameter = parameters.find((p) => `${p.node}.${p.param}` === axis.key);
    if (!parameter) return `Choose a parameter for every row.`;
    const values = parseValues(axis.text, parameter.type);
    if (typeof values === 'string') return `${axis.key}: ${values}`;
    axes.push({ node: parameter.node, param: parameter.param, values });
  }
  const random = form.search === 'random' ? { trials: form.trials, seed: form.seed } : {};
  return {
    name: form.name || 'Experiment',
    strategy_id: strategyId,
    revision,
    search: form.search,
    axes: axes as Experiment['axes'],
    objective: form.objective,
    direction: form.direction,
    holdout_start: form.holdoutStart,
    exploratory: form.exploratory,
    ...random,
  };
}

/** Whether any trial or holdout run is queued or running. After a cancel the experiment is
 * cancelled while its running trial is still being stopped, so the view follows the runs. */
export function isActive(summary: ExperimentSummary): boolean {
  return [...summary.trials.map((t) => t.run), ...summary.holdout.map((h) => h.run)].some((run) => ACTIVE_STATUSES.has(run.status));
}

/** The trial a holdout evaluation would run: the one chosen, else the best ranked. None while any
 * trial is queued or running, since the ranking can still change (the service refuses it then),
 * and before any trial is ranked. */
export function holdoutCandidate(summary: ExperimentSummary, chosen: number | null): number | null {
  const ranked = summary.ranking && 'runs' in summary.ranking ? summary.ranking.runs : [];
  if (summary.trials.some((t) => ACTIVE_STATUSES.has(t.run.status)) || !ranked.length) return null;
  return chosen ?? ranked[0].trial;
}

/** What came before a holdout evaluation: how many evaluations of its window, of each kind, or
 * that it was the first. Counts show even beside no repeat flag, which a store from before
 * Phase 4B keeps from when repeats were counted within one experiment. */
export function earlierEvaluations(h: Pick<HoldoutEvaluation, 'repeat' | 'earlier'>): string {
  const { verified, exploratory } = h.earlier;
  const counts = [verified && `${verified} verified`, exploratory && `${exploratory} exploratory`].filter(Boolean).join(' and ');
  if (!counts) return 'First evaluation';
  const before = `${counts} evaluation${verified + exploratory === 1 ? '' : 's'} of this holdout came before`;
  return h.repeat ? `Repeat: ${before}` : before;
}
