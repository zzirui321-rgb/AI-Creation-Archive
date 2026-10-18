import { platform } from '../platform';
import { applyRecordedProcessView, displayValue, type Workflow, type ModuleArtifact } from '../workflow';
import { safeMedia } from '../workflow';
import type { ModulePreview } from '../workspace';
import type { CreationView } from '../creation-view';

export type ProcessNode = {
  id: string; label: string; kind: string; prompt: string; model: string | null;
  mode: unknown; harness: unknown; params: unknown; inputs: unknown; outputs: unknown;
  environment?: Record<string, unknown>; intent_id?: string;
  status?: string;
  agent_model?: string | null; agent?: unknown; field_status?: unknown; runs?: unknown[];
  provenance: string; evidence_ids: string[]; artifact_ids: string[]; branch_id?: string; dead?: boolean; inline_artifacts?: { id?: string; uri?: string; url?: string; mime?: string; mime_type?: string; label?: string }[];
};
export type ProcessEdge = { id: string; source: string; target: string; kind: 'data' | 'control' | 'semantic'; state: 'verified' | 'candidate'; explanation: string; relation?: string };
export type Analysis = {
  repository?: { id: string; commit_id: string };
  format: 'aica-graph-analysis/0.1'; title: string; description?: string;
  ui_state?: { preview_history?: ModulePreview[]; cover_artifact?: { uri: string; mime: string } };
  creation_graph?: CreationView;
  source: { sha256: string; bytes: number; kind: string };
  process_graph: {
    format: 'aica-process-view/0.1'; nodes: ProcessNode[]; edges: ProcessEdge[];
    evidence: { id: string; [key: string]: unknown }[]; warnings: string[];
    source_context?: { golden_path_node_ids?: string[]; branches?: { id: string; state: string }[]; user_messages?: import('../workflow').RecordedMessage[]; history_issues?: unknown[] };
  };
  artifacts: { id: string; mime: string; available: boolean }[];
  issues: { message: string }[];
};
export type ImportedRecord = { title: string; description: string; workflow: Workflow; previewHistory?: ModulePreview[]; coverArtifact?: { uri: string; mime: string }; repository?: { id: string; commit_id: string } };
const base = '/api/creation-parser';
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function adaptAnalysis(analysis: Analysis, job: string, _origin: string): ImportedRecord {
  if (analysis.format !== 'aica-graph-analysis/0.1' || analysis.process_graph?.format !== 'aica-process-view/0.1' || !Array.isArray(analysis.process_graph.nodes)) throw new Error('解析服务返回了无法识别的流程图。');
  const graph = analysis.process_graph;
  const context = graph.source_context;
  const assets: ModuleArtifact[] = analysis.artifacts.filter(a => a.available).map(a => ({ id: a.id, uri: `${base}/jobs/${encodeURIComponent(job)}/artifacts/${encodeURIComponent(a.id)}`, mime: a.mime, label: a.id }));
  const ids = new Set(graph.nodes.map(n => n.id));
  if (ids.size !== graph.nodes.length || graph.edges.some(e => !ids.has(e.source) || !ids.has(e.target))) throw new Error('解析结果包含重复节点或不存在的依赖。');
  const workflow: Workflow = {
    creationView: analysis.creation_graph,
    source: 'imported', goldenPath: (context?.golden_path_node_ids ?? []).filter(id => ids.has(id)),
    warnings: [...new Set([...analysis.issues.map(i => i.message), ...graph.warnings])],
    importSource: { jobId: job, sha256: analysis.source.sha256, bytes: analysis.source.bytes },
    artifacts: assets,
    modules: graph.nodes.map(n => ({
      id: n.id, title: n.label, kind: n.kind, prompt: n.prompt || '', model: n.model || '', mode: displayValue(n.mode),
      harness: displayValue(n.harness), params: object(n.params), inputs: object(n.inputs), outputs: n.outputs, environment: n.environment, intentId: n.intent_id, status: typeof n.status === 'string' ? n.status : undefined,
      provenance: n.provenance, evidence: graph.evidence.filter(e => n.evidence_ids.includes(e.id)), groupLabel: (() => { const group = analysis.creation_graph?.nodes.find(g => g.member_node_ids.includes(n.id)); return analysis.creation_graph?.tasks?.find(t => t.id === group?.task_id)?.label ?? (group && group.member_node_ids.length > 1 ? group.label : undefined); })(),
      dead: !!n.dead || !!context?.branches?.some(b => b.id === n.branch_id && ['dead_end', 'pruned', 'hidden'].includes(b.state)),
      artifacts: [...assets.filter(a => n.artifact_ids.includes(a.id)), ...(n.inline_artifacts ?? []).map((a, index) => ({ id: a.id || `${n.id}-asset-${index}`, uri: a.uri || a.url || '', mime: a.mime || a.mime_type || '', label: a.label || '产物' }))],
    })),
    edges: graph.edges.map(e => ({ id: e.id, source: e.source, target: e.target, kind: e.kind, state: e.state, explanation: e.explanation, relation: e.relation })),
  };
  const previewHistory = (analysis.ui_state?.preview_history ?? []).filter(p => !!p && typeof p === 'object' && ids.has(p.nodeId) && typeof p.prompt === 'string' && typeof p.model === 'string' && Number.isFinite(p.createdAt) && ['draft', 'final'].includes(p.stage) && typeof p.simulated === 'boolean' && (p.simulated || !!p.artifact) && (!p.artifact || (typeof p.artifact.uri === 'string' && typeof p.artifact.mime === 'string' && /^(image|video)\//.test(p.artifact.mime) && safeMedia(p.artifact.uri, p.artifact.mime))));
  applyRecordedProcessView(workflow, graph);
  const cover = analysis.ui_state?.cover_artifact;
  const coverArtifact = cover && typeof cover.uri === 'string' && typeof cover.mime === 'string' && /^(image|video)\//.test(cover.mime) && safeMedia(cover.uri, cover.mime) ? cover : undefined;
  return { repository: analysis.repository, title: analysis.title || '未命名创作档案', description: analysis.description || '从创作过程 JSON 导入的记录。', workflow, previewHistory, coverArtifact };
}

async function responseJSON(response: Response) {
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('解析服务未启动，请启动 AiCA-graph-lab 本地解析服务后重试。');
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '解析请求失败，请重试。');
  return value;
}

async function upload(file: File, signal: AbortSignal, progress: (text: string) => void): Promise<{ id: string }> {
  progress('正在上传并校验原始记录…');
  return responseJSON(await platform.authorizedFetch(`${base}/imports`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: file }));
}

function pause(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); reject(new DOMException('已取消', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, 500);
    signal.addEventListener('abort', cancel, { once: true });
    if (signal.aborted) { signal.removeEventListener('abort', cancel); cancel(); }
  });
}

export async function importCreation(file: File, signal: AbortSignal, progress: (text: string) => void): Promise<ImportedRecord> {
  const job = await upload(file, signal, progress);
  for (;;) {
    const state = await responseJSON(await platform.authorizedFetch(`${base}/jobs/${job.id}`, { signal }));
    if (state.status === 'error') throw new Error(state.error || '文件解析失败。');
    if (state.status === 'ready') break;
    progress(state.status === 'queued' ? '正在等待本地解析…' : '正在提取节点、校验来源与建立关系…');
    await pause(signal);
  }
  const analysis: Analysis = await responseJSON(await platform.authorizedFetch(`${base}/jobs/${job.id}/result`, { signal }));
  return adaptAnalysis(analysis, job.id, window.location.origin);
}
