// Import a CSV file of daily bars as a dataset (POST /api/datasets/import). The columns to map are
// read from the file's header in the browser; every other rule, including each rejected row, comes
// from the service, which validates the declaration against its contract.
import { useState, type FormEvent, type ReactNode } from 'react';
import { api, type Dataset } from './api';
import type { ColumnMapping, ImportDeclaration } from './contracts.gen';
import { Dialog } from './Dialog';

const FIELDS = ['date', 'open', 'high', 'low', 'close'] as const;

/** The column names on a CSV file's first line, read as the service reads them (Python's csv
 *  module, then stripped): a quote opens a quoted field only at the start of a field, and "" inside
 *  one is a literal quote. */
export function csvColumns(firstLine: string): string[] {
  const line = firstLine.replace(/^\uFEFF/, '');
  const names: string[] = [];
  let name = '';
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted && c === '"' && line[i + 1] === '"') name += line[++i];
    else if (c === '"' && (quoted || name === '')) quoted = !quoted;
    else if (c === ',' && !quoted) {
      names.push(name.trim());
      name = '';
    } else name += c;
  }
  names.push(name.trim());
  return names.filter(Boolean);
}

/** Each field mapped to the column of the same name, ignoring case, or left to the user. */
export function guessMapping(columns: string[]): ColumnMapping {
  const find = (field: string) => columns.find((c) => c.toLowerCase() === field) ?? '';
  return { date: find('date'), open: find('open'), high: find('high'), low: find('low'), close: find('close') };
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (dataset: Dataset) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [columns, setColumns] = useState<string[]>([]);
  const [mapping, setMapping] = useState<ColumnMapping>(guessMapping([]));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const choose = async (chosen: File | undefined) => {
    setFile(chosen ?? null);
    setError(null);
    const names = chosen ? csvColumns((await chosen.slice(0, 65536).text()).split(/\r?\n/)[0]) : [];
    setColumns(names);
    setMapping(guessMapping(names));
    if (chosen && !name) setName(chosen.name.replace(/\.[^.]*$/, ''));
  };

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const text = (field: string) => String(form.get(field) ?? '').trim();
    const declaration: ImportDeclaration = {
      name,
      columns: mapping,
      date_format: text('date_format'),
      price_unit: text('price_unit') as ImportDeclaration['price_unit'],
      calendar: text('calendar') || null,
      instrument: {
        symbol: text('symbol'),
        currency: text('currency'),
        multiplier: Number(text('multiplier')),
        tick_size: Number(text('tick_size')),
        margin_init: Number(text('margin_init')),
        margin_maint: Number(text('margin_maint')),
        expiry: text('expiry'),
      },
      evidence_class: text('evidence_class') as ImportDeclaration['evidence_class'],
      source: text('source'),
      redistributable: form.get('redistributable') === 'on',
    };
    setBusy(true);
    setError(null);
    try {
      onImported(await api.importDataset(file!, declaration));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="Import CSV" onClose={onClose}>
      <form className="form" onSubmit={(e) => void submit(e)}>
        <Field label="File" hint="Daily bars, at most 10 MB, with a header row. Prices are never filled; missing sessions are reported.">
          <input type="file" accept=".csv,text/csv" required onChange={(e) => void choose(e.target.files?.[0])} />
        </Field>
        <Field label="Dataset name">
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <fieldset>
          <legend>Columns</legend>
          {FIELDS.map((field) => (
            <Field key={field} label={field}>
              <select required disabled={!columns.length} value={mapping[field]} onChange={(e) => setMapping({ ...mapping, [field]: e.target.value })}>
                <option value="">Choose a column…</option>
                {columns.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </Field>
          ))}
          <Field label="Date format" hint="strptime directives, e.g. %Y-%m-%d or %d/%m/%Y.">
            <input name="date_format" required defaultValue="%Y-%m-%d" />
          </Field>
        </fieldset>
        <fieldset>
          <legend>Price unit</legend>
          <label>
            <input type="radio" name="price_unit" value="currency" required defaultChecked /> Currency units
          </label>
          <label>
            <input type="radio" name="price_unit" value="cents" /> Cents (divided by 100)
          </label>
        </fieldset>
        <Field label="Exchange calendar" hint="A pandas_market_calendars name such as CBOT_Agriculture. Leave empty to expect every weekday; the report is then labelled approximate.">
          <input name="calendar" />
        </Field>
        <fieldset>
          <legend>Instrument</legend>
          <Field label="Symbol">
            <input name="symbol" required />
          </Field>
          <Field label="Currency">
            <input name="currency" required defaultValue="USD" />
          </Field>
          <Field label="Multiplier" hint="Contract value per point of price.">
            <input name="multiplier" type="number" required step={1} />
          </Field>
          <Field label="Tick size">
            <input name="tick_size" type="number" required step="any" />
          </Field>
          <Field label="Initial margin" hint="Fraction of notional value, e.g. 0.1.">
            <input name="margin_init" type="number" required step="any" />
          </Field>
          <Field label="Maintenance margin" hint="Fraction of notional value, e.g. 0.05.">
            <input name="margin_maint" type="number" required step="any" />
          </Field>
          <Field label="Expiry" hint="The data must end before it.">
            <input name="expiry" type="date" required />
          </Field>
        </fieldset>
        <Field label="Evidence class">
          <select name="evidence_class" defaultValue="retrospective">
            <option value="retrospective">retrospective</option>
            <option value="point_in_time">point_in_time</option>
            <option value="synthetic">synthetic</option>
          </select>
        </Field>
        <Field label="Source" hint="Where the data came from.">
          <input name="source" required />
        </Field>
        <label>
          <input type="checkbox" name="redistributable" /> Exports may include the data
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog__actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={busy}>
            {busy ? 'Importing…' : 'Import'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
