import { type Workflow, type WorkflowModule, type WorkflowEdge } from './workflow';
import { groupArtifacts, projectResults, resultFormat } from './result-projection';
import { layoutResults } from './graph-layout';

export type CreationGroup = { id: string; label: string; member_node_ids: string[]; grouping: string; primary_node_id?: string; user_node_id?: string | null; user_prompt?: string | null; task_id?: string; iteration?: number; result_artifact_ids?: string[]; result_inline_artifacts?: { id?: string; uri: string; mime: string; label?: string }[]; result_text?: string; result_basis?: string; pending_association?: boolean; candidate_node_ids?: string[] };
export type CreationTask = { id: string; label: string; node_ids: string[]; basis: string };
export type CreationView = { format?: string; tasks?: CreationTask[]; nodes: CreationGroup[]; edges: WorkflowEdge[]; supporting_records: { id: string; label: string }[]; raw_node_count: number };

export function creationView(workflow: Workflow): CreationView {
  if (workflow.creationView?.format === resultFormat) return workflow.creationView;
  if (workflow.source === 'demo') return { nodes: workflow.modules.map(n => ({ id: n.id, label: n.title, member_node_ids: [n.id], grouping: 'record' })), edges: workflow.edges, supporting_records: [], raw_node_count: workflow.modules.length };
  return projectResults(workflow);
}

export function iterationLayout(workflow: Workflow, vertical: boolean) {
  const layout = layoutResults(workflow, creationView(workflow), vertical);
  return layout ? { ...layout, frames: layout.frames.map(frame => ({ ...frame, label: phaseLabel(frame.label) })) } : null;
}

export function groupFor(workflow: Workflow, id: string) { return creationView(workflow).nodes.find(g => g.member_node_ids.includes(id)); }
export function phaseLabel(label: string) { return ({ characters: '主体设定', character: '主体设定', cover: '封面设计', page: '页面创作', shot: '镜头创作', frame: '画面创作', mesh: '网格建模', texture: '材质创作', image: '图像创作', video: '视频创作', audio: '音频创作' } as Record<string, string>)[label.toLowerCase()] ?? label; }

export function displayWorkflow(workflow: Workflow, selected: string | null): Workflow {
  const view = creationView(workflow);
  return { ...workflow, creationView: view, modules: view.nodes.flatMap(group => {
    const member = workflow.modules.find(n => n.id === group.primary_node_id) ?? workflow.modules.find(n => n.id === selected && group.member_node_ids.includes(n.id)) ?? workflow.modules.find(n => group.member_node_ids.includes(n.id));
    return member ? [{ ...member, id: group.id, title: phaseLabel(group.label), artifacts: group.grouping === 'result' ? groupArtifacts(workflow, group) : member.artifacts, resultText: group.result_text, resultPending: group.pending_association }] : [];
  }), edges: view.edges, goldenPath: view.nodes.filter(g => g.member_node_ids.some(id => workflow.goldenPath.includes(id))).map(g => g.id) };
}

export function environmentOf(node: WorkflowModule): Record<string, unknown> {
  const result = { ...(node.environment ?? {}) };
  for (const [key, value] of Object.entries(node.params)) if (['reasoning_effort', 'thinking_depth', 'thinking_budget', 'temperature', 'seed', 'top_p'].includes(key) && result[key] === undefined) result[key] = value;
  return result;
}
