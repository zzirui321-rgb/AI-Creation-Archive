import type { ModulePreview, WorkspaceProject } from './workspace';
import { creationView } from './creation-view';
import { resultFormat } from './result-projection';
import type { WorkflowModule } from './workflow';

export function previewId(preview: ModulePreview) { return preview.id || `${preview.nodeId}-${preview.createdAt}`; }
export function matchesPreview(preview: ModulePreview, node: WorkflowModule) {
  return preview.nodeId === node.id && preview.prompt === node.prompt && (node.generatedPreviewId === previewId(preview) ? preview.model === node.model : preview.localEdit ? preview.localEdit.sourceModel === node.model : !node.model || preview.model === node.model);
}
export function historyOf(project: WorkspaceProject) {
  const values = [...(project.previewHistory ?? []), ...(project.preview ? [project.preview] : [])];
  return Array.from(new Map(values.map(value => [previewId(value), value])).values()).sort((a, b) => b.createdAt - a.createdAt);
}
export function rememberPreview(project: WorkspaceProject, preview: ModulePreview): WorkspaceProject {
  return { ...project, preview, previewHistory: [preview, ...historyOf(project).filter(value => previewId(value) !== previewId(preview))], updatedAt: Date.now(), status: 'draft' };
}
export function restorePreview(project: WorkspaceProject, preview: ModulePreview): WorkspaceProject {
  return { ...rememberPreview(project, preview), coverArtifact: preview.stage === 'final' && !preview.simulated ? preview.artifact : project.coverArtifact, workflow: { ...project.workflow, modules: project.workflow.modules.map(node => node.id === preview.nodeId ? { ...node, prompt: preview.prompt, model: preview.localEdit && node.generatedPreviewId !== previewId(preview) ? preview.localEdit.sourceModel ?? node.model : preview.model } : node) } };
}
export function confirmPreview(project: WorkspaceProject): WorkspaceProject {
  const preview = project.preview;
  const node = project.workflow.modules.find(node => node.id === preview?.nodeId);
  if (!preview || (!preview.simulated && !preview.artifact) || !node || !matchesPreview(preview, node)) throw new Error('预览与当前节点不同，请先重新生成。');
  if (preview.simulated || node.generatedPreviewId === previewId(preview)) {
    const next = rememberPreview(project, { ...preview, stage: 'final' });
    return { ...next, coverArtifact: preview.simulated ? project.coverArtifact : preview.artifact };
  }
  const identity = previewId(preview), id = `generated-${identity}`;
  const view = structuredClone(creationView(project.workflow));
  const parent = view.nodes.find(g => g.primary_node_id === node.id || g.member_node_ids.includes(node.id));
  const tasks = view.tasks?.length ? view.tasks : [{ id: `task-${node.id}`, label: node.groupLabel || '创作结果', node_ids: view.nodes.map(g => g.id), basis: 'declared' }];
  const taskId = parent?.task_id || tasks[0].id;
  const task = tasks.find(t => t.id === taskId) ?? tasks[0];
  const number = task.node_ids.length + 1;
  const artifact = { id: identity, uri: preview.artifact!.uri, mime: preview.artifact!.mime, label: '确认生成结果' };
  const resultPrompt = preview.localEdit?.instruction || preview.prompt;
  const resultNode = { ...node, id, title: `${node.title.replace(/ · 版本 \d+$/, '')} · 版本 ${number}`, kind: artifact.mime.startsWith('video/') ? 'video' : 'image', prompt: resultPrompt, model: preview.model, mode: preview.localEdit ? 'local-image-edit' : artifact.mime.startsWith('video/') ? 'video' : 'image', harness: 'AiCA 本地预览适配器', environment: {}, params: {}, inputs: { source_node_id: node.id, ...(preview.localEdit ? { source_artifact_uri: preview.localEdit.sourceUri, local_edit: preview.localEdit } : {}) }, outputs: { uri: artifact.uri, mime: artifact.mime }, status: 'succeeded', provenance: 'recorded', evidence: [], artifacts: [artifact], dead: false, generatedPreviewId: identity, groupLabel: task.label };
  const groupId = `result-${id}`;
  task.node_ids.push(groupId);
  const relation = { kind: 'semantic' as const, state: 'candidate' as const, relation: 'revision', explanation: '用户确认了基于此节点的新生成结果；版本关系不表示可执行端口依赖。' };
  const workflow = { ...project.workflow, modules: [...project.workflow.modules, resultNode], edges: [...project.workflow.edges, { id: `version-${id}`, source: node.id, target: id, ...relation }], creationView: { ...view, format: resultFormat, tasks, nodes: [...view.nodes, { id: groupId, label: resultNode.title, primary_node_id: id, member_node_ids: [id], grouping: 'result', task_id: task.id, iteration: number, result_inline_artifacts: [artifact], result_basis: 'recorded_output' }], edges: parent ? [...view.edges, { id: `version-view-${id}`, source: parent.id, target: groupId, ...relation }] : view.edges, supporting_records: view.supporting_records } };
  const next = rememberPreview({ ...project, workflow, basePrompts: { ...project.basePrompts, [id]: resultPrompt }, baseModels: { ...project.baseModels, [id]: preview.model } }, { ...preview, id: identity, nodeId: id, prompt: resultPrompt, stage: 'final' });
  return { ...next, coverArtifact: preview.artifact };
}
