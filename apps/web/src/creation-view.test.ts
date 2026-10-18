import { describe, expect, it } from 'vitest';
import { creationView, displayWorkflow, groupFor, iterationLayout } from './creation-view';
import { parseWorkflow, moduleImpact } from './workflow';

describe('creation view retains editable source records', () => {
  it('separates legacy iterations into a task and preserves every record ID', () => {
    const { workflow } = parseWorkflow(JSON.stringify({ steps: [{ id: 'a', title: 'page-1', prompt: 'one', outputs: { url: 'https://example.test/1.png' }, provenance: 'inferred' }, { id: 'b', title: 'page-2', prompt: 'two', outputs: { url: 'https://example.test/2.png' }, provenance: 'inferred' }, { id: 'report', title: 'Report', kind: 'document', provenance: 'inferred' }] }));
    expect(creationView(workflow).nodes).toHaveLength(2);
    expect(creationView(workflow).tasks).toHaveLength(1);
    expect(groupFor(workflow, 'b')?.member_node_ids).toEqual(['b']);
    expect(displayWorkflow(workflow, 'b').modules[1].prompt).toBe('two');
    expect(workflow.modules[0].prompt).toBe('one');
    expect(workflow.modules).toHaveLength(3);
  });
  it('does not collapse independent recorded steps solely because names are numbered', () => {
    const { workflow } = parseWorkflow(JSON.stringify({ steps: [{ id: 'a', title: 'step-1', outputs: { text: 'first result' }, provenance: 'recorded' }, { id: 'b', title: 'step-2', outputs: { text: 'second result' }, provenance: 'recorded', depends_on: ['a'] }] }));
    expect(creationView(workflow).nodes).toHaveLength(2);
    expect(moduleImpact(workflow, ['a']).b).toBe('REBUILD');
  });
  it('keeps task frames aligned with projected IDs and excludes semantic edges from impact', () => {
    const { workflow } = parseWorkflow(JSON.stringify({ steps: [{ id: 'a', title: 'page-1', prompt: 'one', outputs: { url: 'https://example.test/1.png' } }, { id: 'b', title: 'page-2', prompt: 'two', outputs: { url: 'https://example.test/2.png' } }] }));
    workflow.edges = [{ source: 'a', target: 'b', kind: 'semantic', state: 'candidate', relation: 'revision' }];
    const presentation = displayWorkflow(workflow, 'b');
    const layout = iterationLayout(presentation, false)!;
    expect(layout.frames).toHaveLength(1);
    expect(presentation.modules.every(n => !!layout.positions[n.id])).toBe(true);
    expect(presentation.edges[0].relation).toBe('revision');
    expect(moduleImpact(workflow, ['a']).b).toBe('CACHE HIT');
  });
  it('opens execution prompt while retaining the original request in the same interaction', () => {
    const { workflow } = parseWorkflow(JSON.stringify({ steps: [{ id: 'u', kind: 'intent', prompt: 'draw city' }, { id: 't', kind: 'tool', prompt: 'expanded city prompt', outputs: { url: 'https://example.test/city.png' } }] }));
    const view = creationView(workflow);
    expect(view.nodes).toHaveLength(1);
    expect(view.nodes[0].user_node_id).toBe('u');
    expect(view.nodes[0].primary_node_id).toBe('t');
    expect(displayWorkflow(workflow, 'u').modules[0].prompt).toBe('expanded city prompt');
  });

  it('rotates task frames, node axes and branches with the layout direction', () => {
    const { workflow } = parseWorkflow(JSON.stringify({ steps: [{ id: 'a', title: 'cover-1', prompt: 'first', outputs: { url: 'https://example.test/a.png' } }, { id: 'b', title: 'cover-2', prompt: 'second', outputs: { url: 'https://example.test/b.png' }, depends_on: ['a'] }, { id: 'c', title: 'cover-3', prompt: 'branch', outputs: { url: 'https://example.test/c.png' }, depends_on: ['a'] }, { id: 'd', title: 'video-1', prompt: 'video', outputs: { url: 'https://example.test/d.mp4' }, depends_on: ['b'] }] }));
    const projected = displayWorkflow(workflow, null);
    const horizontal = iterationLayout(projected, false)!, vertical = iterationLayout(projected, true)!;
    const ids = projected.modules.map(n => n.id);
    expect(horizontal.positions[ids[1]].x).toBeGreaterThan(horizontal.positions[ids[0]].x);
    expect(horizontal.positions[ids[1]].x).toBe(horizontal.positions[ids[2]].x);
    expect(horizontal.positions[ids[2]].y).toBeGreaterThan(horizontal.positions[ids[1]].y);
    expect(vertical.positions[ids[1]].y).toBeGreaterThan(vertical.positions[ids[0]].y);
    expect(vertical.positions[ids[1]].y).toBe(vertical.positions[ids[2]].y);
    expect(vertical.positions[ids[2]].x).toBeGreaterThan(vertical.positions[ids[1]].x);
    expect(horizontal.frames[1].x).toBeGreaterThan(horizontal.frames[0].x + horizontal.frames[0].width);
    expect(vertical.frames[1].y).toBeGreaterThan(vertical.frames[0].y + vertical.frames[0].height);
    expect(horizontal.frames[1].y).toBe(horizontal.frames[0].y);
    expect(vertical.frames[1].x).toBe(vertical.frames[0].x);
  });
  it('keeps prompt-only records available without manufacturing results', () => {
    const { workflow } = parseWorkflow(JSON.stringify({ steps: [{ id: 'draft', prompt: 'not yet generated' }] }));
    expect(creationView(workflow).nodes).toHaveLength(0);
    expect(creationView(workflow).supporting_records[0].id).toBe('draft');
  });

});
