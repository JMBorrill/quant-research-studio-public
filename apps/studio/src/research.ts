// The research views' logic (Stage 5), kept apart from the components so Vitest can check it:
// building analysis documents from the form, and describing projects, read horizons and families.
import type { AnalysisDocument, MethodInfo } from './contracts.gen';

export type InputKind = AnalysisInputValue['kind'];
export type AnalysisInputValue = AnalysisDocument['inputs'][string];
export type Horizon = { reads: number; read_to: string | null; full: boolean; test_period_read: boolean };
export type Outcome = { hypothesis: string; state: string; estimate: number | null; p_value: number | null; adjusted_p_value?: number; supported?: boolean };
export type FamilyView = { id: string; confirmatory: boolean; compromised: boolean; complete: boolean; outcomes: Outcome[] };

/** An input of a kind, with its fields empty for the form to fill. */
export function emptyInput(kind: InputKind): AnalysisInputValue {
  switch (kind) {
    case 'bars':
      return { kind, dataset: '', measure: 'percent' };
    case 'observations':
      return { kind, dataset: '', column: '', on: '', max_age_days: 31 };
    case 'equity':
      return { kind, run: '' };
    case 'record':
      return { kind, run: '', node: '', column: '' };
    case 'fills':
      return { kind, run: '', column: 'quantity' };
    case 'model':
      return { kind, run: '', node: '', term: '' };
    case 'experiment':
      return { kind, experiment: '' };
    case 'trial':
      return { kind, experiment: '', rank: 1 };
  }
}

/** The input kinds a method's input accepts; in a hypothesis, an equity input may also be an experiment's trial. */
export function kindsFor(method: MethodInfo, input: string, hypothesis = false): InputKind[] {
  const kinds = (method.inputs[input] ?? []) as InputKind[];
  return hypothesis && kinds.includes('equity') ? [...kinds, 'trial'] : kinds;
}

/** Fields an input still needs before the document can be submitted. */
export function missing(inputs: Record<string, AnalysisInputValue>): string[] {
  return Object.entries(inputs).flatMap(([name, given]) =>
    Object.entries(given)
      .filter(([, value]) => value === '')
      .map(([field]) => `${name}.${field}`),
  );
}

/** The analysis document the form describes; optional dates left out when empty. */
export function analysisDocument(
  method: MethodInfo,
  name: string,
  inputs: Record<string, AnalysisInputValue>,
  params: Record<string, unknown>,
  dates: { until?: string; project?: string },
): AnalysisDocument {
  return {
    name: name.trim() || method.title,
    method: method.id,
    method_version: method.version,
    inputs,
    params,
    ...(dates.until ? { until: dates.until } : {}),
    ...(dates.project ? { project: dates.project } : {}),
  };
}

/** How far a dataset has been read, in words. */
export function horizonText(h: Horizon): string {
  if (!h.reads) return 'never read';
  const extent = h.full ? 'read in full' : `read to ${h.read_to}`;
  return `${extent} (${h.reads} reads)${h.test_period_read ? ': test period read' : ''}`;
}

/** Whether a confirmatory family could be registered now: no dataset's test period has been read. */
export const testPeriodClean = (horizons: Record<string, Horizon>) => Object.values(horizons).every((h) => !h.test_period_read);

/** A hypothesis ID the contract accepts, not already in the family; or why not. */
export function hypothesisIdProblem(id: string, taken: string[]): string | null {
  if (!/^[a-z][a-z0-9_]{0,39}$/.test(id)) return 'An ID is a lower-case letter, then letters, digits or underscores.';
  return taken.includes(id) ? `The family already has a hypothesis ${id}.` : null;
}

/** Whether a family is still waiting for an evaluation, so its view should look again. */
export const evaluating = (families: FamilyView[]) => families.some((f) => f.outcomes.some((o) => o.state === 'running'));

/** A family's standing, as every view labels it. */
export function familyLabel(f: FamilyView): string {
  if (f.compromised) return 'compromised: the test period was read outside its evaluations';
  return f.confirmatory ? 'confirmatory' : 'post hoc';
}

/** One hypothesis's outcome, in words: its state and p-values, and, once the family is complete, its conclusion. */
export function outcomeText(o: Outcome): string {
  const p = o.p_value === null ? '' : `, p = ${o.p_value.toPrecision(3)}`;
  const adjusted = o.adjusted_p_value === undefined ? '' : `, adjusted ${o.adjusted_p_value.toPrecision(3)}: ${o.supported ? 'supported' : 'not supported'}`;
  return `${o.hypothesis}: ${o.state}${p}${adjusted}`;
}
