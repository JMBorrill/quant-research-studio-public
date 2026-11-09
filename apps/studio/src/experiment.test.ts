import { describe, expect, it } from 'vitest';
import type { OperatorInfo, StrategyDocument } from './contracts.gen';
import type { ExperimentSummary } from './api';
import { buildExperiment, earlierEvaluations, type Form, holdoutCandidate, isActive, parseValues, searchable, trialCount } from './experiment';

const ops: OperatorInfo[] = [
  {
    id: 'qrs.dataset',
    version: 1,
    title: 'Dataset',
    description: '',
    family: 'sources',
    alignment: 'same_index',
    inputs: [],
    outputs: [{ name: 'bars', type: 'bars' }],
    params_schema: { properties: { dataset_id: { type: 'string', 'x-qrs-dataset': 'qrs/dataset' } } },
  },
  {
    id: 'qrs.ema',
    version: 1,
    title: 'EMA',
    description: '',
    family: 'indicators',
    alignment: 'same_index',
    inputs: [{ name: 'bars', type: 'bars' }],
    outputs: [{ name: 'series', type: 'series' }],
    params_schema: { properties: { period: { type: 'integer' }, column: { type: 'string', enum: ['open', 'close'] } } },
  },
  {
    id: 'qrs.threshold',
    version: 1,
    title: 'Threshold',
    description: '',
    family: 'rules',
    alignment: 'same_index',
    inputs: [{ name: 'a', type: 'series' }],
    outputs: [{ name: 'signal', type: 'signal' }],
    params_schema: { properties: { value: { type: 'number' }, strict: { type: 'boolean' } } },
  },
];
const doc: StrategyDocument = {
  name: 'test',
  nodes: [
    { id: 'bars', operator: 'qrs.dataset', version: 1, params: {} },
    { id: 'fast', operator: 'qrs.ema', version: 1, params: {} },
    { id: 'entry', operator: 'qrs.threshold', version: 1, params: {} },
  ],
  edges: [],
};
const parameters = searchable(doc, ops);
const form = (changes: Partial<Form> = {}): Form => ({
  name: 'Periods',
  axes: [{ key: 'fast.period', text: '10, 20' }],
  search: 'grid',
  trials: 10,
  seed: 1,
  objective: 'net_pnl',
  direction: 'maximize',
  holdoutStart: '2025-01-02',
  exploratory: false,
  ...changes,
});

describe('experiment form', () => {
  it('offers every node parameter but dataset references', () => {
    expect(parameters.map((p) => `${p.node}.${p.param}:${p.type}`)).toEqual(['fast.period:integer', 'fast.column:string', 'entry.value:number', 'entry.strict:boolean']);
  });

  it('types listed values as their parameter, and says why it cannot', () => {
    expect(parseValues(' 10, 20 ,30 ', 'integer')).toEqual([10, 20, 30]);
    expect(parseValues('0.5, 1e-3', 'number')).toEqual([0.5, 0.001]);
    expect(parseValues('true,false', 'boolean')).toEqual([true, false]);
    expect(parseValues('open, close', 'string')).toEqual(['open', 'close']);
    expect(parseValues('10, 2.5', 'integer')).toBe('2.5 is not a whole number.');
    expect(parseValues('x', 'number')).toBe('x is not a number.');
    expect(parseValues('yes', 'boolean')).toBe('yes is not true or false.');
    expect(parseValues(' , ', 'integer')).toBe('List at least one value.');
  });

  it('counts a grid as the product of its value counts, and a random search as its trials', () => {
    expect(trialCount('grid', [2, 3, 4], 5)).toBe(24);
    expect(trialCount('random', [2, 3, 4], 5)).toBe(5);
  });

  it('builds the request a grid or a random search needs', () => {
    expect(buildExperiment(form({ axes: [{ key: 'fast.period', text: '10, 20' }, { key: 'entry.value', text: '0.5' }] }), parameters, 's1', 3)).toEqual({
      name: 'Periods',
      strategy_id: 's1',
      revision: 3,
      search: 'grid',
      axes: [
        { node: 'fast', param: 'period', values: [10, 20] },
        { node: 'entry', param: 'value', values: [0.5] },
      ],
      objective: 'net_pnl',
      direction: 'maximize',
      holdout_start: '2025-01-02',
      exploratory: false,
    });
    expect(buildExperiment(form({ search: 'random', trials: 1, seed: 7 }), parameters, 's1', 3)).toMatchObject({ search: 'random', trials: 1, seed: 7 });
    expect(buildExperiment(form({ exploratory: true }), parameters, 's1', 3)).toMatchObject({ exploratory: true });
  });

  it('says what an incomplete form lacks, and leaves the rest to the service', () => {
    expect(buildExperiment(form({ axes: [] }), parameters, 's1', 3)).toBe('Choose at least one parameter.');
    expect(buildExperiment(form({ holdoutStart: '' }), parameters, 's1', 3)).toBe('Choose the holdout start.');
    expect(buildExperiment(form({ axes: [{ key: '', text: '1' }] }), parameters, 's1', 3)).toBe('Choose a parameter for every row.');
    expect(buildExperiment(form({ axes: [{ key: 'fast.period', text: '1.5' }] }), parameters, 's1', 3)).toBe('fast.period: 1.5 is not a whole number.');
    // The service refuses a search over 200 trials, with its own message.
    expect(buildExperiment(form({ search: 'random', trials: 201 }), parameters, 's1', 3)).toMatchObject({ trials: 201 });
  });
});

describe('holdout evaluation', () => {
  // The trials' statuses, and the trials ranked so far, best first.
  const summary = (statuses: string[], ranked: number[]): ExperimentSummary =>
    ({
      trials: statuses.map((status) => ({ run: { status } })),
      ranking: ranked.length ? { window: { start: '', end: '', sessions: 0 }, runs: ranked.map((trial) => ({ trial })), mismatches: [] } : null,
    }) as unknown as ExperimentSummary;

  it('is offered only once the ranking can no longer change', () => {
    expect(holdoutCandidate(summary(['succeeded', 'succeeded', 'running'], [3, 1]), null)).toBeNull();
    expect(holdoutCandidate(summary(['succeeded', 'queued'], [1]), null)).toBeNull();
    expect(holdoutCandidate(summary(['failed', 'failed'], []), null)).toBeNull();
    expect(holdoutCandidate(summary(['succeeded', 'failed', 'succeeded'], [3, 1]), null)).toBe(3); // the best ranked
    expect(holdoutCandidate(summary(['succeeded', 'cancelled', 'succeeded'], [3, 1]), 1)).toBe(1); // or the one chosen
  });
});

describe('earlier evaluations of a holdout window', () => {
  it('says how many of each kind came before, or that it was the first', () => {
    expect(earlierEvaluations({ repeat: false, earlier: { verified: 0, exploratory: 0 } })).toBe('First evaluation');
    expect(earlierEvaluations({ repeat: true, earlier: { verified: 1, exploratory: 0 } })).toBe('Repeat: 1 verified evaluation of this holdout came before');
    expect(earlierEvaluations({ repeat: true, earlier: { verified: 2, exploratory: 1 } })).toBe('Repeat: 2 verified and 1 exploratory evaluations of this holdout came before');
    expect(earlierEvaluations({ repeat: true, earlier: { verified: 0, exploratory: 3 } })).toBe('Repeat: 3 exploratory evaluations of this holdout came before');
    // A store from before Phase 4B keeps its flag, which counted one experiment only.
    expect(earlierEvaluations({ repeat: false, earlier: { verified: 2, exploratory: 0 } })).toBe('2 verified evaluations of this holdout came before');
  });
});

describe('following an experiment', () => {
  const runs = (trials: string[], holdout: string[] = []) =>
    ({ trials: trials.map((status) => ({ run: { status } })), holdout: holdout.map((status) => ({ run: { status } })) }) as unknown as ExperimentSummary;

  it('goes on while any trial or holdout run is queued or running, whatever the experiment says', () => {
    expect(isActive(runs(['cancelled', 'running']))).toBe(true); // cancelled, its running trial still stopping
    expect(isActive(runs(['succeeded', 'queued']))).toBe(true);
    expect(isActive(runs(['succeeded', 'failed'], ['queued']))).toBe(true);
    expect(isActive(runs(['succeeded', 'cancelled'], ['succeeded']))).toBe(false);
  });
});
