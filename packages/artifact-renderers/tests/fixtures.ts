import type { Archive, Artifact, Run } from '../src';

const now = '2026-10-16T00:00:00Z';
export function fixture(): Archive {
  return {
    aica_version: '0.1', metadata: { project_id: 'test', title: 'A reusable archive', source_mode: 'mixed',
      created_at: now, updated_at: now, external_links: ['https://github.com/AiCA-Team/AiCA'] },
    variables: { theme: { type: 'string', default: 'city', value: 'city', modes: ['keep', 'override', 'append'], bindings: [] } },
    interactive_questions: [], variable_operations: [],
    graph: { revision_id: 'revision', nodes: ['source', 'image', 'layout'].map((id, index) => ({
      id, kind: index === 2 ? 'composition' : 'model', label: id, branch_id: 'main',
      provenance: { mode: 'recorded', evidence: [{ source: 'test' }] },
      execution: { kind: 'model', tool: index === 2 ? 'layout_compose' : 'image_gen', version: '1',
        inputs: {}, params: {}, outputs: ['result'] },
    })), edges: [{ id: 'edge', source: 'image', target: 'layout', kind: 'data' }],
    branches: [{ id: 'main', parent_id: null, state: 'active' }], golden_path: ['source', 'image', 'layout'] },
    artifacts: [], runs: [], harnesses: [], agents: [], sessions: [],
    snapshot: { stream_id: 'stream', snapshot_id: 'snapshot', commit_seq: 0, complete: true, gaps: [] }, extensions: {},
  };
}

export function artifact(id = 'artifact', mime = 'image/png'): Artifact {
  return { id, uri: `/media/${id}.png`, mime_type: mime, sha256: 'a'.repeat(64), availability: 'available',
    producer_node_id: 'image', producer_run_id: 'run-image' };
}

export function run(node: string, decision: 'CACHE_HIT' | 'RECOMPUTE' | 'REBUILD', ids: string[] = []): Run {
  return { id: `run-${node}`, trace_id: 'trace', node_id: node, revision_id: 'revision', session_id: 'session',
    agent_id: 'agent', harness_id: 'harness', state: 'succeeded', started_at: now, ended_at: now,
    inputs: {}, outputs: {}, artifact_ids: ids, provenance: { mode: 'recorded', evidence: [] },
    cache: { decision }, preview_stage: 'draft' };
}
