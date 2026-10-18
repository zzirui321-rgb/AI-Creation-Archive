import type { GraphDocument, ModulePage, ModulePrompt } from './semantic-graph.generated';
import type { CommunityProject } from './community';
import { links, steps } from './demo';
import type { CreationView } from './creation-view';

export type ModuleArtifact = { id: string; uri: string; mime: string; label: string };
export type RecordedMessage = { id: string; text: string; timestamp?: string; session_id?: string; agent_id?: string; node_id?: string; artifact_id: string; source_pointer: string };
export type RecordedProcessView = { nodes: { id: string; prompt: string; model: string | null; agent_model?: string | null; agent?: unknown; inputs: unknown; outputs: unknown; harness: unknown; status?: string; field_status?: unknown; runs?: unknown[]; evidence_ids: string[] }[]; evidence: { id: string; [key: string]: unknown }[]; warnings: string[]; source_context?: { user_messages?: RecordedMessage[]; history_issues?: unknown[] } };
export type WorkflowModule = {
  id: string; title: string; kind: string; prompt: string; model: string; mode: string;
  harness: string; params: Record<string, unknown>; inputs: Record<string, unknown>;
  outputs: unknown; provenance: string; evidence: unknown[]; dead: boolean; artifacts: ModuleArtifact[];
  purpose?: string; prompts?: ModulePrompt[]; pages?: ModulePage[]; materialsUrl?: string; artifactBase?: string;
  environment?: Record<string, unknown>;
  status?: string;
  groupLabel?: string;
  intentId?: string;
  resultText?: string;
  resultPending?: boolean;
  generatedPreviewId?: string;
  agentModel?: string; agent?: unknown; fieldStatus?: unknown; runHistory?: unknown[];
};
export type WorkflowEdge = { id?: string; source: string; target: string; kind?: 'data' | 'control' | 'semantic'; state?: 'verified' | 'candidate'; explanation?: string; relation?: string; relations?: string[] };
export const isExecutionEdge = (edge: WorkflowEdge) => edge.kind !== 'semantic' && edge.state !== 'candidate';
export type Workflow = { semanticGraph?: GraphDocument; creationView?: CreationView; artifacts?: ModuleArtifact[]; recordedMessages?: RecordedMessage[]; historyIssues?: unknown[]; importSource?: { jobId: string; sha256: string; bytes: number }; modules: WorkflowModule[]; edges: WorkflowEdge[]; goldenPath: string[]; source: 'demo' | 'imported'; warnings: string[] };
const obj = (v: unknown): Record<string, any> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, any> : {};
const string = (v: unknown, fallback = '') => typeof v === 'string' ? v : fallback;
const list = (v: unknown): any[] => Array.isArray(v) ? v : [];
const sensitive = /^(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|secret|credentials|client[_-]?secret)$/i;
export function redact(value: unknown, depth = 0): any {
  if (depth > 40) throw new Error('文件嵌套层级过深。');
  if (Array.isArray(value)) return value.map(item => redact(item, depth + 1));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).filter(([key]) => !sensitive.test(key)).map(([key, item]) => [key, redact(item, depth + 1)]));
  return value;
}
export function displayValue(value: unknown): string { return typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2); }
export function applyRecordedProcessView(workflow: Workflow, view: RecordedProcessView) {
  const clean = redact(view) as RecordedProcessView;
  const nodes = new Map(clean.nodes.map(node => [node.id, node]));
  workflow.modules = workflow.modules.map(module => {
    const node = nodes.get(module.id);
    return node ? { ...module, prompt: node.prompt, model: node.model || '', inputs: obj(node.inputs), outputs: node.outputs,
      harness: displayValue(node.harness), status: node.status, agentModel: node.agent_model || '', agent: node.agent,
      fieldStatus: node.field_status, runHistory: node.runs, evidence: clean.evidence.filter(item => node.evidence_ids.includes(item.id)) } : module;
  });
  workflow.recordedMessages = clean.source_context?.user_messages ?? [];
  workflow.historyIssues = clean.source_context?.history_issues ?? [];
  workflow.warnings = [...new Set([...workflow.warnings, ...clean.warnings])];
}
export function safeMedia(uri: string, mime = '') { return /^\/api\/3d\/jobs\/[a-f0-9]{32}\/model(?:\?|$)/.test(uri) || /^\/api\/creation-parser\//.test(uri) || /^blob:/i.test(uri) || /^\/api\/repos\/[a-f0-9-]+\/(artifacts|images)\//.test(uri) || /^\/runtime-api\/preview-artifacts\/[a-f0-9]{64}$/.test(uri) || /^https?:\/\//i.test(uri) || (/^data:image\/(png|jpeg|webp);base64,/i.test(uri) && mime.startsWith('image/')); }
export function parseWorkflow(text: string): { title: string; description: string; workflow: Workflow } {
  let parsed: unknown; try { parsed = JSON.parse(text.replace(/^\uFEFF/, '')); } catch { throw new Error('不是有效的 JSON 文件，请上传 MCP 已解析的 .aica 或 JSON。'); }
  const root = obj(redact(parsed)); const graph = obj(root.graph); const metadata = obj(root.metadata);
  if (root.format === 'aica-semantic-graph/0.1') return parseSemanticGraph(root as GraphDocument);
  if (root.aica_version && root.aica_version !== '0.1') throw new Error(`暂不支持 .aica ${root.aica_version}，请使用 0.1 或后端 steps 解析结果。`);
  const nodes = list(graph.nodes).length ? graph.nodes : list(root.steps).length ? root.steps : list(root.modules);
  if (!nodes.length && !root.aica_version) throw new Error('未找到 graph.nodes、steps 或 modules。请上传已包含步骤和依赖的解析文件，而非原始日志。');
  const allArtifacts = list(root.artifacts);
  const branches = list(graph.branches);
  const warnings = ['此处校验展示所需的结构；正式协议和执行语义由团队后端校验。'];
  if (obj(root.snapshot).complete === false || list(obj(root.snapshot).gaps).length) warnings.push('此档案声明存在缺失片段，当前 Graph 仅展示文件提供的记录。');
  function resolveInput(input: unknown): string {
    if (typeof input === 'string') return input;
    const binding = obj(input);
    if (binding.kind === 'literal') return displayValue(binding.value);
    if (binding.kind === 'variable') { const v = obj(root.variables)[binding.variable_id]; return displayValue(obj(v).value ?? obj(v).default ?? v); }
    if (binding.kind === 'template') return string(binding.template).replace(/\{\{([^}]+)\}\}/g, (match, key) => { const v = obj(root.variables)[obj(binding.variables)[key] ?? key]; return displayValue(obj(v).value ?? obj(v).default ?? v) || match; });
    return displayValue(input);
  }
  const modules: WorkflowModule[] = nodes.map((raw: unknown, index: number) => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(`第 ${index + 1} 个模块必须是对象。`);
    const n = obj(raw); const execution = obj(n.execution);
    const id = string(n.id, `step-${index + 1}`);
    if (!id.trim() || id.length > 180) throw new Error(`第 ${index + 1} 个模块 ID 无效。`);
    const run = obj(list(root.runs).filter(r => r.node_id === id).at(-1));
    const inputs = obj(execution.inputs ?? n.inputs ?? run.inputs);
    const harnessId = n.harness_id ?? execution.harness_id ?? run.harness_id;
    const harness = n.harness ?? execution.harness ?? list(root.harnesses).find(h => h.id === harnessId);
    const provenance = typeof n.provenance === 'string' ? n.provenance : obj(n.provenance).mode;
    const artifacts = [...allArtifacts.filter(a => a.producer_node === id || a.producer_node_id === id || obj(a.producer).node_id === id), ...list(n.artifacts)];
    return { id, title: string(n.label ?? n.title ?? n.name, `步骤 ${index + 1}`), kind: string(n.kind ?? n.type, 'step'),
      prompt: string(n.prompt, resolveInput(inputs.prompt)), model: string(n.model ?? execution.model, ''),
      mode: string(n.mode ?? execution.mode ?? execution.kind, ''), harness: displayValue(harness ?? harnessId), params: obj(execution.params ?? n.params), inputs,
      outputs: execution.outputs ?? n.outputs ?? run.outputs ?? [], provenance: string(provenance, 'unknown'), evidence: list(obj(n.provenance).evidence ?? n.evidence),
      agentModel: string(n.agentModel), agent: n.agent, fieldStatus: n.fieldStatus, runHistory: list(n.runHistory),
      dead: !!n.dead || branches.some(b => b.id === n.branch_id && ['dead_end', 'pruned', 'hidden'].includes(b.state)),
      artifacts: artifacts.map((a, i) => ({ id: string(a.id, `${id}-artifact-${i}`), uri: string(a.uri ?? a.url), mime: string(a.mime ?? a.mime_type), label: string(a.label ?? a.name, '产物') })) };
  });
  const ids = new Set(modules.map(n => n.id)); if (ids.size !== modules.length) throw new Error('模块 ID 重复，请检查解析结果。');
  const rawEdges = list(graph.edges ?? root.edges);
  nodes.forEach((n: any, i: number) => list(n.depends_on ?? n.dependencies).forEach(source => rawEdges.push({ source, target: modules[i].id })));
  const edges = Array.from(new Map(rawEdges.map(raw => { const e = Array.isArray(raw) ? { source: raw[0], target: raw[1] } : obj(raw); const source = string(e.source), target = string(e.target); if (!ids.has(source) || !ids.has(target)) throw new Error(`依赖引用了不存在的模块：${source} → ${target}`); return [`${source}\0${target}`, { source, target }]; })).values());
  const indegree = new Map(modules.map(n => [n.id, 0])); edges.forEach(e => indegree.set(e.target, indegree.get(e.target)! + 1));
  const queue = modules.filter(n => !indegree.get(n.id)).map(n => n.id); let visited = 0;
  while (queue.length) { const id = queue.shift()!; visited++; edges.filter(e => e.source === id).forEach(e => { indegree.set(e.target, indegree.get(e.target)! - 1); if (!indegree.get(e.target)) queue.push(e.target); }); }
  if (visited !== modules.length) throw new Error('依赖中存在循环，请修正后再导入。');
  if (!edges.length && modules.length > 1) warnings.push('文件未提供依赖，模块将独立展示，不推测执行顺序。');
  const goldenPath = list(graph.golden_path ?? root.golden_path).filter((id): id is string => typeof id === 'string');
  if (goldenPath.some(id => !ids.has(id))) throw new Error('推荐路径引用了不存在的模块。');
  return { title: string(metadata.title ?? root.title, '未命名创作档案'), description: string(metadata.description ?? root.description, '从 MCP 解析文件导入的创作记录。'), workflow: { recordedMessages: list(root.recorded_messages), historyIssues: list(root.history_issues), artifacts: allArtifacts.map(a => ({ id: a.id, uri: a.uri || a.url || '', mime: a.mime || a.mime_type || '', label: a.label || a.name || a.id })), modules, edges, goldenPath, source: 'imported', warnings } };
}

function parseSemanticGraph(graph: GraphDocument): { title: string; description: string; workflow: Workflow } {
  if (!Array.isArray(graph.nodes) || !graph.nodes.length || graph.nodes.length > 200 || !Array.isArray(graph.edges)
      || !Array.isArray(graph.evidence) || !Array.isArray(graph.variables)) throw new Error('提炼后的 Graph 结构不完整。');
  const ids = new Set(graph.nodes.map(node => node.id));
  if (ids.size !== graph.nodes.length || graph.edges.some(edge => !ids.has(edge.source) || !ids.has(edge.target))) throw new Error('提炼后的 Graph 存在无效节点引用。');
  const modules: WorkflowModule[] = graph.nodes.map(node => {
    const variables = graph.variables.filter(variable => variable.step_id === node.id);
    const prompt = variables.find(variable => /prompt|提示词/i.test(variable.label) && typeof variable.current_value === 'string');
    return { id: node.id, title: node.label, kind: node.kind, purpose: node.purpose.text,
      prompt: node.prompts?.filter(item => item.kind === 'edited').map(item => item.text).join('\n\n') || node.prompts?.filter(item => item.kind === 'consolidated').map(item => item.text).join('\n\n') || node.prompts?.filter(item => item.kind === 'recorded').map(item => item.text).join('\n\n') || (prompt ? String(prompt.current_value) : ''),
      prompts: node.prompts ?? [], pages: node.pages ?? [], model: '', mode: node.purpose.review === 'candidate' ? '提炼结果 · 待审核' : '提炼结果',
      harness: '', params: { variables }, inputs: { source_node_ids: node.source_node_ids, requirements: node.requirements },
      outputs: { run_ids: node.run_ids, artifact_ids: node.artifact_ids }, provenance: node.purpose.origin,
      evidence: graph.evidence.filter(evidence => node.evidence_ids.includes(evidence.id)), dead: false, artifacts: [] };
  });
  return { title: '提炼后的创作图', description: '解析模块提炼的创作步骤与关系，解释和变量保留审核状态。',
    workflow: { modules, edges: graph.edges.map(edge => ({ ...edge, explanation: edge.explanation.text })), goldenPath: [], source: 'imported', semanticGraph: graph,
      warnings: ['原始提示词、AI整合与AI扩展建议分开显示；语义Graph仍不可直接执行，未提供的模型与Harness保持空缺。',
        ...(graph.coverage?.issues ?? []).map(issue => `${issue.code}：${issue.message}`)] } };
}

export function demoWorkflow(project: CommunityProject): Workflow {
  return { source: 'demo', warnings: [], goldenPath: steps.filter(s => !s.dead).map(s => s.id), edges: links.map(([source, target]) => ({ source, target })),
    modules: steps.map(s => ({ id: s.id, title: ({ story: '创作意图', style: '视觉风格', character: '角色设定', panel1: '画面生成 · 01', panel2: '画面生成 · 02', music: '氛围音轨', layout: '成品合成', trial: '未采用的方案' } as Record<string, string>)[s.id], kind: s.kind.toLowerCase(),
      prompt: s.id === 'story' ? project.description : s.prompt.replaceAll('{{style}}', project.values.style).replaceAll('{{character}}', project.values.character).replaceAll('{{detail}}', project.values.detail || '无'),
      model: '', mode: s.kind === 'IMAGE' ? 'text-to-image' : s.kind === 'COMPOSE' ? 'compose' : 'text', harness: '', params: {}, inputs: {}, outputs: [], provenance: 'demo', evidence: [], dead: !!s.dead, artifacts: [] })) };
}
export function moduleImpact(workflow: Workflow, edited: string[]) {
  const dirty = new Set(edited); let changed = true;
  while (changed) { changed = false; workflow.edges.filter(isExecutionEdge).forEach(e => { if (dirty.has(e.source) && !dirty.has(e.target)) { dirty.add(e.target); changed = true; } }); }
  return Object.fromEntries(workflow.modules.filter(n => !n.dead).map(n => [n.id, !dirty.has(n.id) ? 'CACHE HIT' : edited.includes(n.id) ? 'RECOMPUTE' : 'REBUILD']));
}
export function modulePositions(workflow: Workflow, vertical = false) {
  const dependencies = workflow.edges.filter(isExecutionEdge);
  if (!dependencies.length && workflow.source === 'imported') return Object.fromEntries(workflow.modules.map((node, index) => [node.id, vertical ? { x: index % 2 * 295, y: Math.floor(index / 2) * 210 } : { x: index % 4 * 305, y: Math.floor(index / 4) * 190 }]));
  const ranks = new Map(workflow.modules.map(n => [n.id, 0]));
  for (let i = 0; i < workflow.modules.length; i++) { let changed = false; dependencies.forEach(e => { if (ranks.get(e.target)! <= ranks.get(e.source)!) { ranks.set(e.target, ranks.get(e.source)! + 1); changed = true; } }); if (!changed) break; }
  const counts = new Map<number, number>();
  return Object.fromEntries(workflow.modules.map(n => { const rank = ranks.get(n.id)!; const row = counts.get(rank) ?? 0; counts.set(rank, row + 1); return [n.id, vertical ? { x: row * 295, y: rank * 210 } : { x: rank * 305, y: row * 190 }]; }));
}
