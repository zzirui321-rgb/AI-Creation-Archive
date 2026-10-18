import { describe, expect, it } from 'vitest';
import { confirmPreview, historyOf, rememberPreview, restorePreview, matchesPreview } from './preview-history';
import { exampleProjects, forkProject, type ModulePreview } from './workspace';

const artifact = { uri: 'http://127.0.0.1:5173/api/creation-parser/previews/test/artifact', mime: 'image/png' };
const project = () => ({ ...structuredClone(exampleProjects[0]), owned: true });
const preview = (createdAt = 10): ModulePreview => ({ id: `version-${createdAt}`, nodeId: 'character', prompt: 'a cat', model: 'qwen-image-2.0', stage: 'draft', simulated: false, artifact, createdAt });

describe('preview history and final confirmation', () => {
  it('keeps earlier outputs when a newer version is generated', () => {
    const next = rememberPreview(rememberPreview(project(), preview(10)), preview(20));
    expect(historyOf(next).map(v => v.createdAt)).toEqual([20, 10]);
    expect(historyOf(rememberPreview(next, preview(20)))).toHaveLength(2);
  });
  it('restores prompts and models without mutating the prior project', () => {
    const source = project();
    const restored = restorePreview(source, preview());
    expect(restored.workflow.modules.find(n => n.id === 'character')?.prompt).toBe('a cat');
    expect(restored.workflow.modules.find(n => n.id === 'character')?.model).toBe('qwen-image-2.0');
    expect(source.workflow.modules.find(n => n.id === 'character')?.prompt).not.toBe('a cat');
  });
  it('confirms the exact current output without creating a new generation', () => {
    const source = restorePreview(project(), preview());
    const confirmed = confirmPreview(source);
    expect(confirmed.preview?.stage).toBe('final');
    expect(confirmed.preview?.id).toBe(source.preview?.id);
    expect(confirmed.coverArtifact).toEqual(artifact);
    expect(historyOf(confirmed)).toHaveLength(1);
    expect(confirmed.workflow.modules).toHaveLength(source.workflow.modules.length + 1);
    expect(confirmed.workflow.modules.find(n => n.id === 'character')?.artifacts).toEqual(source.workflow.modules.find(n => n.id === 'character')?.artifacts);
    expect(confirmPreview(confirmed).workflow.modules).toHaveLength(confirmed.workflow.modules.length);
    confirmed.workflow.modules.find(n => n.id === confirmed.preview?.nodeId)!.prompt = 'new prompt';
    expect(() => confirmPreview(confirmed)).toThrow('重新生成');
  });
  it('preserves the original artifact as Fork baseline while starting a new history', () => {
    const source = confirmPreview(restorePreview(project(), preview()));
    const fork = forkProject(source, 'Creator');
    expect(fork.baselineArtifact).toEqual(artifact);
    expect(fork.preview).toBeUndefined();
    expect(fork.previewHistory).toEqual([]);
    expect(source.preview?.stage).toBe('final');
  });
  it('confirms a local edit with its configured model while preserving the source and selection', () => {
    const source = project();
    const node = source.workflow.modules.find(n => n.id === 'character')!;
    node.model = 'original-model';
    const local = { ...preview(), prompt: node.prompt, localEdit: { sourceUri: artifact.uri, instruction: 'Change only the jacket to white', rect: { x: .2, y: .3, width: .1, height: .2 }, featherPx: 4, sourceModel: 'original-model' } };
    expect(matchesPreview(local, node)).toBe(true);
    const restored = restorePreview(source, local);
    expect(restored.workflow.modules.find(n => n.id === node.id)?.model).toBe('original-model');
    const next = confirmPreview(restored);
    const generated = next.workflow.modules.find(n => n.id === next.preview?.nodeId)!;
    expect(generated.prompt).toBe(local.localEdit.instruction);
    expect(generated.mode).toBe('local-image-edit');
    expect(generated.inputs.local_edit).toEqual(local.localEdit);
    expect(matchesPreview(next.preview!, generated)).toBe(true);
    expect(next.workflow.modules.find(n => n.id === node.id)?.artifacts).toEqual(node.artifacts);
    expect(node.model).toBe('original-model');
  });

});
