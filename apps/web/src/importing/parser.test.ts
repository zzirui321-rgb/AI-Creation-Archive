import { describe, expect, it } from 'vitest';
import { adaptAnalysis, type Analysis, type ProcessNode } from './parser';
import { moduleImpact, modulePositions, parseWorkflow } from '../workflow';
import { forkProject, saveWorkspace, readWorkspace, type WorkspaceProject } from '../workspace';

const node = (id: string): ProcessNode => ({ id, label: `步骤 ${id}`, kind: 'generation', prompt: `prompt-${id}`, model: 'qwen-image', mode: 'image', harness: { name: 'MCP' }, params: { seed: 42 }, inputs: {}, outputs: [], provenance: 'recorded', evidence_ids: [`proof-${id}`], artifact_ids: ['image'] });
const analysis = (): Analysis => ({
  format: 'aica-graph-analysis/0.1', title: '我的创作', source: { sha256: 'a'.repeat(64), bytes: 300_000_000, kind: 'aica' },
  process_graph: { format: 'aica-process-view/0.1', nodes: [node('a'), node('b'), node('c')], edges: [
    { id: 'exec', source: 'a', target: 'b', kind: 'data', state: 'verified', explanation: '输入依赖' },
    { id: 'reading', source: 'b', target: 'c', kind: 'semantic', state: 'candidate', explanation: '阅读顺序' },
    { id: 'loop', source: 'c', target: 'a', kind: 'semantic', state: 'candidate', explanation: '语义关联' },
  ], evidence: [{ id: 'proof-a', pointer: '/graph/nodes/0' }], warnings: ['依据来源展示'], source_context: { golden_path_node_ids: ['a', 'b'] } },
  artifacts: [{ id: 'image', mime: 'image/png', available: true }, { id: 'missing', mime: 'image/png', available: false }], issues: [],
});

describe('creation parser integration', () => {
  it('keeps execution and Agent models separate and preserves history after saving and reopening', () => {
    const source = analysis();
    source.process_graph.nodes[0] = { ...node('a'), model: null, agent_model: 'agent-only-model',
      field_status: { model: { state: 'not_recorded' } }, runs: [{ id: 'run-a', inputs: { prompt: 'actual prompt' } }] };
    source.process_graph.source_context!.user_messages = [{ id: 'message', text: '用户原始要求', artifact_id: 'events', source_pointer: '/history/0#line=1' }];
    const { workflow } = adaptAnalysis(source, 'job1', 'http://localhost');
    expect(workflow.modules[0].model).toBe('');
    expect(workflow.modules[0].agentModel).toBe('agent-only-model');
    expect(workflow.modules[0].runHistory).toHaveLength(1);
    expect(workflow.recordedMessages?.[0].text).toBe('用户原始要求');
    const reopened = parseWorkflow(JSON.stringify({ steps: workflow.modules, edges: [], recorded_messages: workflow.recordedMessages })).workflow;
    expect(reopened.modules[0].agentModel).toBe('agent-only-model');
    expect(reopened.modules[0].fieldStatus).toEqual({ model: { state: 'not_recorded' } });
    expect(reopened.recordedMessages).toEqual(workflow.recordedMessages);
  });
  it('adapts generic nodes into the existing graph and keeps prompts, model, harness and evidence', () => {
    const record = adaptAnalysis(analysis(), 'job1', 'http://localhost:5173');
    const a = record.workflow.modules[0];
    expect(a.prompt).toBe('prompt-a');
    expect(a.model).toBe('qwen-image');
    expect(a.harness).toContain('MCP');
    expect(a.params.seed).toBe(42);
    expect(a.evidence).toEqual([{ id: 'proof-a', pointer: '/graph/nodes/0' }]);
    expect(record.workflow.goldenPath).toEqual(['a', 'b']);
    expect(a.artifacts[0].uri).toBe('/api/creation-parser/jobs/job1/artifacts/image');
    expect(record.workflow.artifacts).toHaveLength(1);
    expect(record.workflow.importSource?.bytes).toBe(300_000_000);
  });
  it('shows candidate relations while excluding them from dirty propagation and layout cycles', () => {
    const { workflow } = adaptAnalysis(analysis(), 'job1', 'http://localhost:5173');
    expect(workflow.edges).toHaveLength(3);
    expect(moduleImpact(workflow, ['a'])).toEqual({ a: 'RECOMPUTE', b: 'REBUILD', c: 'CACHE HIT' });
    expect(moduleImpact(workflow, ['c'])).toEqual({ a: 'CACHE HIT', b: 'CACHE HIT', c: 'RECOMPUTE' });
    expect(modulePositions(workflow).b.x).toBeGreaterThan(modulePositions(workflow).a.x);
  });
  it('retains imported graphs through workspace persistence and isolates Fork edits', () => {
    const record = adaptAnalysis(analysis(), 'job1', 'http://localhost:5173');
    const project = { ...record, id: 'local-test', owned: true, status: 'draft', values: { style: '', character: '', detail: '' } } as WorkspaceProject;
    const storage = new Map<string, string>();
    const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { setItem: (key: string, value: string) => storage.set(key, value), getItem: (key: string) => storage.get(key) ?? null } });
    try {
      saveWorkspace([project]);
      const saved = readWorkspace()[0];
      expect(saved.workflow.edges).toEqual(record.workflow.edges);
      expect(saved.workflow.importSource).toEqual(record.workflow.importSource);
      const fork = forkProject(saved, 'C');
      fork.workflow.modules[0].prompt = 'remixed';
      expect(saved.workflow.modules[0].prompt).toBe('prompt-a');
      expect(fork.basePrompts?.a).toBe('prompt-a');
    } finally {
      if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
      else Reflect.deleteProperty(globalThis, 'localStorage');
    }
  });
});
