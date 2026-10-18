import { describe, expect, it } from 'vitest';
import { attachModel, generationTarget } from './three-d';
import type { WorkspaceProject } from './workspace';
import type { RodinJob } from './ThreeDPanel';
import { safeMedia } from './workflow';

describe('Image to 3D provenance', () => {
  it('keeps source image, creates one derived model and survives export/import-shaped serialization', () => {
    const source = { id: 'portrait', title: 'Portrait', kind: 'image', artifacts: [{ id: 'image', uri: '/api/repos/a/images/image', mime: 'image/png', label: 'Portrait' }] };
    const project = { id: 'repo', workflow: { modules: [source], edges: [], goldenPath: [], source: 'imported', warnings: [] } } as unknown as WorkspaceProject;
    const job: RodinJob = { id: 'a'.repeat(32), state: 'succeeded', created_at: 1, recipe: { source_node: 'portrait', source_artifact: 'image', source_commit: 'original', input_sha256: 'digest', tier: 'Gen-2', prompt: 'same outfit' }, artifact: { id: 'model', uri: '/api/3d/jobs/' + 'a'.repeat(32) + '/model', mime: 'model/gltf-binary', label: 'GLB' } };
    const next = attachModel(project, job);
    expect(project.workflow.modules).toHaveLength(1);
    expect(next.workflow.modules[0].artifacts).toEqual(source.artifacts);
    expect(next.workflow.modules[1].inputs.source_commit_id).toBe('original');
    expect(next.workflow.modules[1].artifacts[0].uri).toContain('?repo_id=repo');
    expect(next.workflow.edges[0]).toMatchObject({ source: 'portrait', target: 'rodin-' + job.id });
    expect(attachModel(next, job)).toBe(next);
    expect(JSON.parse(JSON.stringify(next)).workflow.creationView.nodes.at(-1).result_inline_artifacts[0].mime).toBe('model/gltf-binary');
    expect(safeMedia(next.workflow.modules[1].artifacts[0].uri)).toBe(true);
    expect(generationTarget(next, next.workflow.modules[1].id, 'regenerate')).toMatchObject({ nodeId: 'portrait', artifact: { id: 'image' }, prompt: 'same outfit', mode: 'regenerate' });
    expect(generationTarget(next, 'portrait', 'regenerate', 'unrelated-image')).toBeUndefined();
    expect(generationTarget(project, 'portrait', 'regenerate')).toBeUndefined();
    const second = attachModel(next, { ...job, id: 'b'.repeat(32), artifact: { ...job.artifact!, id: 'model-2' } });
    expect(second.workflow.modules).toHaveLength(3);
    expect(second.workflow.modules[1]).toEqual(next.workflow.modules[1]);
    expect(second.workflow.modules[2].title).toContain('版本 2');
    expect(generationTarget(second, null, 'regenerate')?.artifact.id).toBe('image');
    const missingSource = { ...next, workflow: { ...next.workflow, modules: next.workflow.modules.slice(1) } };
    expect(generationTarget(missingSource, next.workflow.modules[1].id, 'regenerate')).toBeUndefined();
  });
});
