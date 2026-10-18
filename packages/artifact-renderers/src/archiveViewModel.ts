import type { AicaArchive } from './schema.generated';

export type Archive = AicaArchive;
export type Artifact = Archive['artifacts'][number];
export type ArchiveNode = Archive['graph']['nodes'][number];
export type Run = Archive['runs'][number];
export type PreviewMode = 'draft' | 'high_quality' | 'final';
export type CacheDecision = 'CACHE_HIT' | 'RECOMPUTE' | 'REBUILD' | 'unknown';
export type UriResolver = (artifact: Artifact) => string | undefined;
export type TextLoader = (artifact: Artifact, signal: AbortSignal) => Promise<string>;
export interface PanelRegion {
  artifact_id: string;
  node_id?: string | null;
  rect: { x: number; y: number; width: number; height: number };
  cache_decision?: CacheDecision;
  prompt?: string;
  caption?: string;
  role?: string;
}
export interface PreviewMetadata {
  composition_mode?: 'layer_stack' | 'masked_replace';
  preservation?: string;
  protected_pixel_count?: number;
  source?: string;
  origin_source?: string;
  is_substitute?: boolean;
  contains_substitute?: boolean;
  stage?: 'draft' | 'final';
  seed?: number;
  resolved_prompt?: string;
  model?: string;
  regions?: PanelRegion[];
  canvas?: { width: number; height: number };
  artifact_ids?: string[];
  attachment_artifact_ids?: string[];
}

export function safeMediaUri(uri: string | undefined): string | undefined {
  if (!uri || /[\u0000-\u001f]/.test(uri)) return undefined;
  const value = uri.trim();
  if (/^https?:\/\//i.test(value) || /^blob:/i.test(value)) return value;
  if (/^data:(image\/(png|jpeg|webp|gif|svg\+xml)|audio\/[\w.+-]+|video\/[\w.+-]+);base64,/i.test(value)) return value;
  if (!/^[a-z][a-z\d+.-]*:/i.test(value) && !value.startsWith('//') && !value.includes('\\')) return value;
  return undefined;
}

export function safeExternalUri(uri: string): string | undefined {
  try {
    const parsed = new URL(uri);
    return ['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : undefined;
  } catch { return undefined; }
}

export const defaultUriResolver: UriResolver = artifact => safeMediaUri(artifact.preview_uri)
  || artifact.locations?.filter(uri => /^https?:\/\//i.test(uri)).map(safeMediaUri).find(Boolean)
  || safeMediaUri(artifact.uri) || artifact.locations?.map(safeMediaUri).find(Boolean);

export function runMetadata(archive: Archive | undefined, runId: string | null | undefined): PreviewMetadata {
  if (!archive || !runId) return {};
  const extension = archive.extensions?.['aica.preview'] as { runs?: Record<string, PreviewMetadata> } | undefined;
  return extension?.runs?.[runId] || {};
}

export function latestRuns(archive: Archive, mode?: PreviewMode): Map<string, Run> {
  const stage = mode === 'high_quality' ? 'final' : mode;
  const result = new Map<string, Run>();
  for (const run of archive.runs) {
    if (run.revision_id !== archive.graph.revision_id || (stage && run.preview_stage !== stage)) continue;
    const previous = result.get(run.node_id);
    if (!previous || run.started_at >= previous.started_at) result.set(run.node_id, run);
  }
  return result;
}

export function artifactRun(archive: Archive | undefined, artifact: Artifact): Run | undefined {
  if (!archive) return undefined;
  return [...archive.runs].reverse().find(run => run.revision_id === archive.graph.revision_id &&
    run.artifact_ids.includes(artifact.id)) || archive.runs.find(run => run.id === artifact.producer_run_id);
}

export function artifactMetadata(archive: Archive | undefined, artifact: Artifact): PreviewMetadata {
  return runMetadata(archive, artifactRun(archive, artifact)?.id);
}

export function cacheDecision(run: Run | undefined): CacheDecision {
  return run?.cache?.decision || 'unknown';
}

export function decisionLabel(decision: CacheDecision): string {
  return decision === 'unknown' ? 'UNVERIFIED' : decision.replaceAll('_', ' ');
}

export function generationSource(metadata: PreviewMetadata): { label: string; tone: string } {
  if (metadata.is_substitute || metadata.source === 'local_fallback' || metadata.origin_source === 'local_fallback') {
    return { label: 'Local Fallback', tone: 'fallback' };
  }
  if (metadata.source === 'live_api') return { label: 'Live API', tone: 'live' };
  if (metadata.source === 'prewarmed_cache') return { label: 'Prewarmed Cache', tone: 'prewarmed' };
  if (metadata.source === 'composite_layout') return { label: 'Local Composition', tone: 'local' };
  if (['text_materialize', 'local_materialize'].includes(metadata.source || '')) return { label: 'Local Materialization', tone: 'local' };
  if (metadata.source === 'imported_verified') return { label: 'Imported Asset', tone: 'local' };
  return { label: 'Source unavailable', tone: 'unknown' };
}

export function replayNodes(archive: Archive): ArchiveNode[] {
  const branches = new Map(archive.graph.branches.map(branch => [branch.id, branch]));
  function active(id: string | null, visited = new Set<string>()): boolean {
    if (id === null) return true;
    if (visited.has(id)) return false;
    visited.add(id);
    const branch = branches.get(id);
    return Boolean(branch && branch.state === 'active' && active(branch.parent_id, visited));
  }
  return archive.graph.nodes.filter(node => node.execution && active(node.branch_id));
}

export function primaryArtifacts(archive: Archive, mode?: PreviewMode): Artifact[] {
  const runs = latestRuns(archive, mode);
  const activeNodes = new Set(replayNodes(archive).map(node => node.id));
  const nonterminal = new Set(archive.graph.edges.filter(edge => activeNodes.has(edge.source) && activeNodes.has(edge.target)).map(edge => edge.source));
  const terminalIds = [...runs].filter(([node]) => activeNodes.has(node) && !nonterminal.has(node))
    .flatMap(([, run]) => run.state === 'succeeded' ? run.artifact_ids : []);
  const ids = new Set(terminalIds.length ? terminalIds : [...runs.values()]
    .filter(run => run.state === 'succeeded').flatMap(run => run.artifact_ids));
  const compositions = new Set(archive.graph.nodes.filter(node => node.kind === 'composition').map(node => node.id));
  return archive.artifacts.filter(artifact => artifact.mime_type !== 'application/vnd.aica.preview+json' && (ids.size ? ids.has(artifact.id) : !artifact.producer_run_id))
    .sort((left, right) => Number(compositions.has(right.producer_node_id || '')) - Number(compositions.has(left.producer_node_id || '')));
}

export function reuseStatistics(archive: Archive, mode?: PreviewMode) {
  const counts = { reused: 0, recomputed: 0, rebuilt: 0, unknown: 0 };
  const runs = latestRuns(archive, mode);
  for (const node of replayNodes(archive)) {
    const run = runs.get(node.id);
    const decision = run?.state === 'succeeded' ? cacheDecision(run) : 'unknown';
    if (decision === 'CACHE_HIT') counts.reused++;
    else if (decision === 'RECOMPUTE') counts.recomputed++;
    else if (decision === 'REBUILD') counts.rebuilt++;
    else counts.unknown++;
  }
  return counts;
}

export function variableChanges(original: Archive, fork: Archive) {
  const names = new Set([...Object.keys(original.variables), ...Object.keys(fork.variables)]);
  return [...names].flatMap(name => {
    const before = original.variables[name]?.value;
    const after = fork.variables[name]?.value;
    const operations = (fork.variable_operations || []).filter(operation => operation.variable_id === name);
    if (JSON.stringify(before) === JSON.stringify(after) && !operations.length) return [];
    return [{ name, before, after, operations: operations.map(operation => operation.mode) }];
  });
}

export function builtWith(archive: Archive): string[] {
  return [...new Set(archive.graph.nodes.flatMap(node => node.execution
    ? [node.execution.tool, node.execution.model].filter((item): item is string => Boolean(item)) : []))];
}
