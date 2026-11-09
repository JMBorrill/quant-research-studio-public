// Typed calls to the local Python service. Domain types come from contracts.gen.ts, which is
// generated from the Python contracts; only response envelopes are declared here.
import type { FamilyView, Horizon } from './research';
import type {
  AnalysisDocument,
  AnalysisInfo,
  DatasetManifest,
  DocumentsManifest,
  Experiment,
  FamilyDocument,
  Fill,
  ImportDeclaration,
  Issue,
  MethodInfo,
  ObservationsManifest,
  OperatorInfo,
  ProjectDocument,
  ReportDocument,
  RunInfo,
  StrategyDocument,
  Template as TemplateFile,
} from './contracts.gen';

type EvidenceClass = DatasetManifest['evidence_class'];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

type FieldError = { loc: (string | number)[]; msg: string };

/** The service's error detail as one message: its text, or each invalid field (from FastAPI). */
function describe(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return (detail as FieldError[]).map((e) => `${e.loc.slice(1).join('.')}: ${e.msg}`).join('\n');
  return (detail as { message?: string } | undefined)?.message ?? fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { headers: { 'Content-Type': 'application/json' }, ...init });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail;
    throw new ApiError(res.status, describe(detail, res.statusText), detail);
  }
  return res.json();
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

export type StrategyRecord = { id: string; revision: number; document: StrategyDocument };
export type StrategySummary = { id: string; revision: number; name: string; saved_at: string };
export type Dataset = DatasetManifest & { id: string };
// /api/datasets lists bars, observations and documents; no operator takes documents, so no picker lists them.
export type ListedDataset = Dataset | ((ObservationsManifest | DocumentsManifest) & { id: string });
// datasets: the kinds of dataset the template reads, such as bars and features.
export type Template = Pick<TemplateFile, 'id' | 'version' | 'title' | 'description' | 'required_bar' | 'conditions'> & { sha256: string; datasets: string[] };
export type Compiled = { issues: Issue[]; code: string | null; code_sha256: string | null; warm_up: Record<string, number> | null };
export type EquityRow = { session: string; position: number; cash: number; unrealized_pnl: number; equity: number };
export type BacktestOutput = {
  metrics: Record<string, number>;
  assumptions: Record<string, unknown>;
  warm_up_sessions: number | null;
  first_signal_session: string | null;
  fills: Fill[];
  equity: EquityRow[];
};
export type ComparedRun = {
  id: string;
  strategy_id: string;
  revision: number;
  name: string;
  evidence_class: EvidenceClass;
  node: string;
  warm_up: number;
  excluded_sessions: number;
  position_at_start: number;
  metrics: Record<string, number>;
  equity_change: number[];
};
export type Comparison = {
  window: SessionWindow;
  runs: ComparedRun[];
  parameters: { node: string; field: string; values: unknown[] }[];
  mismatches: { name: string; values: unknown[] }[];
};
export type SessionWindow = { start: string; end: string; sessions: number };
export const ACTIVE_STATUSES = new Set(['queued', 'running']);
export type ExperimentOverview = {
  id: string;
  experiment: Experiment;
  created_at: string;
  cancelled_at: string | null;
  status: 'running' | 'finished' | 'cancelled';
  counts: Record<string, number>; // trials by status
  exploratory: boolean; // it may read exploratory data; its results are labelled and kept apart
};
// A holdout evaluation, with how many evaluations of the same window (bars and holdout start), in
// any experiment, came before it.
export type HoldoutEvaluation = {
  experiment_id: string;
  experiment_name: string;
  exploratory: boolean;
  earlier: { verified: number; exploratory: number };
  trial: number;
  repeat: boolean;
  requested_at: string;
  run: RunInfo;
  window?: SessionWindow;
  result?: ComparedRun;
};
export type ExperimentSummary = ExperimentOverview & {
  trials: { trial: number; values: { node: string; param: string; value: unknown }[]; run: RunInfo }[];
  // The succeeded trials, best first, over their common window; or why they cannot be ranked.
  ranking: { window: SessionWindow; runs: (Omit<ComparedRun, 'equity_change'> & { trial: number })[]; mismatches: Comparison['mismatches'] } | { error: string } | null;
  holdout: HoldoutEvaluation[];
  // Every evaluation of this experiment's holdout window, in any experiment: verified and exploratory apart.
  window: { bars: string; holdout_start: string; verified: HoldoutEvaluation[]; exploratory: HoldoutEvaluation[] };
};
export type RunResult = {
  record: { code_sha256: string; result_sha256: string; runtime: { qrs: string; qrs_source_sha256: string } };
  evidence_class: EvidenceClass; // the weakest among the datasets the run read
  results: Record<string, BacktestOutput>;
};

// Research (Stage 5): projects, families, analyses and reports.
export type FamilyStatus = FamilyView & {
  document: FamilyDocument;
  sha256: string;
  registered_at: string;
  withdrawals: { hypothesis: string; reason: string }[];
  evaluations: { hypothesis: string; analysis: AnalysisInfo | null; error: string | null }[];
};
export type ProjectSummary = {
  id: string;
  document: ProjectDocument;
  created_at: string;
  notes: { revision: number; text: string; saved_at: string }[];
  horizons: Record<string, Horizon>;
  families: FamilyStatus[];
  analyses: Record<string, number>; // how many of its analyses ended each way
};
export type AnalysisResult = {
  document: AnalysisDocument;
  record: { result_sha256: string };
  result: { values: Record<string, unknown>; warnings: string[]; assumptions: string[]; evidence_class: EvidenceClass; cutoff: string | null; outputs: Record<string, 'rows' | 'aggregates'> };
  tables: Record<string, Record<string, unknown>[]>;
};
export type ReportSummary = { id: string; document: ReportDocument; record?: { report_sha256: string } };

export const api = {
  operators: () => request<OperatorInfo[]>('/operators'),
  templates: () => request<Template[]>('/templates'),
  datasets: () => request<ListedDataset[]>('/datasets'),
  strategies: () => request<StrategySummary[]>('/strategies'),
  createFromTemplate: (template_id: string, datasets: Record<string, string>) => request<StrategyRecord>('/strategies', post({ template_id, datasets })),
  createStrategy: (document: StrategyDocument) => request<StrategyRecord>('/strategies', post({ document })),
  importDataset: (file: File, declaration: ImportDeclaration) =>
    request<Dataset>(`/datasets/import?declaration=${encodeURIComponent(JSON.stringify(declaration))}`, { method: 'POST', body: file, headers: { 'Content-Type': 'text/csv' } }),
  strategy: (id: string) => request<StrategyRecord>(`/strategies/${id}`),
  save: (id: string, revision: number, document: StrategyDocument) => request<{ revision: number }>(`/strategies/${id}`, { method: 'PUT', body: JSON.stringify({ revision, document }) }),
  compile: (document: StrategyDocument) => request<Compiled>('/compile', post({ document })),
  startRun: (id: string, revision: number) => request<RunInfo>(`/strategies/${id}/runs`, post({ revision })),
  runs: (strategyId?: string) => request<RunInfo[]>(strategyId ? `/runs?strategy_id=${strategyId}` : '/runs'),
  rerun: (runId: string) => request<RunInfo>(`/runs/${runId}/rerun`, { method: 'POST' }),
  compare: (run_ids: string[]) => request<Comparison>('/compare', post({ run_ids })),
  cancel: (runId: string) => request<RunInfo>(`/runs/${runId}/cancel`, { method: 'POST' }),
  result: (runId: string) => request<RunResult>(`/runs/${runId}/result`),
  exportUrl: (id: string, revision: number) => `/api/strategies/${id}/export?revision=${revision}`,
  logUrl: (runId: string) => `/api/runs/${runId}/log`,
  experiments: (strategyId: string) => request<ExperimentOverview[]>(`/experiments?strategy_id=${strategyId}`),
  experiment: (id: string) => request<ExperimentSummary>(`/experiments/${id}`),
  createExperiment: (experiment: Experiment) => request<ExperimentSummary>('/experiments', post(experiment)),
  cancelExperiment: (id: string) => request<ExperimentSummary>(`/experiments/${id}/cancel`, { method: 'POST' }),
  evaluateHoldout: (id: string, trial: number) => request<ExperimentSummary>(`/experiments/${id}/holdout`, post({ trial })),
  allExperiments: () => request<ExperimentOverview[]>('/experiments'),
  methods: () => request<MethodInfo[]>('/methods'),
  analyses: () => request<AnalysisInfo[]>('/analyses'),
  createAnalysis: (document: AnalysisDocument) => request<AnalysisInfo>('/analyses', post(document)),
  analysisResult: (id: string) => request<AnalysisResult>(`/analyses/${id}/result`),
  figureUrl: (id: string, name: string) => `/api/analyses/${id}/figures/${encodeURIComponent(name)}`,
  projects: () => request<{ id: string; document: ProjectDocument }[]>('/projects'),
  project: (id: string) => request<ProjectSummary>(`/projects/${id}`),
  createProject: (document: ProjectDocument) => request<ProjectSummary>('/projects', post(document)),
  addNote: (id: string, text: string) => request<ProjectSummary>(`/projects/${id}/notes`, post({ text })),
  registerFamily: (document: FamilyDocument) => request<FamilyStatus>('/families', post(document)),
  evaluate: (id: string, hypothesis: string) => request<FamilyStatus>(`/families/${id}/evaluate`, post({ hypothesis })),
  withdraw: (id: string, hypothesis: string, reason: string) => request<FamilyStatus>(`/families/${id}/withdraw`, post({ hypothesis, reason })),
  reports: () => request<ReportSummary[]>('/reports'),
  buildReport: (document: ReportDocument) => request<ReportSummary>('/reports', post(document)),
  verifyReport: (id: string) => request<{ verified: boolean; problems: string[] }>(`/reports/${id}/verify`, { method: 'POST' }),
  reportUrl: (id: string) => `/api/reports/${id}/html`,
};
