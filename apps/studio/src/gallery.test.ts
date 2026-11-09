import { describe, expect, it } from 'vitest';
import type { ListedDataset, Template } from './api';
import { datasetChoices, firstChoices } from './gallery';
import { provenance } from './ParamsForm';

const template = (datasets: string[]) => ({ id: 't', version: 1, title: 'T', description: '', required_bar: '1D', conditions: [], sha256: '', datasets }) as unknown as Template;
const bars = (id: string, bar = '1D') => ({ id, kind: 'qrs/dataset', bar, name: id, rows: 10, evidence_class: 'synthetic' }) as unknown as ListedDataset;
const observations = (id: string, derivation?: object) =>
  ({ id, kind: 'qrs/observations', name: id, rows: 4, evidence_class: 'exploratory', ...(derivation ? { derivation } : {}) }) as unknown as ListedDataset;
const documents = { id: 'docs', kind: 'qrs/documents', name: 'docs', rows: 4, evidence_class: 'synthetic' } as unknown as ListedDataset;

describe('template gallery', () => {
  it('offers, for each kind of dataset a template reads, the datasets of that kind it can use', () => {
    const listed = [bars('b1'), bars('weekly', '1W'), observations('o1'), documents, observations('o2')];
    const choices = datasetChoices(template(['qrs/dataset', 'qrs/observations']), listed);
    expect(Object.fromEntries(Object.entries(choices).map(([kind, ds]) => [kind, ds.map((d) => d.id)]))).toEqual({
      'qrs/dataset': ['b1'],
      'qrs/observations': ['o1', 'o2'],
    });
    // A bars-only template is offered bars alone, and documents are never offered.
    expect(Object.keys(datasetChoices(template(['qrs/dataset']), listed))).toEqual(['qrs/dataset']);
  });

  it('starts from the first dataset of each kind that has one', () => {
    expect(firstChoices({ 'qrs/dataset': [bars('b1'), bars('b2')], 'qrs/observations': [] })).toEqual({ 'qrs/dataset': 'b1' });
  });
});

describe('feature provenance', () => {
  it('says how a feature dataset was extracted, and nothing for other datasets', () => {
    const derivation = { provider: 'fixture', model: 'fixture', hosted: false, requests: 4, invalid_rows: { not_json: 1 }, retrospective_rows: 4 };
    expect(provenance(observations('o', derivation))).toBe('Extracted by fixture fixture from 4 responses: 3 of 4 rows with values; 4 captured after they could be used.');
    expect(provenance(observations('o', { ...derivation, hosted: true, invalid_rows: {}, retrospective_rows: 0 }))).toBe(
      'Extracted by fixture fixture (hosted) from 4 responses: 4 of 4 rows with values.',
    );
    expect(provenance(observations('imported'))).toBe('');
    expect(provenance(bars('b1'))).toBe('');
  });
});
