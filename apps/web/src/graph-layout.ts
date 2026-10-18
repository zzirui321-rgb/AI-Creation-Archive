import type { CreationView } from './creation-view';
import type { Workflow } from './workflow';

function ordered(ids: string[], links: { source: string; target: string }[]) {
  const order = new Map(ids.map((id, index) => [id, index]));
  const indegree = new Map(ids.map(id => [id, 0]));
  const adjacency = new Map(ids.map(id => [id, new Set<string>()]));
  for (const edge of links) if (edge.source !== edge.target && order.has(edge.source) && order.has(edge.target)) adjacency.get(edge.source)!.add(edge.target);
  adjacency.forEach(targets => targets.forEach(id => indegree.set(id, indegree.get(id)! + 1)));
  const queue = ids.filter(id => !indegree.get(id)), result: string[] = [];
  while (queue.length) { queue.sort((a, b) => order.get(a)! - order.get(b)!); const id = queue.shift()!; result.push(id); adjacency.get(id)!.forEach(target => { indegree.set(target, indegree.get(target)! - 1); if (!indegree.get(target)) queue.push(target); }); }
  return { ids: [...result, ...ids.filter(id => !result.includes(id))], adjacency };
}

export function layoutResults(workflow: Workflow, view: CreationView, vertical: boolean) {
  if (!view.tasks?.length) return null;
  const visible = new Set(workflow.modules.map(n => n.id));
  const tasks = view.tasks.filter(t => t.node_ids.some(id => visible.has(id)));
  const owner = new Map(tasks.flatMap(t => t.node_ids.map(id => [id, t.id] as const)));
  const taskOrder = ordered(tasks.map(t => t.id), view.edges.map(e => ({ source: owner.get(e.source) ?? '', target: owner.get(e.target) ?? '' }))).ids;
  const positions: Record<string, { x: number; y: number }> = {};
  const frames: { id: string; label: string; basis: string; count: number; x: number; y: number; width: number; height: number }[] = [];
  let offset = 0;
  for (const id of taskOrder) {
    const task = tasks.find(t => t.id === id)!;
    const ids = task.node_ids.filter(id => visible.has(id));
    const edges = view.edges.filter(e => ids.includes(e.source) && ids.includes(e.target));
    const graph = ordered(ids, edges);
    const rank = new Map(ids.map(id => [id, 0]));
    if (!edges.length) graph.ids.forEach((id, index) => rank.set(id, index));
    else graph.ids.forEach((source, index) => graph.adjacency.get(source)!.forEach(target => { if (graph.ids.indexOf(target) > index) rank.set(target, Math.max(rank.get(target)!, rank.get(source)! + 1)); }));
    const lanes = new Map<number, number>();
    const local = graph.ids.map(id => { const level = rank.get(id)!; const lane = lanes.get(level) ?? 0; lanes.set(level, lane + 1); return { id, x: 28 + (vertical ? lane * 380 : level * 380), y: 76 + (vertical ? level * 410 : lane * 410) }; });
    const width = Math.max(...local.map(p => p.x)) + 280 + 28;
    const height = Math.max(...local.map(p => p.y)) + 360 + 28;
    const x = vertical ? 0 : offset, y = vertical ? offset : 0;
    local.forEach(p => { positions[p.id] = { x: x + p.x, y: y + p.y }; });
    frames.push({ id, label: task.label, basis: task.basis, count: ids.length, x, y, width, height });
    offset += (vertical ? height : width) + 120;
  }
  return { positions, frames };
}
