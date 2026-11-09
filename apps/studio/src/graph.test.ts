import { describe, expect, it } from 'vitest';
import type { OperatorInfo, StrategyDocument } from './contracts.gen';
import { addNode, canConnect, connect, edgeId, moveNodes, removeEdges, removeNodes, setParam } from './graph';

const op = (id: string, inputs: [string, string][], outputs: [string, string][]): OperatorInfo => ({
  id,
  version: 1,
  title: id,
  description: '',
  family: 'test',
  alignment: 'same_index',
  inputs: inputs.map(([name, type]) => ({ name, type })),
  outputs: outputs.map(([name, type]) => ({ name, type })),
  params_schema: {},
});

const OPS = [op('qrs.dataset', [], [['bars', 'bars']]), op('qrs.ema', [['bars', 'bars']], [['series', 'series']]), op('qrs.cross', [['a', 'series'], ['b', 'series']], [['signal', 'signal']])];
const ref = (node: string, port: string) => ({ node, port });

function build(): StrategyDocument {
  let doc: StrategyDocument = { name: 'test', nodes: [], edges: [], layout: {} };
  for (const [opId, pos] of [['qrs.dataset', 0], ['qrs.ema', 1], ['qrs.ema', 2], ['qrs.cross', 3]] as const) doc = addNode(doc, OPS.find((o) => o.id === opId)!, { x: pos * 100.4, y: 0 }).doc;
  return doc;
}

describe('graph edits', () => {
  it('creates unique, valid node IDs and rounded positions', () => {
    const doc = build();
    expect(doc.nodes!.map((n) => n.id)).toEqual(['dataset_1', 'ema_1', 'ema_2', 'cross_1']);
    expect(doc.layout!.ema_1).toEqual({ x: 100, y: 0 });
  });

  it('lets one output feed several inputs (fan-out)', () => {
    let doc = connect(build(), ref('dataset_1', 'bars'), ref('ema_1', 'bars'));
    doc = connect(doc, ref('dataset_1', 'bars'), ref('ema_2', 'bars'));
    doc = connect(doc, ref('ema_1', 'series'), ref('cross_1', 'a'));
    doc = connect(doc, ref('ema_2', 'series'), ref('cross_1', 'b'));
    expect(doc.edges!.map(edgeId)).toEqual(['dataset_1.bars->ema_1.bars', 'dataset_1.bars->ema_2.bars', 'ema_1.series->cross_1.a', 'ema_2.series->cross_1.b']);
  });

  it('replaces the existing connection into an input', () => {
    let doc = connect(build(), ref('ema_1', 'series'), ref('cross_1', 'a'));
    doc = connect(doc, ref('ema_2', 'series'), ref('cross_1', 'a'));
    expect(doc.edges!.map(edgeId)).toEqual(['ema_2.series->cross_1.a']);
  });

  it('only connects matching port types on different nodes', () => {
    const doc = build();
    expect(canConnect(OPS, doc, ref('dataset_1', 'bars'), ref('ema_1', 'bars'))).toBe(true);
    expect(canConnect(OPS, doc, ref('dataset_1', 'bars'), ref('cross_1', 'a'))).toBe(false);
    expect(canConnect(OPS, doc, ref('ema_1', 'series'), ref('ema_1', 'bars'))).toBe(false);
    expect(canConnect(OPS, doc, ref('ema_1', 'nope'), ref('cross_1', 'a'))).toBe(false);
  });

  it('connects a label only to a label input', () => {
    const ops = [...OPS, op('tests.target', [['bars', 'bars']], [['label', 'label']]), op('tests.fit', [['target', 'label']], [['model', 'model']])];
    let doc = build();
    doc = addNode(doc, ops.find((o) => o.id === 'tests.target')!, { x: 0, y: 100 }).doc;
    doc = addNode(doc, ops.find((o) => o.id === 'tests.fit')!, { x: 100, y: 100 }).doc;
    expect(canConnect(ops, doc, ref('target_1', 'label'), ref('cross_1', 'a'))).toBe(false);
    expect(canConnect(ops, doc, ref('target_1', 'label'), ref('fit_1', 'target'))).toBe(true);
  });

  it('removes nodes with their connections and layout', () => {
    let doc = connect(build(), ref('dataset_1', 'bars'), ref('ema_1', 'bars'));
    doc = removeNodes(doc, ['dataset_1']);
    expect(doc.nodes!.map((n) => n.id)).toEqual(['ema_1', 'ema_2', 'cross_1']);
    expect(doc.edges).toEqual([]);
    expect(doc.layout!.dataset_1).toBeUndefined();
    expect(removeEdges(connect(doc, ref('ema_1', 'series'), ref('cross_1', 'a')), ['ema_1.series->cross_1.a']).edges).toEqual([]);
  });

  it('sets and clears parameters, and moves nodes', () => {
    let doc = setParam(build(), 'ema_1', 'period', 9);
    expect(doc.nodes![1].params).toEqual({ period: 9 });
    doc = setParam(doc, 'ema_1', 'period', undefined);
    expect(doc.nodes![1].params).toEqual({});
    expect(moveNodes(doc, { ema_1: { x: 10.6, y: 20.2 } }).layout!.ema_1).toEqual({ x: 11, y: 20 });
  });
});
