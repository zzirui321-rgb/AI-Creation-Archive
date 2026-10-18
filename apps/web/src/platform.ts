import { PlatformClient, type Repository } from './platform-client.mjs';
import { applyRecordedProcessView, parseWorkflow, redact } from './workflow';
import type { WorkspaceProject } from './workspace';

export const platform = new PlatformClient(typeof window === 'undefined' ? 'http://localhost' : window.location.origin);
const heads = new Map<string, string | null>();
const queues = new Map<string, Promise<unknown>>();
let userId: string | null = null;
export function setPlatformIdentity(id: string | null) { userId = id; heads.clear(); }
const key = (id: string, branch: string) => `${id}:${branch}`;
const suffix = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), n => n.toString(16).padStart(2, '0')).join('');
export function isRemote(project: WorkspaceProject) { return !project.id.startsWith('local-') && !project.id.startsWith('example-') && /^[0-9a-f-]{36}$/.test(project.id); }

export function projectDocument(project: WorkspaceProject) {

  return redact({ format: 'aica-ui-module-record', title: project.title, description: project.description,
    source_semantic_graph: project.workflow.semanticGraph,
    steps: project.workflow.modules, edges: project.workflow.edges, golden_path: project.workflow.goldenPath,
    creationView: project.workflow.creationView, artifacts: project.workflow.artifacts,
    recorded_messages: project.workflow.recordedMessages, history_issues: project.workflow.historyIssues,
    ui: { category: project.category, theme: project.theme, accent: project.accent, values: project.values,
      basePrompts: project.basePrompts, baseModels: project.baseModels, preview: project.preview, previewHistory: project.previewHistory, coverArtifact: project.coverArtifact, baselineArtifact: project.baselineArtifact } });
}

export async function loadProject(repo: Repository, branch = 'main'): Promise<WorkspaceProject> {
  const loaded = await platform.graph(repo.id, { branch });
  heads.set(key(repo.id, branch), loaded.commit.id);
  const parsed = parseWorkflow(JSON.stringify(loaded.document));
  if (loaded.process_graph) applyRecordedProcessView(parsed.workflow, loaded.process_graph);
  if (parsed.workflow.semanticGraph) for (const module of parsed.workflow.modules) {
    module.materialsUrl = `/api/repos/${repo.id}/modules/${encodeURIComponent(module.id)}/materials?commit_id=${loaded.commit.id}`;
    module.artifactBase = `/api/repos/${repo.id}/artifacts/`;
    module.artifactBase += `{id}?commit_id=${loaded.commit.id}`;
  }
  parsed.workflow.creationView ??= loaded.document.creationView as typeof parsed.workflow.creationView;
  parsed.workflow.artifacts ??= loaded.document.artifacts as typeof parsed.workflow.artifacts;
  if (loaded.commit.has_archive) {
    const assetsResponse = await platform.authorizedFetch(`/api/repos/${repo.id}/archive-info?commit_id=${loaded.commit.id}`);
    if (!assetsResponse.ok) throw new Error('归档附件目录读取失败');
    const catalog = await assetsResponse.json();
    const known = new Map<string, string>(catalog.artifacts.filter((item: { available: boolean }) => item.available).map((item: { id: string }) => [item.id, `/api/repos/${repo.id}/artifacts/${encodeURIComponent(item.id)}?commit_id=${loaded.commit.id}`]));
    for (const module of parsed.workflow.modules) {
      const semanticNode = parsed.workflow.semanticGraph?.nodes.find(node => node.id === module.id);
      if (semanticNode) {
        const ids = new Set([...semanticNode.artifact_ids, ...(semanticNode.pages ?? []).flatMap(page => page.artifact_ids)]);
        module.artifacts = catalog.artifacts.filter((item: { id: string; available: boolean }) => item.available && ids.has(item.id)).map((item: { id: string; mime: string; label: string }) => ({ id: item.id, uri: known.get(item.id)!, mime: item.mime, label: item.label }));
      } else module.artifacts = module.artifacts.map(item => ({ ...item, uri: known.get(item.id) ?? item.uri }));
    }
    parsed.workflow.artifacts = catalog.artifacts.filter((item: { available: boolean }) => item.available).map((item: { id: string; mime: string; label: string }) => ({ id: item.id, mime: item.mime, label: item.label, uri: known.get(item.id)! }));
  }
  const modelUri = (uri: string) => /^\/api\/3d\/jobs\/[a-f0-9]{32}\/model/.test(uri) ? uri.split('?')[0] + '?repo_id=' + repo.id : uri;
  for (const node of parsed.workflow.modules) for (const artifact of node.artifacts) artifact.uri = modelUri(artifact.uri);
  for (const artifact of parsed.workflow.artifacts ?? []) artifact.uri = modelUri(artifact.uri);
  for (const group of parsed.workflow.creationView?.nodes ?? []) for (const artifact of group.result_inline_artifacts ?? []) artifact.uri = modelUri(artifact.uri);
  const ui = (loaded.document.ui ?? {}) as Partial<WorkspaceProject>;
  const owned = repo.owner === `user:${userId}`;
  if (owned) {
    const response = await platform.authorizedFetch(`/api/creation-parser/previews?project_id=${repo.id}`);
    if (response.ok) {
      const jobs = await response.json();
      const history = new Map((ui.previewHistory ?? []).map(item => [item.id || `${item.nodeId}-${item.createdAt}`, item]));
      for (const item of jobs.previews ?? []) {
        if (!item.artifact || !parsed.workflow.modules.some(module => module.id === item.node_id) || history.has(item.id)) continue;
        const edit = item.edit_request;
        history.set(item.id, { id: item.id, nodeId: item.node_id, prompt: item.prompt, model: item.model, stage: item.stage,
          simulated: false, artifact: { uri: item.artifact.url, mime: item.artifact.mime, sha256: item.artifact.sha256 }, createdAt: item.created_at,
          ...(edit && item.local_edit ? { localEdit: { sourceUri: item.local_edit.source_url, instruction: edit.instruction, rect: edit.rect, featherPx: edit.feather_px,
            points: edit.points, shape: edit.shape, rotation: edit.rotation, sourceModel: parsed.workflow.modules.find(module => module.id === item.node_id)?.model } } : {}) });
      }
      ui.previewHistory = [...history.values()];
    }
  }
  return { id: repo.id, title: parsed.workflow.semanticGraph ? repo.title : parsed.title || repo.title, description: parsed.workflow.semanticGraph ? repo.description : parsed.description || repo.description,
    english: repo.name, author: repo.owner_name || repo.owner_username, avatar: (repo.owner_name || 'A').slice(0, 1),
    category: ui.category ?? '其他', theme: ui.theme ?? 'type', accent: ui.accent ?? '#9ecddd', height: 310,
    likes: 0, forks: repo.forks, date: repo.created_at.slice(0, 10), tools: [...new Set(parsed.workflow.modules.map(n => n.model).filter(Boolean))],
    values: ui.values ?? { character: repo.title, style: '平台创作档案', detail: '' }, workflow: parsed.workflow,
    owned, parentId: repo.parent_repo ?? undefined, status: repo.visibility === 'public' ? 'published' : 'draft',
    updatedAt: Date.parse(loaded.commit.created_at), commitId: loaded.commit.id, basePrompts: ui.basePrompts, baseModels: ui.baseModels,
    preview: ui.preview, previewHistory: ui.previewHistory, coverArtifact: ui.coverArtifact, baselineArtifact: ui.baselineArtifact, archiveCommit: loaded.commit.has_archive ? loaded.commit.id : undefined, branch };
}

export async function listProjects(scope: 'mine' | 'public') {
  const result = await platform.repositories({ scope, limit: 200 });
  const loaded = await Promise.all(result.repositories.map(async repo => {
    try { return await loadProject(repo); }
    catch (error) { if ((error as { status?: number }).status === 404) return null; throw error; }
  }));
  return loaded.filter((project): project is WorkspaceProject => project !== null);
}

export async function createProject(project: WorkspaceProject): Promise<WorkspaceProject> {
  const { repository } = await platform.createRepository({ name: `creation-${suffix()}`, title: project.title,
    description: project.description, visibility: 'private' });
  heads.set(key(repository.id, 'main'), null);
  return saveProject({ ...project, id: repository.id, parentId: undefined, branch: 'main', status: 'draft' });
}

export function saveProject(project: WorkspaceProject): Promise<WorkspaceProject> {
  const branch = project.branch ?? 'main';
  const queueKey = key(project.id, branch);
  const previous = queues.get(queueKey) ?? Promise.resolve();
  const result = previous.catch(() => undefined).then(async () => {
    if (!heads.has(queueKey)) throw new Error('请先重新打开分支，读取最新版本。');
    await platform.me();
    if (project.status !== 'published') await platform.setVisibility(project.id, 'private');
    const committed = await platform.commit(project.id, projectDocument(project), { branch, expectedHead: heads.get(queueKey)!, message: '保存创作记录' });
    heads.set(queueKey, committed.commit.id);
    if (project.status === 'published') await platform.setVisibility(project.id, 'public');
    return { ...project, commitId: committed.commit.id, archiveCommit: committed.commit.has_archive ? committed.commit.id : undefined };
  });
  queues.set(queueKey, result);
  return result;
}

export async function forkProject(project: WorkspaceProject) {
  if (!isRemote(project)) return createProject({ ...project, title: `${project.title} · 我的 Remix`, owned: true });
  const { repository } = await platform.fork(project.id, { name: `fork-${suffix()}`,
    title: `${project.title} · 我的 Remix`, branch: project.branch ?? 'main' });
  return { ...await loadProject(repository), title: repository.title };
}

export async function exportAgentWorkflow(project: WorkspaceProject, format: 'aica' | 'bundle') {
  const commitId = project.commitId ?? heads.get(key(project.id, project.branch ?? 'main'));
  if (!commitId) throw new Error('请先保存当前作品版本，再导出 Agent 工作流。');
  const response = await platform.authorizedFetch(`/api/repos/${project.id}/handoff?commit_id=${encodeURIComponent(commitId)}&format=${format}`);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.code === 'transfer_limit' && format === 'aica' ? '内容超过单文件 .aica 的兼容大小，请改用完整接手包 ZIP。' : error.message || '接手工作流导出失败');
  }
  return { blob: await response.blob(), commitId, filename: format === 'aica' ? 'workflow.aica' : 'agent-workflow.aica.zip' };
}

export async function switchBranch(project: WorkspaceProject, branch: string) {
  await queues.get(key(project.id, project.branch ?? 'main'));
  const { repository } = await platform.repository(project.id);
  return loadProject(repository, branch);
}

export async function saveParsedGraph(projectId: string, parseId: string): Promise<WorkspaceProject> {
  const response = await platform.authorizedFetch(`/api/projects/${projectId}/semantic-parses/${parseId}/repository`, { method: 'POST' });
  const value = await response.json();
  if (!response.ok) throw new Error(value.message || '提炼结果上库失败');
  const { repository } = await platform.repository(value.publication.repo_id);
  const project = await loadProject(repository);
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('aica:repository-saved', { detail: project }));
  return project;
}
