// Parameter form generated from a JSON Schema (an operator's or an analysis method's, from its Pydantic
// model), so new operators and methods get a form without editor changes. A missing value shows the
// default. Lists of text (such as a method's axes) are typed comma-separated.
import { useState } from 'react';
import type { ListedDataset } from './api';

type Property = {
  type?: string;
  title?: string;
  description?: string;
  enum?: string[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  items?: { type?: string };
  'x-qrs-dataset'?: ListedDataset['kind']; // the kind of dataset the parameter takes
};

/** A comma-separated list as its trimmed, non-empty items. */
export const parseList = (text: string): string[] =>
  text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

/** A list value as the text it is typed as; nothing yet (or anything not a list) is empty. */
export const listText = (value: unknown): string => (Array.isArray(value) ? value.join(', ') : '');

/** A list typed as text: the text is kept as typed, so a trailing comma survives until the next item. */
function ListInput({ value, onChange }: { value: unknown; onChange: (items: string[]) => void }) {
  const [text, setText] = useState(listText(value));
  return (
    <input
      type="text"
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(parseList(e.target.value));
      }}
    />
  );
}

export const datasetLabel = (d: ListedDataset) => `${d.name} · ${d.rows} ${d.kind === 'qrs/observations' ? 'observations' : 'sessions'} · ${d.evidence_class}`;

/** Where a feature dataset came from: its extraction's provider and model, and how many of its rows
 * have values; empty for other datasets. */
export function provenance(d: ListedDataset): string {
  if (!('derivation' in d) || !d.derivation) return '';
  const x = d.derivation;
  const invalid = Object.values(x.invalid_rows).reduce((total, n) => total + n, 0);
  const late = x.retrospective_rows ? `; ${x.retrospective_rows} captured after they could be used` : '';
  return `Extracted by ${x.provider} ${x.model}${x.hosted ? ' (hosted)' : ''} from ${x.requests} responses: ${d.rows - invalid} of ${d.rows} rows with values${late}.`;
}

export function ParamsForm({
  schema,
  params,
  datasets,
  onChange,
}: {
  schema: { properties?: unknown };
  params: Record<string, unknown> | undefined;
  datasets: ListedDataset[];
  onChange: (name: string, value: unknown) => void;
}) {
  const properties = (schema.properties ?? {}) as Record<string, Property>;
  const entries = Object.entries(properties);
  if (!entries.length) return <p className="muted">No parameters.</p>;
  return (
    <div className="form">
      {entries.map(([name, prop]) => {
        const value = params?.[name] ?? prop.default ?? '';
        let input;
        if (prop['x-qrs-dataset']) {
          input = (
            <select value={String(value)} onChange={(e) => onChange(name, e.target.value || undefined)}>
              <option value="">Choose a dataset…</option>
              {datasets
                .filter((d) => d.kind === prop['x-qrs-dataset'])
                .map((d) => (
                <option key={d.id} value={d.id}>
                  {datasetLabel(d)}
                </option>
              ))}
            </select>
          );
          const chosen = datasets.find((d) => d.id === value);
          if (chosen && provenance(chosen)) input = (
            <>
              {input}
              <small className="provenance">{provenance(chosen)}</small>
            </>
          );
        } else if (prop.enum) {
          input = (
            <select value={String(value)} onChange={(e) => onChange(name, e.target.value)}>
              {prop.enum.map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          );
        } else if (prop.type === 'boolean') {
          input = <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(name, e.target.checked)} />;
        } else if (prop.type === 'array') {
          input = <ListInput value={value} onChange={(items) => onChange(name, items)} />;
        } else if (prop.type === 'integer' || prop.type === 'number') {
          input = (
            <input
              type="number"
              step={prop.type === 'integer' ? 1 : 'any'}
              min={prop.minimum}
              max={prop.maximum}
              value={value as number}
              onChange={(e) => onChange(name, Number.isNaN(e.target.valueAsNumber) ? undefined : e.target.valueAsNumber)}
            />
          );
        } else {
          input = <input type="text" value={String(value)} onChange={(e) => onChange(name, e.target.value)} />;
        }
        return (
          <label key={name} className="field">
            <span>{prop.title ?? name}</span>
            {input}
            {prop.description && <small>{prop.description}</small>}
          </label>
        );
      })}
    </div>
  );
}
