import { platform } from './platform';
import type { GraphDocument } from '../../../packages/runtime/design/graph-v0.1/graph.generated';

export type ParserSelection = { projectId: string; importId: string };
export type ParseJob = { id: string; state: string; graph_id: string | null; revision_id: string | null; error_code: string | null; error_detail?: { stage?: string } | null; usage: unknown };
export type PreparedInput = { input_sha256: string; facts: unknown[]; source_node_ids: string[]; coverage?: unknown };

export async function parserRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await platform.authorizedFetch(`/api${path}`, init);
  const body = await response.json();
  if (!response.ok) throw new Error(`${body.code || response.status}: ${body.message || 'Parser request failed'}`);
  return body as T;
}

export const parserJson = (body: unknown, signal?: AbortSignal): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });

export async function importParserBundle(bytes: ArrayBuffer, title: string, signal: AbortSignal): Promise<ParserSelection> {
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
  const { project } = await parserRequest<{ project: { id: string } }>('/projects', parserJson({ title: title.slice(0, 200) || 'Imported archive' }, signal));
  const receipt = await parserRequest<{ import_id: string }>(`/projects/${project.id}/imports`, {
    method: 'POST', signal, body: bytes, headers: { 'Content-Type': 'application/vnd.aica.bundle+zip', 'X-AiCA-Bundle-SHA256': hash, 'Idempotency-Key': `sha256:${hash}` },
  });
  return { projectId: project.id, importId: receipt.import_id };
}

export async function readSemanticGraph(selection: ParserSelection, job: ParseJob, signal: AbortSignal): Promise<GraphDocument> {
  if (!job.graph_id || !job.revision_id) throw new Error('This parse has no completed graph');
  const { graph } = await parserRequest<{ graph: GraphDocument }>(`/projects/${selection.projectId}/semantic-graphs/${encodeURIComponent(job.graph_id)}?revision_id=${encodeURIComponent(job.revision_id)}`, { signal });
  if (graph.format !== 'aica-semantic-graph/0.1' || graph.executable !== false || graph.source.import_id !== selection.importId) throw new Error('Unexpected semantic graph contract or source');
  return graph;
}
