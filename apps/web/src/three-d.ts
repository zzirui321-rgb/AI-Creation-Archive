import { creationView, groupFor } from './creation-view';
import { groupArtifacts, resultFormat } from './result-projection';
import type { WorkspaceProject } from './workspace';
import type { WorkflowModule } from './workflow';
import type { RodinJob } from './ThreeDPanel';

export function generationTarget(project: WorkspaceProject, selectedId: string | null, mode: 'generate' | 'regenerate', artifactId?: string) {
  const modules = project.workflow.modules;
  const selected = modules.find(node => node.id === selectedId);
  const isModel = (node: WorkflowModule) => node.artifacts.some(item => item.mime === 'model/gltf-binary');
  const model = mode === 'regenerate' ? selected && isModel(selected) ? selected : [...modules].reverse().find(node => isModel(node) && (!selected || node.inputs.source_node_id === selected.id && (!artifactId || node.inputs.source_artifact_id === artifactId))) : selected && isModel(selected) ? selected : undefined;
  if (mode === 'regenerate' && !model) return;
  const source = model ? modules.find(node => node.id === model.inputs.source_node_id) : selected ?? modules.find(node => node.artifacts.some(item => item.mime.startsWith('image/')));
  if (!source) return;
  const images = [...source.artifacts, ...groupArtifacts(project.workflow, groupFor(project.workflow, source.id))].filter(item => item.mime.startsWith('image/'));
  const artifact = model ? images.find(item => item.id === model.inputs.source_artifact_id) : artifactId ? images.find(item => item.id === artifactId) : images[0];
  if (!artifact) return;
  return { nodeId: source.id, artifact, prompt: mode === 'regenerate' ? model!.prompt : '', mode };
}

export function attachModel(project: WorkspaceProject, job: RodinJob): WorkspaceProject {
  if (job.state !== 'succeeded' || !job.artifact) throw new Error('3D 模型尚未完成');
  const id = `rodin-${job.id}`;
  if (project.workflow.modules.some(node => node.id === id)) return project;
  const parent = project.workflow.modules.find(node => node.id === job.recipe.source_node);
  if (!parent) throw new Error('源图片节点不存在');
  const artifact = { ...job.artifact, uri: `/api/3d/jobs/${job.id}/model?repo_id=${project.id}` };
  const version = project.workflow.modules.filter(item => item.inputs?.source_node_id === parent.id && item.artifacts.some(file => file.mime === 'model/gltf-binary')).length + 1;
  const node: WorkflowModule = { id, title: `${parent.title} · 3D 模型${version > 1 ? ` · 版本 ${version}` : ''}`, kind: 'mesh', mode: 'image-to-3d', model: `Hyper3D Rodin ${job.recipe.tier}`,
    harness: 'AiCA Hyper3D 后端适配器', prompt: job.recipe.prompt, params: { tier: job.recipe.tier, material: 'PBR', format: 'glb', quality: 'medium', mesh_mode: 'Raw' },
    inputs: { source_node_id: parent.id, source_artifact_id: job.recipe.source_artifact, source_commit_id: job.recipe.source_commit, source_image_sha256: job.recipe.input_sha256 },
    outputs: { uri: artifact.uri, mime: artifact.mime }, provenance: 'recorded', evidence: [{ provider: 'hyper3d', job_id: job.id, recipe: job.recipe }],
    status: 'succeeded', dead: false, artifacts: [artifact] };
  const view = structuredClone(creationView(project.workflow));
  const group = view.nodes.find(item => item.member_node_ids.includes(parent.id));
  const groupId = `result-${id}`, taskId = `task-${id}`;
  const edge = { source: parent.id, target: id, kind: 'data' as const, state: 'verified' as const, relation: 'image_to_3d' };
  return { ...project, coverArtifact: project.coverArtifact ?? parent.artifacts.find(item => item.mime.startsWith('image/')), updatedAt: Date.now(), status: 'draft', workflow: { ...project.workflow,
    modules: [...project.workflow.modules, node], edges: [...project.workflow.edges, edge],
    artifacts: [...(project.workflow.artifacts ?? []), artifact],
    creationView: { ...view, format: resultFormat, raw_node_count: project.workflow.modules.length + 1,
      tasks: [...(view.tasks ?? []), { id: taskId, label: '3D 建模', node_ids: [groupId], basis: 'declared' }],
      nodes: [...view.nodes, { id: groupId, label: node.title, member_node_ids: [id], primary_node_id: id, grouping: 'result', task_id: taskId, result_inline_artifacts: [artifact], result_basis: 'recorded_output' }],
      edges: group ? [...view.edges, { ...edge, source: group.id, target: groupId }] : view.edges } } };
}
