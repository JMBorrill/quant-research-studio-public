// Pure edits on a strategy document, adapted from AI Workflow Studio's editor ops. Every editor
// action goes through these and returns a new document. Connections are typed: an output may feed
// any number of inputs of the same port type, and each input takes one connection, so a new
// connection to an input replaces the old one. Other graph problems are reported by server-side
// validation rather than prevented here.
import type { Edge, OperatorInfo, PortRef, StrategyDocument } from './contracts.gen';

type XY = { x: number; y: number };

export const edgeId = (e: Edge) => `${e.source.node}.${e.source.port}->${e.target.node}.${e.target.port}`;

export const findOperator = (ops: OperatorInfo[], id: string, version: number) => ops.find((o) => o.id === id && o.version === version);

function uniqueId(doc: StrategyDocument, base: string): string {
  const used = new Set((doc.nodes ?? []).map((n) => n.id));
  for (let i = 1; ; i++) if (!used.has(`${base}_${i}`)) return `${base}_${i}`;
}

const round = (p: XY): XY => ({ x: Math.round(p.x), y: Math.round(p.y) });

export function addNode(doc: StrategyDocument, op: OperatorInfo, position: XY): { doc: StrategyDocument; id: string } {
  const base = op.id.split('.').pop()!.replace(/[^a-z0-9_]/g, '_');
  const id = uniqueId(doc, /^[a-z]/.test(base) ? base : `node_${base}`);
  return {
    id,
    doc: { ...doc, nodes: [...(doc.nodes ?? []), { id, operator: op.id, version: op.version, params: {} }], layout: { ...doc.layout, [id]: round(position) } },
  };
}

export function removeNodes(doc: StrategyDocument, ids: string[]): StrategyDocument {
  const drop = new Set(ids);
  return {
    ...doc,
    nodes: (doc.nodes ?? []).filter((n) => !drop.has(n.id)),
    edges: (doc.edges ?? []).filter((e) => !drop.has(e.source.node) && !drop.has(e.target.node)),
    layout: Object.fromEntries(Object.entries(doc.layout ?? {}).filter(([id]) => !drop.has(id))),
  };
}

export function connect(doc: StrategyDocument, source: PortRef, target: PortRef): StrategyDocument {
  const edges = (doc.edges ?? []).filter((e) => !(e.target.node === target.node && e.target.port === target.port));
  return { ...doc, edges: [...edges, { source, target }] };
}

export function removeEdges(doc: StrategyDocument, ids: string[]): StrategyDocument {
  const drop = new Set(ids);
  return { ...doc, edges: (doc.edges ?? []).filter((e) => !drop.has(edgeId(e))) };
}

export function moveNodes(doc: StrategyDocument, positions: Record<string, XY>): StrategyDocument {
  return { ...doc, layout: { ...doc.layout, ...Object.fromEntries(Object.entries(positions).map(([id, p]) => [id, round(p)])) } };
}

/** Set a parameter, or remove it (undefined) so the operator's default applies. */
export function setParam(doc: StrategyDocument, nodeId: string, name: string, value: unknown): StrategyDocument {
  return {
    ...doc,
    nodes: (doc.nodes ?? []).map((n) => {
      if (n.id !== nodeId) return n;
      const params = { ...n.params };
      if (value === undefined) delete params[name];
      else params[name] = value;
      return { ...n, params };
    }),
  };
}

export function portType(ops: OperatorInfo[], doc: StrategyDocument, ref: PortRef, side: 'inputs' | 'outputs'): string | undefined {
  const node = (doc.nodes ?? []).find((n) => n.id === ref.node);
  const op = node && findOperator(ops, node.operator, node.version);
  return op?.[side].find((p) => p.name === ref.port)?.type;
}

export function canConnect(ops: OperatorInfo[], doc: StrategyDocument, source: PortRef, target: PortRef): boolean {
  if (source.node === target.node) return false;
  const from = portType(ops, doc, source, 'outputs');
  return from !== undefined && from === portType(ops, doc, target, 'inputs');
}
