import type { Archive, Artifact, PreviewMetadata } from './archiveViewModel';

export type VariableOperation = { variable_id: string; mode: 'keep' | 'override' | 'append'; value?: unknown };
export type ArchiveSnapshot = { archive_id: string; archive: Archive };
export type RuntimePlan = { node_ids: string[]; executable: boolean; blockers: { message: string; node_id?: string }[]; decisions: Record<string, string> };
export type RuntimeJob = { id: string; state: string; result_archive_id: string; result?: { message?: string } };
export type ImageProviderConfig = { provider: 'custom' | 'openai' | 'siliconflow' | 'fal' | 'qwen' | 'sdcpp' | 'local_fallback'; api_base: string; api_key: string; model: string; draft_model?: string; hq_model?: string; timeout_seconds?: number; allow_fallback?: boolean };
export const PREVIEW_METADATA_MIME = 'application/vnd.aica.preview+json';

export function variableFields(archive: Archive) {
  return Object.entries(archive.variables).map(([id, variable]) => ({ id, variable,
    question: archive.interactive_questions.find(item => item.variable_id === id)?.prompt || variable.description || id,
    modes: variable.modes.filter(mode => !archive.interactive_questions.some(item => item.variable_id === id) || archive.interactive_questions.some(item => item.variable_id === id && item.modes.includes(mode))),
  }));
}

export function operationValue(variable: Archive['variables'][string], mode: VariableOperation['mode'], text: string): unknown {
  if (!variable.modes.includes(mode)) throw new Error('Variable operation is not enabled');
  if (mode === 'keep') return undefined;
  if (variable.type === 'string') return text;
  const value: unknown = JSON.parse(text);
  const valid = variable.type === 'array' ? Array.isArray(value) : variable.type === 'object' ? !!value && typeof value === 'object' && !Array.isArray(value) : variable.type === 'integer' ? typeof value === 'number' && Number.isInteger(value) : variable.type === 'number' ? typeof value === 'number' && Number.isFinite(value) : typeof value === variable.type;
  if (!valid) throw new Error(`Expected ${variable.type}`);
  return value;
}

export class ArchiveClient {
  constructor(private fetcher: typeof fetch = globalThis.fetch.bind(globalThis), private apiPrefix = '/api') {}
  private url(path: string) { return path.replace(/^\/api(?=\/|$)/, this.apiPrefix); }
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.fetcher(this.url(path), { ...init, credentials: 'omit', redirect: 'error', cache: 'no-store' });
    if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.message || `HTTP ${response.status}`); }
    return response.json();
  }
  projects(signal?: AbortSignal) { return this.request<{ projects: { id: string; title: string; head: string | null }[] }>('/api/projects?limit=200', { signal }); }
  async snapshot(project: string, id?: string, signal?: AbortSignal) {
    const result = await this.request<ArchiveSnapshot>(`/api/projects/${encodeURIComponent(project)}/archive${id ? '?archive_id=' + encodeURIComponent(id) : ''}`, { signal });
    if (result.archive?.aica_version !== '0.1' || !result.archive_id || (id && result.archive_id !== id)) throw new Error('Archive version mismatch');
    return result;
  }
  plan(project: string, id: string, stage: string, signal?: AbortSignal, imageConfig?: ImageProviderConfig) {
    return imageConfig ? this.request<RuntimePlan>(`/api/projects/${encodeURIComponent(project)}/plan`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ archive_id: id, stage, image_config: imageConfig }), signal }) : this.request<RuntimePlan>(`/api/projects/${encodeURIComponent(project)}/plan?archive_id=${encodeURIComponent(id)}&stage=${stage}`, { signal });
  }
  goldenPath(project: string, id: string, signal?: AbortSignal) { return this.request<{ node_ids: string[] }>(`/api/projects/${encodeURIComponent(project)}/golden-path?archive_id=${encodeURIComponent(id)}`, { signal }); }
  async upload(file: File, signal?: AbortSignal) {
    if (file.size > 128 * 1024 * 1024) throw new Error('Archive exceeds 128 MiB');
    if (!/\.(json|aica|zip)$/i.test(file.name)) throw new Error('Expected .aica, .json or .zip');
    const content = await file.arrayBuffer();
    const sha = await hashBytes(content);
    const { project } = await this.request<{ project: { id: string } }>('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: file.name.slice(0, 200) }), signal });
    await this.request(`/api/projects/${project.id}/imports`, { method: 'POST', headers: { 'Content-Type': /\.zip$/i.test(file.name) ? 'application/vnd.aica.bundle+zip' : 'application/json', 'Idempotency-Key': `sha256:${sha}`, 'X-AiCA-Bundle-SHA256': sha }, body: content, signal });
    return project.id;
  }
  fork(project: string, snapshot: ArchiveSnapshot, operations: VariableOperation[], signal?: AbortSignal) {
    return this.request<{ archive_id: string; dirty_node_ids: string[] }>(`/api/projects/${encodeURIComponent(project)}/forks`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), archive_id: snapshot.archive_id, expected_revision: snapshot.archive.graph.revision_id, operations }), signal });
  }
  execute(project: string, snapshot: ArchiveSnapshot, stage: 'draft' | 'final', signal?: AbortSignal, imageConfig?: ImageProviderConfig) {
    return this.request<{ job_id: string }>(`/api/projects/${encodeURIComponent(project)}/executions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), archive_id: snapshot.archive_id, expected_revision: snapshot.archive.graph.revision_id, stage, record: true, execute: true, ...(imageConfig ? { image_config: imageConfig } : {}) }), signal });
  }
  job(project: string, id: string, signal?: AbortSignal) { return this.request<{ execution: RuntimeJob }>(`/api/projects/${encodeURIComponent(project)}/executions/${encodeURIComponent(id)}`, { signal }); }
  cancel(project: string, id: string) { return this.request(`/api/projects/${encodeURIComponent(project)}/executions/${encodeURIComponent(id)}/cancel`, { method: 'POST' }); }
  async bytes(project: string, artifact: Artifact, signal?: AbortSignal) {
    if (!artifact.sha256) throw new Error('Artifact has no verified hash');
    const response = await this.fetcher(this.url(`/api/projects/${encodeURIComponent(project)}/artifacts/${encodeURIComponent(artifact.id)}`), { signal, credentials: 'omit', redirect: 'error', cache: 'no-store' });
    if (!response.ok) throw new Error(`Artifact HTTP ${response.status}`);
    if (Number(response.headers.get('Content-Length')) > 64 * 1024 * 1024) throw new Error('Artifact exceeds preview limit');
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength > 64 * 1024 * 1024 || (artifact.size != null && bytes.byteLength !== artifact.size) || await hashBytes(bytes) !== artifact.sha256) throw new Error('Artifact hash/size mismatch');
    return new Blob([bytes], { type: artifact.mime_type });
  }
}

export async function hashBytes(content: ArrayBuffer) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', content))].map(value => value.toString(16).padStart(2, '0')).join('');
}

export async function loadArchiveAssets(client: ArchiveClient, project: string, archive: Archive, signal: AbortSignal) {
  const abort = new AbortController();
  signal = AbortSignal.any([signal, abort.signal]);
  const copy = structuredClone(archive);
  const urls = new Map<string, string>();
  const blobs = new Map<string, Blob>();
  try {
    if (archive.artifacts.length > 256) throw new Error('Paginated preview required above 256 artifacts');
    let size = 0;
    const pending = [...archive.artifacts];
    await Promise.all(Array.from({ length: Math.min(4, pending.length) }, async () => {
      while (pending.length && !signal.aborted) {
        const artifact = pending.shift()!;
        const blob = await client.bytes(project, artifact, signal);
        size += blob.size;
        if (size > 128 * 1024 * 1024) throw new Error('Archive exceeds 128 MiB preview limit');
        if (!signal.aborted) { blobs.set(artifact.id, blob); urls.set(artifact.id, URL.createObjectURL(blob)); }
      }
    }));
    const existing = copy.extensions?.['aica.preview'] as { runs?: Record<string, PreviewMetadata> } | undefined;
    const extension = { ...existing, runs: { ...existing?.runs } as Record<string, PreviewMetadata> };
    for (const run of copy.runs) {
      const sidecars = copy.artifacts.filter(item => run.artifact_ids.includes(item.id) && item.mime_type === PREVIEW_METADATA_MIME);
      for (const sidecar of sidecars) {
        const body = JSON.parse(await blobs.get(sidecar.id)!.text());
        if (body.format !== 'aica-preview/1') continue;
        const metadata = body.metadata;
        if (!copy.artifacts.some(item => run.artifact_ids.includes(item.id) && item.sha256 === metadata.primary_sha256 && item.mime_type !== PREVIEW_METADATA_MIME)) throw new Error('Unbound preview sidecar');
        const rebind = (id: string) => {
          const sha = metadata.reference_hashes?.[id];
          if (!sha) return id;
          const producer = copy.artifacts.find(item => item.id === id)?.producer_node_id;
          const matches = copy.artifacts.filter(item => item.sha256 === sha && (!producer || item.producer_node_id === producer));
          const current = [...copy.runs].reverse().find(upstream => upstream.node_id === producer && upstream.revision_id === run.revision_id && upstream.preview_stage === run.preview_stage && upstream.state === 'succeeded');
          const preferred = matches.find(item => current?.artifact_ids.includes(item.id));
          return preferred?.id || matches.at(-1)?.id || id;
        };
        metadata.regions = metadata.regions?.map((region: { artifact_id: string; prompt?: string }) => {
          const childId = rebind(region.artifact_id);
          const producer = copy.artifacts.find(item => item.id === childId)?.producer_node_id;
          const upstream = [...copy.runs].reverse().find(item => item.node_id === producer && item.revision_id === run.revision_id && item.preview_stage === run.preview_stage);
          return { ...region, artifact_id: childId, prompt: typeof upstream?.inputs.prompt === 'string' ? upstream.inputs.prompt : region.prompt };
        });
        metadata.attachment_artifact_ids = metadata.attachment_artifact_ids?.map(rebind);
        extension.runs[run.id] = metadata;
      }
    }
    copy.extensions = { ...copy.extensions, 'aica.preview': extension };
    return { archive: copy, resolveUri: (artifact: Artifact) => urls.get(artifact.id), dispose: () => { for (const url of urls.values()) URL.revokeObjectURL(url); } };
  } catch (error) { abort.abort(); for (const url of urls.values()) URL.revokeObjectURL(url); throw error; }
}
