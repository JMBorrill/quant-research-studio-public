// The template gallery's own logic, apart from its rendering: which datasets a template can start
// from, kind by kind (bars, and observations such as extracted features), and the first of each.
import type { ListedDataset, Template } from './api';

export const KIND_LABELS: Record<string, string> = { 'qrs/dataset': 'Bars', 'qrs/observations': 'Observations or features' };

/** For each kind of dataset the template reads, the datasets it can use: bars of the bar it needs,
 * and any observations. */
export function datasetChoices(template: Template, datasets: ListedDataset[]): Record<string, ListedDataset[]> {
  return Object.fromEntries(
    template.datasets.map((kind) => [kind, datasets.filter((d) => d.kind === kind && (kind !== 'qrs/dataset' || ('bar' in d && d.bar === template.required_bar)))]),
  );
}

/** The first dataset of each kind that has one: the gallery's starting choice. */
export function firstChoices(choices: Record<string, ListedDataset[]>): Record<string, string> {
  return Object.fromEntries(Object.entries(choices).flatMap(([kind, listed]) => (listed.length ? [[kind, listed[0].id]] : [])));
}
