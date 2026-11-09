import { describe, expect, it } from 'vitest';
import type { MethodInfo } from './contracts.gen';
import { listText, parseList } from './ParamsForm';
import { analysisDocument, emptyInput, evaluating, familyLabel, horizonText, hypothesisIdProblem, kindsFor, missing, outcomeText, testPeriodClean } from './research';

const meanTest: MethodInfo = {
  id: 'qrs.mean_test',
  version: 1,
  title: 'Mean test',
  description: '',
  inputs: { values: ['bars', 'observations', 'equity', 'record'] },
  outputs: {},
  tests: 'directional',
  params_schema: {},
};

describe('analysis documents', () => {
  it('are built from the form, leaving out empty dates', () => {
    const inputs = { values: { ...emptyInput('bars'), dataset: 'sha256:x' } } as const;
    expect(analysisDocument(meanTest, ' ', { ...inputs }, { level: 0.9 }, { until: '', project: 'p1' })).toEqual({
      name: 'Mean test',
      method: 'qrs.mean_test',
      method_version: 1,
      inputs,
      params: { level: 0.9 },
      project: 'p1',
    });
  });
  it('list the fields still empty', () => {
    expect(missing({ values: emptyInput('observations') })).toEqual(['values.dataset', 'values.column', 'values.on']);
    expect(missing({ values: emptyInput('model') })).toEqual(['values.run', 'values.node', 'values.term']);
    expect(missing({ values: emptyInput('fills') })).toEqual(['values.run']);
    expect(missing({ values: { kind: 'equity', run: 'abc' } })).toEqual([]);
  });
  it('offer an experiment trial only to a hypothesis, for an equity input', () => {
    expect(kindsFor(meanTest, 'values')).not.toContain('trial');
    expect(kindsFor(meanTest, 'values', true)).toContain('trial');
  });
});

describe('projects and families', () => {
  it('describe read horizons and whether the test period is clean', () => {
    expect(horizonText({ reads: 0, read_to: null, full: false, test_period_read: false })).toBe('never read');
    expect(horizonText({ reads: 2, read_to: '2025-06-02', full: false, test_period_read: false })).toBe('read to 2025-06-02 (2 reads)');
    const full = { reads: 1, read_to: null, full: true, test_period_read: true };
    expect(horizonText(full)).toBe('read in full (1 reads): test period read');
    expect(testPeriodClean({ a: full })).toBe(false);
  });
  it('label families and their outcomes', () => {
    const family = { id: 'f', confirmatory: true, compromised: false, complete: true, outcomes: [] };
    expect(familyLabel(family)).toBe('confirmatory');
    expect(familyLabel({ ...family, compromised: true })).toMatch(/^compromised/);
    expect(familyLabel({ ...family, confirmatory: false })).toBe('post hoc');
    expect(outcomeText({ hypothesis: 'h1', state: 'evaluated', estimate: 0.1, p_value: 0.004, adjusted_p_value: 0.008, supported: true })).toBe(
      'h1: evaluated, p = 0.00400, adjusted 0.00800: supported',
    );
    expect(outcomeText({ hypothesis: 'h2', state: 'withdrawn', estimate: null, p_value: 1 })).toBe('h2: withdrawn, p = 1.00');
  });
});

describe('forms', () => {
  it('check hypothesis IDs and notice evaluations still running', () => {
    expect(hypothesisIdProblem('h1', [])).toBeNull();
    expect(hypothesisIdProblem('H1', [])).toMatch(/lower-case/);
    expect(hypothesisIdProblem('h1', ['h1'])).toMatch(/already/);
    const family = { id: 'f', confirmatory: true, compromised: false, complete: false, outcomes: [{ hypothesis: 'h', state: 'running', estimate: null, p_value: null }] };
    expect(evaluating([family])).toBe(true);
    expect(evaluating([{ ...family, outcomes: [] }])).toBe(false);
  });
  it('list every empty field of record and trial inputs', () => {
    expect(missing({ x: emptyInput('record') })).toEqual(['x.run', 'x.node', 'x.column']);
    expect(missing({ x: emptyInput('trial') })).toEqual(['x.experiment']);
  });
  it('type lists as text, whatever the value was', () => {
    expect(listText(['a', 'b'])).toBe('a, b');
    expect(listText(undefined)).toBe('');
    expect(listText('')).toBe('');
    expect(parseList('a, b,, c ')).toEqual(['a', 'b', 'c']);
  });
});
