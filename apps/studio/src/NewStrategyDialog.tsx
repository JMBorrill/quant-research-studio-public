// The template gallery: start from an empty canvas or a template, with a dataset of each kind the
// template reads. Templates describe the market conditions they are designed for.
import { useState } from 'react';
import type { ListedDataset, Template } from './api';
import { Dialog } from './Dialog';
import { datasetChoices, firstChoices, KIND_LABELS } from './gallery';
import { datasetLabel } from './ParamsForm';

const EMPTY = '';

export function NewStrategyDialog({
  templates,
  datasets,
  onClose,
  onCreate,
}: {
  templates: Template[];
  datasets: ListedDataset[];
  onClose: () => void;
  onCreate: (templateId: string | null, chosen: Record<string, string>) => void;
}) {
  const choicesFor = (t?: Template) => (t ? datasetChoices(t, datasets) : {});
  const [choice, setChoice] = useState(templates[0]?.id ?? EMPTY);
  const template = templates.find((t) => t.id === choice);
  const [chosen, setChosen] = useState(firstChoices(choicesFor(template)));

  const choose = (id: string) => {
    setChoice(id);
    setChosen(firstChoices(choicesFor(templates.find((t) => t.id === id))));
  };

  return (
    <Dialog title="New strategy" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onCreate(template?.id ?? null, chosen);
        }}
      >
        <fieldset className="gallery">
          <legend>Start from</legend>
          <label className="gallery__item">
            <input type="radio" name="start" checked={choice === EMPTY} onChange={() => choose(EMPTY)} />
            <span>
              <b>Empty canvas</b>
              <span className="muted">Build a strategy from the operator palette.</span>
            </span>
          </label>
          {templates.map((t) => (
            <label key={t.id} className="gallery__item">
              <input type="radio" name="start" checked={choice === t.id} onChange={() => choose(t.id)} />
              <span>
                <b>{t.title}</b> <small className="muted">version {t.version}</small>
                <span>{t.description}</span>
                <ul>
                  {t.conditions.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </span>
            </label>
          ))}
        </fieldset>
        {template &&
          Object.entries(choicesFor(template)).map(([kind, listed]) => (
            <label key={kind} className="field">
              <span>{KIND_LABELS[kind] ?? kind}</span>
              <select
                aria-label={KIND_LABELS[kind] ?? kind}
                value={chosen[kind] ?? ''}
                onChange={(e) => setChosen(({ [kind]: _, ...rest }) => (e.target.value ? { ...rest, [kind]: e.target.value } : rest))}
              >
                <option value="">Choose later</option>
                {listed.map((d) => (
                  <option key={d.id} value={d.id}>
                    {datasetLabel(d)}
                  </option>
                ))}
              </select>
              <small>{kind === 'qrs/dataset' ? `Datasets with ${template.required_bar} bars, which this template needs.` : 'The observations or extracted features this template reads.'}</small>
            </label>
          ))}
        <div className="dialog__actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary">
            Create
          </button>
        </div>
      </form>
    </Dialog>
  );
}
