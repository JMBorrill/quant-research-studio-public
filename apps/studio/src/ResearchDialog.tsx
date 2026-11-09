// Research (Stage 5): projects with their notes, read horizons and families; families registered behind
// a confirmation step that shows the horizons; analyses created through the generated parameter form
// and viewed with their values, tables and SVG figures (as images, so nothing in them runs); and reports,
// built from stored analyses and families and viewed in a sandboxed frame.
import { useCallback, useEffect, useState } from 'react';
import { api, type AnalysisResult, type ExperimentOverview, type FamilyStatus, type ListedDataset, type ProjectSummary, type ReportSummary } from './api';
import type { AnalysisDocument, AnalysisInfo, FamilyDocument, Hypothesis, MethodInfo, ProjectDocument, ReportDocument, RunInfo } from './contracts.gen';
import { Dialog } from './Dialog';
import { ParamsForm } from './ParamsForm';
import { type AnalysisInputValue, analysisDocument, emptyInput, evaluating, familyLabel, horizonText, hypothesisIdProblem, type InputKind, kindsFor, missing, outcomeText, testPeriodClean } from './research';

type Tab = 'projects' | 'analyses' | 'reports';
type Sources = { datasets: ListedDataset[]; methods: MethodInfo[]; runs: RunInfo[]; experiments: ExperimentOverview[] };

export function ResearchDialog({ datasets, onClose }: { datasets: ListedDataset[]; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('projects');
  const [sources, setSources] = useState<Sources>({ datasets, methods: [], runs: [], experiments: [] });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([api.methods(), api.runs(), api.allExperiments()]).then(
      ([methods, runs, experiments]) => setSources({ datasets, methods, runs: runs.filter((r) => r.status === 'succeeded'), experiments }),
      (e: Error) => setError(e.message),
    );
  }, [datasets]);
  return (
    <Dialog title="Research" onClose={onClose} wide>
      <div className="tabs" role="tablist">
        {(['projects', 'analyses', 'reports'] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'primary' : ''} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      {error && <p className="error">{error}</p>}
      {tab === 'projects' && <Projects sources={sources} />}
      {tab === 'analyses' && <Analyses sources={sources} />}
      {tab === 'reports' && <Reports />}
      <div className="dialog__actions">
        <button onClick={onClose}>Close</button>
      </div>
    </Dialog>
  );
}

const datasetName = (datasets: ListedDataset[], id: string) => datasets.find((d) => d.id === id)?.name ?? id;

function Projects({ sources }: { sources: Sources }) {
  const [projects, setProjects] = useState<{ id: string; document: { name: string } }[]>([]);
  const [chosen, setChosen] = useState<ProjectSummary | null>(null);
  const [view, setView] = useState<'project' | 'new' | 'family'>('project');
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => api.projects().then(setProjects, (e: Error) => setError(e.message)), []);
  const open = useCallback((id: string) => api.project(id).then(setChosen, (e: Error) => setError(e.message)), []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const act = (work: Promise<unknown>) => work.then(() => setError(null), (e: Error) => setError(e.message));
  return (
    <div className="research">
      <nav className="research__list">
        {projects.map((p) => (
          <button key={p.id} onClick={() => void open(p.id).then(() => setView('project'))}>
            {p.document.name}
          </button>
        ))}
        <button onClick={() => setView('new')}>New project…</button>
      </nav>
      <section className="research__main">
        {error && <p className="error">{error}</p>}
        {view === 'new' && (
          <NewProject
            datasets={sources.datasets}
            onCreate={(document) =>
              act(
                api.createProject(document).then((created) => {
                  setChosen(created);
                  setView('project');
                  void refresh();
                }),
              )
            }
          />
        )}
        {view === 'project' && chosen && (
          <ProjectView
            key={chosen.id}
            project={chosen}
            sources={sources}
            // A refresh that lands after another project was chosen leaves that one shown.
            onChanged={(p) => setChosen((current) => (current?.id === p.id ? p : current))}
            onRegister={() => setView('family')}
            act={act}
          />
        )}
        {view === 'family' && chosen && (
          <FamilyForm
            project={chosen}
            sources={sources}
            onRegister={(document) =>
              act(
                api.registerFamily(document).then(() => {
                  setView('project');
                  return open(chosen.id);
                }),
              )
            }
            onBack={() => setView('project')}
          />
        )}
      </section>
    </div>
  );
}

function NewProject({ datasets, onCreate }: { datasets: ListedDataset[]; onCreate: (document: ProjectDocument) => void }) {
  const [name, setName] = useState('');
  const [question, setQuestion] = useState('');
  const [scope, setScope] = useState<string[]>([]);
  const [start, setStart] = useState('');
  return (
    <div className="form">
      <label className="field">
        <span>Project name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Research question</span>
        <textarea value={question} onChange={(e) => setQuestion(e.target.value)} />
      </label>
      <fieldset>
        <legend>Datasets in scope</legend>
        {datasets
          .filter((d) => d.kind !== 'qrs/documents')
          .map((d) => (
            <label key={d.id}>
              <input type="checkbox" checked={scope.includes(d.id)} onChange={(e) => setScope(e.target.checked ? [...scope, d.id] : scope.filter((i) => i !== d.id))} /> {d.name}
            </label>
          ))}
      </fieldset>
      <label className="field">
        <span>Confirmation start</span>
        <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        <small>From this date the datasets' sessions are the test period: analyses in the project read only before it.</small>
      </label>
      <button className="primary" disabled={!name || !question || !scope.length || !start} onClick={() => onCreate({ name, question, datasets: scope as ProjectDocument['datasets'], confirmation_start: start })}>
        Create project
      </button>
    </div>
  );
}

function ProjectView({
  project,
  sources,
  onChanged,
  onRegister,
  act,
}: {
  project: ProjectSummary;
  sources: Sources;
  onChanged: (p: ProjectSummary) => void;
  onRegister: () => void;
  act: (work: Promise<unknown>) => Promise<void>;
}) {
  const [note, setNote] = useState('');
  const counts = Object.entries(project.analyses).map(([status, n]) => `${n} ${status}`);
  const refresh = useCallback(() => api.project(project.id).then(onChanged), [project.id, onChanged]);
  const running = evaluating(project.families);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void refresh(), 1000);
    return () => clearInterval(timer);
  }, [running, refresh]);
  return (
    <div>
      <h3>{project.document.name}</h3>
      <p>{project.document.question}</p>
      <p className="muted">Confirmation start {project.document.confirmation_start}. Analyses: {counts.join(', ') || 'none yet'}.</p>
      <h4>Read horizons</h4>
      <table>
        <tbody>
          {Object.entries(project.horizons).map(([id, h]) => (
            <tr key={id}>
              <td>{datasetName(sources.datasets, id)}</td>
              <td>{horizonText(h)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>Families</h4>
      {project.families.map((f) => (
        <FamilyView key={f.id} family={f} onEvaluate={(h) => act(api.evaluate(f.id, h).then(refresh))} onWithdraw={(h, reason) => act(api.withdraw(f.id, h, reason).then(refresh))} />
      ))}
      <button onClick={onRegister}>Register a family…</button>
      <h4>Notes</h4>
      {project.notes.map((n) => (
        <p key={n.revision}>
          <span className="muted">{n.saved_at}</span> {n.text}
        </p>
      ))}
      <textarea aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      <button disabled={!note.trim()} onClick={() => void act(api.addNote(project.id, note).then((p) => (onChanged(p), setNote(''))))}>
        Add note
      </button>
    </div>
  );
}

function FamilyView({ family, onEvaluate, onWithdraw }: { family: FamilyStatus; onEvaluate: (h: string) => void; onWithdraw: (h: string, reason: string) => void }) {
  const settled = new Set([...family.evaluations.map((e) => e.hypothesis), ...family.withdrawals.map((w) => w.hypothesis)]);
  // Both are binding: an evaluation reads the test period once, and a withdrawal counts as p = 1 for good.
  const [pending, setPending] = useState<{ hypothesis: string; action: 'evaluate' | 'withdraw' } | null>(null);
  const [reason, setReason] = useState('');
  return (
    <div className="family" data-family={family.id}>
      <p>
        <strong>{family.document.name}</strong> · {familyLabel(family)} · {family.document.correction} at {family.document.alpha} ·{' '}
        {family.complete ? 'complete' : 'not complete'}
      </p>
      <ul>
        {family.outcomes.map((o) => (
          <li key={o.hypothesis}>
            {outcomeText(o)}
            {!settled.has(o.hypothesis) && pending?.hypothesis !== o.hypothesis && (
              <>
                {' '}
                <button onClick={() => setPending({ hypothesis: o.hypothesis, action: 'evaluate' })}>Evaluate {o.hypothesis}</button>{' '}
                <button onClick={() => (setPending({ hypothesis: o.hypothesis, action: 'withdraw' }), setReason(''))}>Withdraw {o.hypothesis}</button>
              </>
            )}
            {pending?.hypothesis === o.hypothesis && pending.action === 'evaluate' && (
              <span className="confirm">
                {' '}
                Evaluate {o.hypothesis} now? It reads the test period, once: the result is binding.{' '}
                <button className="primary" onClick={() => (onEvaluate(o.hypothesis), setPending(null))}>
                  Confirm evaluation
                </button>{' '}
                <button onClick={() => setPending(null)}>Cancel</button>
              </span>
            )}
            {pending?.hypothesis === o.hypothesis && pending.action === 'withdraw' && (
              <span className="confirm">
                {' '}
                <input aria-label="Reason for withdrawing" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why it is withdrawn" />{' '}
                <button className="primary" disabled={!reason.trim()} onClick={() => (onWithdraw(o.hypothesis, reason.trim()), setPending(null))}>
                  Withdraw for good (counts as p = 1)
                </button>{' '}
                <button onClick={() => setPending(null)}>Cancel</button>
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function FamilyForm({ project, sources, onRegister, onBack }: { project: ProjectSummary; sources: Sources; onRegister: (d: FamilyDocument) => void; onBack: () => void }) {
  const [name, setName] = useState('');
  const [confirmatory, setConfirmatory] = useState(true);
  const [correction, setCorrection] = useState<FamilyDocument['correction']>('holm');
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [id, setId] = useState('h1');
  const [statement, setStatement] = useState('');
  const [direction, setDirection] = useState<Hypothesis['direction']>('greater');
  const [reviewing, setReviewing] = useState(false);
  const testing = { ...sources, methods: sources.methods.filter((m) => m.tests) };
  const idProblem = hypothesisIdProblem(id, hypotheses.map((h) => h.id));
  // Registration waits for at least one hypothesis, so this is the contract's non-empty list.
  const document: FamilyDocument = { project: project.id, name, confirmatory, correction, hypotheses: hypotheses as FamilyDocument['hypotheses'] };
  if (reviewing) {
    const clean = testPeriodClean(project.horizons);
    return (
      <div>
        <h4>Register “{name}”: this cannot be changed</h4>
        <p>
          {hypotheses.length} hypotheses, {confirmatory ? 'confirmatory' : 'post hoc'}, {correction}. Once registered the family is frozen: hypotheses can be
          withdrawn (counting as p = 1) but never changed or added.
        </p>
        <table>
          <tbody>
            {Object.entries(project.horizons).map(([dataset, h]) => (
              <tr key={dataset}>
                <td>{datasetName(sources.datasets, dataset)}</td>
                <td>{horizonText(h)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {confirmatory && !clean && <p className="error">The test period has been read, so a confirmatory family will be refused: register it as post hoc.</p>}
        <button className="primary" onClick={() => onRegister(document)}>
          Register family
        </button>{' '}
        <button onClick={() => setReviewing(false)}>Back</button>
      </div>
    );
  }
  return (
    <div className="form">
      <label className="field">
        <span>Family name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        <input type="checkbox" checked={confirmatory} onChange={(e) => setConfirmatory(e.target.checked)} /> Confirmatory
      </label>
      <label className="field">
        <span>Correction</span>
        <select value={correction} onChange={(e) => setCorrection(e.target.value as FamilyDocument['correction'])}>
          <option value="holm">Holm</option>
          <option value="bh">Benjamini–Hochberg</option>
          <option value="by">Benjamini–Yekutieli</option>
        </select>
      </label>
      <ul>
        {hypotheses.map((h) => (
          <li key={h.id}>
            {h.id}: {h.statement} ({h.direction}, {h.analysis.method}){' '}
            <button onClick={() => setHypotheses(hypotheses.filter((other) => other.id !== h.id))}>Remove {h.id}</button>
          </li>
        ))}
      </ul>
      <fieldset>
        <legend>Hypothesis</legend>
        <label className="field">
          <span>Hypothesis ID</span>
          <input value={id} onChange={(e) => setId(e.target.value)} />
          {idProblem && <small className="error">{idProblem}</small>}
        </label>
        <label className="field">
          <span>Statement</span>
          <input value={statement} onChange={(e) => setStatement(e.target.value)} />
        </label>
        <label className="field">
          <span>Direction</span>
          <select value={direction} onChange={(e) => setDirection(e.target.value as Hypothesis['direction'])}>
            <option value="greater">greater</option>
            <option value="less">less</option>
            <option value="two_sided">two-sided</option>
          </select>
        </label>
        <AnalysisForm
          sources={testing}
          hypothesis
          submitLabel="Add hypothesis"
          blocked={idProblem ?? (statement.trim() ? null : 'Write the hypothesis statement.')}
          onSubmit={(analysis) => {
            // A joint test has no direction.
            const joint = testing.methods.find((m) => m.id === analysis.method)?.tests === 'joint';
            setHypotheses([...hypotheses, { id, statement, direction: joint ? 'two_sided' : direction, analysis }]);
            setId(`h${hypotheses.length + 2}`);
            setStatement('');
          }}
        />
      </fieldset>
      <button className="primary" disabled={!name || !hypotheses.length} onClick={() => setReviewing(true)}>
        Review and register…
      </button>{' '}
      <button onClick={onBack}>Back</button>
    </div>
  );
}

function AnalysisForm({
  sources,
  hypothesis,
  projects,
  submitLabel,
  blocked,
  onSubmit,
}: {
  sources: Sources;
  hypothesis?: boolean;
  projects?: { id: string; document: { name: string } }[];
  submitLabel: string;
  blocked?: string | null; // why the form may not be submitted yet, from its surroundings
  onSubmit: (document: AnalysisDocument) => void;
}) {
  const [methodId, setMethodId] = useState('');
  const [name, setName] = useState('');
  const [inputs, setInputs] = useState<Record<string, AnalysisInputValue>>({});
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [until, setUntil] = useState('');
  const [project, setProject] = useState('');
  const method = sources.methods.find((m) => m.id === methodId);
  const choose = (id: string) => {
    const chosen = sources.methods.find((m) => m.id === id);
    setMethodId(id);
    setParams({});
    setInputs(chosen ? Object.fromEntries(Object.keys(chosen.inputs).map((input) => [input, emptyInput(kindsFor(chosen, input)[0])])) : {});
  };
  const set = (input: string, field: string, value: unknown) => setInputs({ ...inputs, [input]: { ...inputs[input], [field]: value } as AnalysisInputValue });
  const unfilled = missing(inputs);
  return (
    <div className="form analysis-form">
      <label className="field">
        <span>Method</span>
        <select value={methodId} onChange={(e) => choose(e.target.value)}>
          <option value="">Choose a method…</option>
          {sources.methods.map((m) => (
            <option key={m.id} value={m.id}>
              {m.title}
            </option>
          ))}
        </select>
        {method && <small>{method.description}</small>}
      </label>
      {method &&
        Object.keys(method.inputs).map((input) => (
          <InputFields
            key={input}
            name={input}
            kinds={kindsFor(method, input, hypothesis)}
            value={inputs[input]}
            sources={sources}
            onKind={(kind) => setInputs({ ...inputs, [input]: emptyInput(kind) })}
            onField={(field, value) => set(input, field, value)}
          />
        ))}
      {method && <ParamsForm schema={method.params_schema} params={params} datasets={sources.datasets} onChange={(n, v) => setParams({ ...params, [n]: v })} />}
      {!hypothesis && (
        <>
          <label className="field">
            <span>Analysis name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Until</span>
            <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
          </label>
          {projects && (
            <label className="field">
              <span>Project</span>
              <select value={project} onChange={(e) => setProject(e.target.value)}>
                <option value="">None</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.document.name}
                  </option>
                ))}
              </select>
              <small>An analysis in a project reads nothing from its confirmation start.</small>
            </label>
          )}
        </>
      )}
      <button
        disabled={!method || unfilled.length > 0 || Boolean(blocked)}
        title={blocked ?? (unfilled.length ? `Still needed: ${unfilled.join(', ')}` : undefined)}
        onClick={() => method && onSubmit(analysisDocument(method, name, inputs, params, { until, project }))}
      >
        {submitLabel}
      </button>
    </div>
  );
}

function InputFields({
  name,
  kinds,
  value,
  sources,
  onKind,
  onField,
}: {
  name: string;
  kinds: InputKind[];
  value: AnalysisInputValue | undefined;
  sources: Sources;
  onKind: (kind: InputKind) => void;
  onField: (field: string, value: unknown) => void;
}) {
  if (!value) return null;
  const fields = value as unknown as Record<string, unknown>;
  const pick = (field: string, kind: ListedDataset['kind']) => (
    <label className="field">
      <span>
        {name}: {field}
      </span>
      <select value={String(fields[field])} onChange={(e) => onField(field, e.target.value)}>
        <option value="">Choose…</option>
        {sources.datasets
          .filter((d) => d.kind === kind)
          .map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
      </select>
    </label>
  );
  const text = (field: string) => (
    <label className="field">
      <span>
        {name}: {field}
      </span>
      <input value={String(fields[field])} onChange={(e) => onField(field, e.target.value)} />
    </label>
  );
  const number = (field: string) => (
    <label className="field">
      <span>
        {name}: {field}
      </span>
      <input type="number" min={1} step={1} value={Number(fields[field])} onChange={(e) => onField(field, e.target.valueAsNumber)} />
    </label>
  );
  const run = (
    <label className="field">
      <span>{name}: run</span>
      <select value={String(fields.run)} onChange={(e) => onField('run', e.target.value)}>
        <option value="">Choose a succeeded run…</option>
        {sources.runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.id} (strategy {r.strategy_id} r{r.revision}
            {r.trial ? `, trial ${r.trial}` : ''})
          </option>
        ))}
      </select>
    </label>
  );
  const experiment = (
    <label className="field">
      <span>{name}: experiment</span>
      <select value={String(fields.experiment)} onChange={(e) => onField('experiment', e.target.value)}>
        <option value="">Choose an experiment…</option>
        {sources.experiments.map((x) => (
          <option key={x.id} value={x.id}>
            {x.experiment.name} ({x.status})
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <fieldset>
      <legend>Input {name}</legend>
      {kinds.length > 1 && (
        <label className="field">
          <span>{name}: kind</span>
          <select value={value.kind} onChange={(e) => onKind(e.target.value as InputKind)}>
            {kinds.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>
      )}
      {value.kind === 'bars' && (
        <>
          {pick('dataset', 'qrs/dataset')}
          <label className="field">
            <span>{name}: measure</span>
            <select value={String(fields.measure)} onChange={(e) => onField('measure', e.target.value)}>
              <option value="percent">percent</option>
              <option value="price_change">price change per contract</option>
            </select>
            <small>A continuous series can only be read as price changes.</small>
          </label>
        </>
      )}
      {value.kind === 'observations' && (
        <>
          {pick('dataset', 'qrs/observations')}
          {text('column')}
          {pick('on', 'qrs/dataset')}
          {number('max_age_days')}
        </>
      )}
      {(value.kind === 'equity' || value.kind === 'record' || value.kind === 'fills' || value.kind === 'model') && run}
      {value.kind === 'record' && (
        <>
          {text('node')}
          {text('column')}
        </>
      )}
      {value.kind === 'fills' && (
        <label className="field">
          <span>{name}: column</span>
          <select value={String(fields.column)} onChange={(e) => onField('column', e.target.value)}>
            <option value="quantity">quantity bought less sold</option>
            <option value="commission">commission</option>
            <option value="slippage">slippage</option>
          </select>
        </label>
      )}
      {value.kind === 'model' && (
        <>
          {text('node')}
          {text('term')}
        </>
      )}
      {(value.kind === 'experiment' || value.kind === 'trial') && experiment}
      {value.kind === 'trial' && number('rank')}
    </fieldset>
  );
}

function Analyses({ sources }: { sources: Sources }) {
  const [analyses, setAnalyses] = useState<AnalysisInfo[]>([]);
  const [projects, setProjects] = useState<{ id: string; document: { name: string } }[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => api.analyses().then(setAnalyses, (e: Error) => setError(e.message)), []);
  useEffect(() => {
    void refresh();
    void api.projects().then(setProjects);
  }, [refresh]);
  const active = analyses.some((a) => a.status === 'queued' || a.status === 'running');
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refresh(), 1000);
    return () => clearInterval(timer);
  }, [active, refresh]);
  return (
    <div className="research">
      <nav className="research__list">
        {analyses.map((a) => (
          <button key={a.id} onClick={() => (setChosen(a.id), setCreating(false))}>
            {a.name} · {a.status}
          </button>
        ))}
        <button onClick={() => setCreating(true)}>New analysis…</button>
      </nav>
      <section className="research__main">
        {error && <p className="error">{error}</p>}
        {creating ? (
          <AnalysisForm
            sources={sources}
            projects={projects}
            submitLabel="Run analysis"
            onSubmit={(document) =>
              api.createAnalysis(document).then(
                (created) => {
                  setError(null);
                  setCreating(false);
                  setChosen(created.id);
                  return refresh();
                },
                (e: Error) => setError(e.message),
              )
            }
          />
        ) : (
          chosen && <AnalysisView key={chosen} info={analyses.find((a) => a.id === chosen)} />
        )}
      </section>
    </div>
  );
}

function AnalysisView({ info }: { info: AnalysisInfo | undefined }) {
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (info?.status === 'succeeded') void api.analysisResult(info.id).then(setResult, (e: Error) => setError(e.message));
  }, [info?.id, info?.status]);
  if (!info) return null;
  if (error) return <p className="error">{error}</p>;
  if (info.status !== 'succeeded') return <p>{info.status === 'failed' ? `Failed: ${info.error}` : `${info.name} is ${info.status}.`}</p>;
  if (!result) return <p>Loading…</p>;
  const figures = Object.keys(result.result.outputs).filter((p) => p.startsWith('figures/'));
  return (
    <div className="analysis-view">
      <h3>{info.name}</h3>
      <p className="muted">
        {info.method} · evidence {result.result.evidence_class} · cutoff {result.result.cutoff ?? 'none'} · result {result.record.result_sha256.slice(0, 12)}
      </p>
      <table>
        <tbody>
          {Object.entries(result.result.values).map(([k, v]) => (
            <tr key={k}>
              <th>{k}</th>
              <td>{JSON.stringify(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {result.result.warnings.map((w) => (
        <p key={w} className="warning">
          {w}
        </p>
      ))}
      {figures.map((path) => {
        const figure = path.slice('figures/'.length, -'.svg'.length);
        return <img key={path} src={api.figureUrl(info.id, figure)} alt={`${figure} (${result.result.outputs[path]})`} />;
      })}
      {Object.entries(result.tables).map(([table, rows]) => (
        <details key={table}>
          <summary>
            Table {table} ({rows.length} rows, {result.result.outputs[`tables/${table}.csv`]})
          </summary>
          <table>
            <tbody>
              {rows.slice(0, 20).map((row, i) => (
                <tr key={i}>
                  {Object.values(row).map((v, j) => (
                    <td key={j}>{String(v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ))}
    </div>
  );
}

function Reports() {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [projects, setProjects] = useState<{ id: string; document: { name: string } }[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [verified, setVerified] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ project: string; title: string; analyses: string[]; families: string[] }>({ project: '', title: '', analyses: [], families: [] });
  const [candidates, setCandidates] = useState<{ analyses: AnalysisInfo[]; families: FamilyStatus[] }>({ analyses: [], families: [] });
  const refresh = useCallback(() => api.reports().then(setReports, (e: Error) => setError(e.message)), []);
  useEffect(() => {
    void refresh();
    void api.projects().then(setProjects);
  }, [refresh]);
  useEffect(() => {
    setCandidates({ analyses: [], families: [] });
    if (!draft.project) return;
    let current = true; // a slower answer for a project no longer chosen is ignored
    void Promise.all([api.analyses(), api.project(draft.project)]).then(([analyses, project]) => {
      if (current) setCandidates({ analyses: analyses.filter((a) => a.project === draft.project && a.status === 'succeeded'), families: project.families });
    });
    return () => {
      current = false;
    };
  }, [draft.project]);
  const toggle = (key: 'analyses' | 'families', id: string, on: boolean) => setDraft({ ...draft, [key]: on ? [...draft[key], id] : draft[key].filter((i) => i !== id) });
  const build = () => {
    const document: ReportDocument = {
      project: draft.project,
      title: draft.title,
      sections: [
        {
          heading: 'Findings',
          blocks: [...draft.analyses.map((analysis) => ({ kind: 'values' as const, analysis })), ...draft.families.map((family) => ({ kind: 'family' as const, family }))],
        },
      ],
    };
    api.buildReport(document).then(
      (built) => {
        setError(null);
        setVerified(null);
        setChosen(built.id);
        return refresh();
      },
      (e: Error) => setError(e.message),
    );
  };
  return (
    <div className="research">
      <nav className="research__list">
        {reports.map((r) => (
          <button key={r.id} onClick={() => (setChosen(r.id), setVerified(null))}>
            {r.document.title}
          </button>
        ))}
        <button onClick={() => (setChosen(null), setVerified(null))}>New report…</button>
      </nav>
      <section className="research__main">
        {error && <p className="error">{error}</p>}
        {chosen ? (
          <div>
            <button
              onClick={() =>
                void api.verifyReport(chosen).then(
                  (v) => setVerified(v.verified ? 'Verified against the store.' : `Not verified: ${v.problems.join(' ')}`),
                  (e: Error) => setVerified(`Not verified: ${e.message}`),
                )
              }
            >
              Verify
            </button>
            {verified && <p>{verified}</p>}
            <iframe title="Report" sandbox="" src={api.reportUrl(chosen)} className="report-frame" />
          </div>
        ) : (
          <div className="form">
            <label className="field">
              <span>Report project</span>
              <select value={draft.project} onChange={(e) => setDraft({ project: e.target.value, title: draft.title, analyses: [], families: [] })}>
                <option value="">Choose a project…</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.document.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Report title</span>
              <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            </label>
            {candidates.analyses.map((a) => (
              <label key={a.id}>
                <input type="checkbox" checked={draft.analyses.includes(a.id)} onChange={(e) => toggle('analyses', a.id, e.target.checked)} /> {a.name} ({a.method})
              </label>
            ))}
            {candidates.families.map((f) => (
              <label key={f.id}>
                <input type="checkbox" checked={draft.families.includes(f.id)} onChange={(e) => toggle('families', f.id, e.target.checked)} /> Family {f.document.name} ({familyLabel(f)})
              </label>
            ))}
            <button className="primary" disabled={!draft.project || !draft.title || draft.analyses.length + draft.families.length === 0} onClick={build}>
              Build report
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
