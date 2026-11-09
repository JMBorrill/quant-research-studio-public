// The strategy canvas (React Flow), adapted from AI Workflow Studio's Canvas. The document is the
// source of truth: nodes and edges are derived from it, positions are written back when a drag
// ends, and every node is drawn generically from its operator's declared ports, so new operators
// need no editor changes. Handles carry their port type; only matching types connect. Each node
// shows its effective warm-up once the strategy is valid, and the palette groups operators by family.
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, applyNodeChanges, useReactFlow, type Connection, type Edge, type Node, type NodeChange, type NodeProps } from '@xyflow/react';
import type { Issue, Node as StrategyNode, OperatorInfo, StrategyDocument } from './contracts.gen';
import { addNode, canConnect, connect, edgeId, findOperator, moveNodes, portType, removeEdges, removeNodes } from './graph';
import type { Update } from './useStrategy';

const DRAG_TYPE = 'application/x-qrs-operator';
// Pipeline order for the palette; a family not listed here follows these, alphabetically.
const FAMILY_ORDER = ['sources', 'indicators', 'rules', 'positions', 'evaluation'];
const familyRank = (family: string) => (FAMILY_ORDER.includes(family) ? FAMILY_ORDER.indexOf(family) : FAMILY_ORDER.length);

type CardData = { node: StrategyNode; op?: OperatorInfo; issues: number; warmUp?: number };
type CardNode = Node<CardData, 'card'>;

const Card = memo(function Card({ data, selected }: NodeProps<CardNode>) {
  const { node, op, issues, warmUp } = data;
  return (
    <div className="node" data-selected={selected || undefined} data-issues={issues || undefined}>
      <div className="node__head">
        <span className="node__title">{op?.title ?? `${node.operator}@${node.version}`}</span>
        {issues > 0 && (
          <span className="node__issues" title={`${issues} problem${issues === 1 ? '' : 's'}`}>
            {issues}
          </span>
        )}
      </div>
      <div className="node__id">
        {node.id}
        {warmUp !== undefined && (
          <span className="node__warmup" title="Effective warm-up: sessions before this node's outputs can be valid">
            warm-up {warmUp}
          </span>
        )}
      </div>
      <div className="node__ports">
        <div>
          {op?.inputs.map((p) => (
            <div key={p.name} className="port port--in" title={`${p.name}: ${p.type}`}>
              <Handle type="target" position={Position.Left} id={p.name} className="handle" data-type={p.type} />
              {p.name}
            </div>
          ))}
        </div>
        <div>
          {op?.outputs.map((p) => (
            <div key={p.name} className="port port--out" title={`${p.name}: ${p.type}`}>
              {p.name}
              <Handle type="source" position={Position.Right} id={p.name} className="handle" data-type={p.type} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
});

const nodeTypes = { card: Card };

type Props = {
  doc: StrategyDocument;
  ops: OperatorInfo[];
  issues: Issue[];
  warmUp: Record<string, number> | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  update: Update;
};

export function Canvas({ doc, ops, issues, warmUp, selectedId, onSelect, update }: Props) {
  const flow = useReactFlow();
  const wrapper = useRef<HTMLDivElement>(null);
  const issueCount = useMemo(() => {
    const counts = new Map<string, number>();
    for (const i of issues) if (i.node) counts.set(i.node, (counts.get(i.node) ?? 0) + 1);
    return counts;
  }, [issues]);

  const derived = useMemo<CardNode[]>(
    () =>
      (doc.nodes ?? []).map((n) => ({
        id: n.id,
        type: 'card',
        position: doc.layout?.[n.id] ?? { x: 0, y: 0 },
        selected: n.id === selectedId,
        ariaLabel: `${findOperator(ops, n.operator, n.version)?.title ?? n.operator}: ${n.id}`,
        data: { node: n, op: findOperator(ops, n.operator, n.version), issues: issueCount.get(n.id) ?? 0, warmUp: warmUp?.[n.id] },
      })),
    [doc, ops, selectedId, issueCount, warmUp],
  );
  const families = useMemo(() => [...new Set(ops.map((o) => o.family))].sort((a, b) => familyRank(a) - familyRank(b) || a.localeCompare(b)), [ops]);
  // Local copy so dragging is smooth; re-derived whenever the document or its check changes. Measured
  // sizes are carried over: without them React Flow drops each node's handle positions and measures
  // it again, and a connection released meanwhile is lost.
  const [nodes, setNodes] = useState<CardNode[]>(derived);
  useEffect(() => setNodes((current) => {
    const measured = new Map(current.map((n) => [n.id, n.measured]));
    return derived.map((n) => ({ ...n, measured: measured.get(n.id) }));
  }), [derived]);

  const edges = useMemo<Edge[]>(
    () =>
      (doc.edges ?? []).map((e) => ({
        id: edgeId(e),
        source: e.source.node,
        sourceHandle: e.source.port,
        target: e.target.node,
        targetHandle: e.target.port,
        className: `edge edge--${portType(ops, doc, e.source, 'outputs') ?? 'unknown'}`,
      })),
    [doc, ops],
  );

  const ref = (c: Connection | Edge) => [
    { node: c.source, port: c.sourceHandle ?? '' },
    { node: c.target, port: c.targetHandle ?? '' },
  ] as const;

  const onNodesChange = useCallback(
    (changes: NodeChange<CardNode>[]) => {
      setNodes((ns) => applyNodeChanges(changes, ns));
      const selected = changes.find((c) => c.type === 'select' && c.selected);
      if (selected && selected.type === 'select') onSelect(selected.id);
      // A finished drag and each keyboard move arrive as position changes that are not dragging;
      // repeated moves of the same nodes within a second are one undo step.
      const moved = changes.flatMap((c) => (c.type === 'position' && c.position && !c.dragging ? [[c.id, c.position] as const] : []));
      if (moved.length) update((d) => moveNodes(d, Object.fromEntries(moved)), `move:${moved.map(([id]) => id).join(',')}`);
    },
    [update, onSelect],
  );

  const add = useCallback(
    (op: OperatorInfo, screen?: { x: number; y: number }) => {
      const box = wrapper.current!.getBoundingClientRect();
      const at = flow.screenToFlowPosition(screen ?? { x: box.left + box.width / 2, y: box.top + box.height / 2 });
      let created = '';
      update((d) => {
        const r = addNode(d, op, { x: at.x - 90, y: at.y - 40 });
        created = r.id;
        return r.doc;
      });
      if (created) onSelect(created);
    },
    [flow, update, onSelect],
  );

  return (
    <div className="canvas" ref={wrapper}>
      <aside className="palette" aria-label="Operators">
        <h2>Operators</h2>
        {families.map((family) => (
          <section key={family} className="palette__family">
            <h3>{family}</h3>
            {ops
              .filter((op) => op.family === family)
              .sort((a, b) => a.title.localeCompare(b.title))
              .map((op) => (
                <button
                  key={`${op.id}@${op.version}`}
                  className="palette__item"
                  draggable
                  title={op.description}
                  onDragStart={(e) => e.dataTransfer.setData(DRAG_TYPE, `${op.id}@${op.version}`)}
                  onClick={() => add(op)}
                >
                  <span>{op.title}</span>
                  <small>
                    {op.id}@{op.version}
                  </small>
                </button>
              ))}
          </section>
        ))}
      </aside>
      <div
        className="canvas__flow"
        onDragOver={(e) => e.dataTransfer.types.includes(DRAG_TYPE) && e.preventDefault()}
        onDrop={(e) => {
          const key = e.dataTransfer.getData(DRAG_TYPE);
          const op = ops.find((o) => `${o.id}@${o.version}` === key);
          if (op) add(op, { x: e.clientX, y: e.clientY });
        }}
      >
        <ReactFlow<CardNode>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onDelete={({ nodes: deletedNodes, edges: deletedEdges }) => {
            // Nodes and their connections go in one edit, so one undo restores them together.
            update((d) => removeNodes(removeEdges(d, deletedEdges.map((e) => e.id)), deletedNodes.map((n) => n.id)));
            if (deletedNodes.length) onSelect(null);
          }}
          isValidConnection={(c) => canConnect(ops, doc, ...ref(c))}
          onConnect={(c) => update((d) => connect(d, ...ref(c)))}
          onPaneClick={() => onSelect(null)}
          deleteKeyCode={['Delete', 'Backspace']}
          fitView
          fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
          minZoom={0.3}
          maxZoom={1.6}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={22} size={1} />
          <Controls showInteractive={false} position="bottom-right" />
        </ReactFlow>
      </div>
    </div>
  );
}
