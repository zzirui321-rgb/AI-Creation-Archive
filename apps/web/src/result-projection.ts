import type { CreationGroup, CreationTask, CreationView } from './creation-view';
import type { ModuleArtifact, Workflow, WorkflowModule } from './workflow';

export const resultFormat = 'aica-creation-view/0.3';
const failed = new Set(['failed', 'error', 'cancelled', 'canceled', 'running', 'pending', 'queued']);
const utility = /(?:^|[._/ -])(read|list|save|copy|download|upload|write_file|status|inspect)(?:$|[._/ -])/i;
function mime(uri: string) { return /\.(mp4|webm)(?:\?|$)/i.test(uri) ? 'video/mp4' : /\.(pdf)(?:\?|$)/i.test(uri) ? 'application/pdf' : /\.(glb|gltf)(?:\?|$)/i.test(uri) ? 'model/gltf-binary' : 'image/png'; }

export function recordedOutput(value: unknown): { media: ModuleArtifact[]; text: string } {
  const media: ModuleArtifact[] = [], text: string[] = [];
  function visit(v: unknown) {
    if (typeof v === 'string') {
      if (/^\s*[\[{]/.test(v)) { try { visit(JSON.parse(v)); return; } catch { /* Plain output text can begin with brackets. */ } }
      if (/^(https?:\/\/|data:image\/)/i.test(v) || /\.(png|jpe?g|webp|mp4|webm|pdf|glb|gltf)$/i.test(v)) media.push({ id: v, uri: v, mime: mime(v), label: '记录中的生成结果' });
      else if (v.trim() && !/^(ok|done|success|completed|已完成|生成完成)[.!。！\s]*$/i.test(v.trim())) text.push(v);
    } else if (Array.isArray(v)) v.forEach(visit);
    else if (v && typeof v === 'object') {
      const record = v as Record<string, unknown>;
      const uri = record.uri || record.url;
      if (typeof uri === 'string') media.push({ id: typeof record.id === 'string' ? record.id : uri, uri, mime: typeof (record.mime || record.mime_type) === 'string' ? String(record.mime || record.mime_type) : mime(uri), label: typeof record.label === 'string' ? record.label : '记录中的生成结果' });
      for (const key of ['text', 'output_text', 'images', 'videos', 'artifacts', 'results', 'data', 'output', 'outputs', 'result', 'content', 'choices', 'message', 'image_url', 'video_url']) if (record[key] !== undefined) visit(record[key]);
    }
  }
  visit(value);
  return { media: [...new Map(media.map(a => [a.uri, a])).values()], text: [...new Set(text)].join('\n') };
}

export function groupArtifacts(workflow: Workflow, group?: CreationGroup): ModuleArtifact[] {
  if (!group) return [];
  const known = [...(workflow.artifacts ?? []), ...workflow.modules.flatMap(n => n.artifacts)];
  const ids = (group.result_artifact_ids ?? []).map(id => known.find(a => a.id === id) ?? { id, uri: '', mime: '', label: '已记录结果，文件暂不可用' });
  const inline = (group.result_inline_artifacts ?? []).map((a, i) => ({ id: a.id || `${group.id}-${i}`, uri: a.uri, mime: a.mime, label: a.label || '本次生成结果' }));
  return [...new Map([...ids, ...inline].map(a => [a.uri || a.id, a])).values()];
}

export function projectResults(workflow: Workflow): CreationView {
  const tasks: CreationTask[] = [], nodes: CreationGroup[] = [];
  const taskMap = new Map<string, CreationTask>();
  const assigned = new Map<string, string>();
  let owner: WorkflowModule | undefined;
  for (const n of workflow.modules) {
    if (n.kind === 'intent') owner = n;
    if (failed.has(n.status ?? '') || ['intent', 'document', 'collection'].includes(n.kind) || (n.kind === 'tool' && utility.test(String(n.environment?.tool || n.title)))) continue;
    if (n.kind === 'decision' && workflow.modules.some(m => m.kind === 'tool' && m.intentId === n.intentId)) continue;
    const result = recordedOutput(n.outputs);
    const media = [...n.artifacts.filter(a => /^(image|video|audio|model)\//.test(a.mime) || a.mime === 'application/pdf'), ...result.media];
    if (!media.length && !result.text) continue;
    const old = workflow.creationView?.nodes.find(g => g.member_node_ids.includes(n.id));
    const label = n.groupLabel || workflow.creationView?.tasks?.find(t => t.id === old?.task_id)?.label || n.title.replace(/[-_\s]*\d+(?:[-_\s].*)?$/, '') || n.title;
    let task = taskMap.get(label);
    if (!task) { task = { id: `task-${n.id}`, label, node_ids: [], basis: n.groupLabel ? 'declared' : 'inferred' }; taskMap.set(label, task); tasks.push(task); }
    const group: CreationGroup = { id: `result-${n.id}`, label: n.title, grouping: 'result', primary_node_id: n.id, member_node_ids: [n.id], task_id: task.id, iteration: task.node_ids.length + 1, user_node_id: n.intentId || (n.kind === 'tool' ? owner?.id : undefined), result_inline_artifacts: [...new Map(media.map(a => [a.uri || a.id, a])).values()], result_text: result.text, result_basis: 'recorded_output' };
    nodes.push(group); task.node_ids.push(group.id); assigned.set(n.id, group.id);
  }
  return { format: resultFormat, tasks, nodes, edges: workflow.edges.flatMap(e => { const source = assigned.get(e.source), target = assigned.get(e.target); return source && target && source !== target && e.relation !== 'supports' ? [{ ...e, source, target }] : []; }), supporting_records: workflow.modules.filter(n => !assigned.has(n.id)).map(n => ({ id: n.id, label: n.title })), raw_node_count: workflow.modules.length };
}
