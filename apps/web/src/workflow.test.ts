import { describe, expect, it } from 'vitest';
import { moduleImpact, modulePositions, parseWorkflow, redact } from './workflow';
import { exampleProjects, forkProject } from './workspace';
const encode = (value: unknown) => JSON.stringify(value);
const record = { title: 'A', steps: [{ id: 'a', prompt: '月光' }, { id: 'b', depends_on: ['a'], prompt: '温室' }, { id: 'c', depends_on: ['b'] }] };

describe('backend module records', () => {
  it('uses explicit dependencies and does not invent edges for independent steps', () => {
    const { workflow } = parseWorkflow(encode(record));
    expect(workflow.edges).toEqual([{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }]);
    expect(moduleImpact(workflow, ['b'])).toEqual({ a: 'CACHE HIT', b: 'RECOMPUTE', c: 'REBUILD' });
    expect(modulePositions(workflow).c.x).toBeGreaterThan(modulePositions(workflow).b.x);
    const independent = parseWorkflow(encode({ steps: [{ id: 'a' }, { id: 'b' }] }));
    expect(independent.workflow.edges).toEqual([]); expect(independent.workflow.warnings.join('')).toContain('不推测');
  });
  it('reads formal variable bindings, run harness references and artifacts', () => {
    const parsed = parseWorkflow(encode({ aica_version: '0.1', metadata: { title: 'Archive' }, variables: { character: { value: '橘猫' } }, graph: { nodes: [{ id: 'image', kind: 'model', label: 'Image', execution: { model: 'qwen-image', inputs: { prompt: { kind: 'template', template: '{{subject}} 在月球', variables: { subject: 'character' } } } }, provenance: { mode: 'recorded', evidence: [{ source: 'trace' }] } }], edges: [] }, runs: [{ node_id: 'image', harness_id: 'h1' }], harnesses: [{ id: 'h1', name: 'Codex', config: { api_key: 'do-not-store' } }], artifacts: [{ id: 'out', producer_node_id: 'image', uri: 'https://example.com/a.png', mime_type: 'image/png' }] }));
    expect(parsed.workflow.modules[0].prompt).toBe('橘猫 在月球');
    expect(parsed.workflow.modules[0].harness).toContain('Codex');
    expect(JSON.stringify(parsed)).not.toContain('do-not-store');
    expect(parsed.workflow.modules[0].artifacts[0].mime).toBe('image/png');
  });
  it('rejects duplicate IDs, missing dependencies, cycles and non-record JSON', () => {
    expect(() => parseWorkflow('{')).toThrow('JSON');
    expect(() => parseWorkflow(encode({ messages: [] }))).toThrow('未找到');
    expect(() => parseWorkflow(encode({ steps: [{ id: 'a' }, { id: 'a' }] }))).toThrow('重复');
    expect(() => parseWorkflow(encode({ steps: [{ id: 'a', depends_on: ['missing'] }] }))).toThrow('不存在');
    expect(() => parseWorkflow(encode({ steps: [{ id: 'a', depends_on: ['b'] }, { id: 'b', depends_on: ['a'] }] }))).toThrow('循环');
  });
  it('forks isolated copies and supports multiple derivatives of the same source', () => {
    const source = exampleProjects[0]; const a = forkProject(source, 'C'); const b = forkProject(source, 'C');
    expect(a.id).not.toBe(b.id); expect(a.parentId).toBe(source.id);
    a.workflow.modules[0].prompt = 'changed'; expect(source.workflow.modules[0].prompt).not.toBe('changed');
    expect(a.basePrompts?.[a.workflow.modules[0].id]).toBe(source.workflow.modules[0].prompt);
  });
  it('removes known secret fields recursively before display or export', () => {
    expect(redact({ password: 'x', config: { API_KEY: 'x', Authorization: 'x', model: 'qwen' }, credentials: { token: 'x' } })).toEqual({ config: { model: 'qwen' } });
  });
});
