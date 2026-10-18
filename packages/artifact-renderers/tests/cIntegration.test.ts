import { afterEach, describe, expect, it, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { Blob as NodeBlob } from 'node:buffer';
import { ArchiveClient, operationValue, variableFields, loadArchiveAssets, PREVIEW_METADATA_MIME } from '../src';
import { fixture, artifact, run } from './fixtures';

afterEach(() => vi.unstubAllGlobals());

describe('protocol driven C/D bridge', () => {
  it('uses the configured Runtime prefix and selected provider for plan and execution', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const fetcher = vi.fn().mockImplementation(async () => Response.json({ job_id: 'job' }));
    const client = new ArchiveClient(fetcher, '/runtime-api');
    const provider = { provider: 'sdcpp' as const, api_base: 'http://local.invalid/v1/images/generations', api_key: 'session-key', model: 'local-model' };
    await client.plan('p', 'snapshot', 'draft', undefined, provider);
    await client.execute('p', { archive_id: 'snapshot', archive: fixture() }, 'final', undefined, provider);
    expect(fetcher.mock.calls[0][0]).toBe('/runtime-api/projects/p/plan');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ image_config: provider, stage: 'draft' });
    expect(fetcher.mock.calls[1][0]).toBe('/runtime-api/projects/p/executions');
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({ image_config: provider, stage: 'final', record: true, execute: true });
  });
  it.each([1, 3, 10])('reads %i dynamic variables without field name conventions', count => {
    const archive = fixture();
    archive.variables = Object.fromEntries(Array.from({ length: count }, (_, index) => [`config-${index}`, { type: 'string', value: `v${index}`, default: '', modes: ['keep', 'override', 'append'], bindings: [] }]));
    archive.interactive_questions = [{ id: 'q', variable_id: 'config-0', prompt: 'Actual question', modes: ['keep', 'override'] }];
    const fields = variableFields(archive);
    expect(fields).toHaveLength(count);
    expect(fields[0].question).toBe('Actual question');
    expect(fields[0].modes).toEqual(['keep', 'override']);
  });
  it('preserves typed variable operation values', () => {
    const string = fixture().variables.theme;
    expect(operationValue(string, 'append', 'blue')).toBe('blue');
    const array = { ...string, type: 'array' as const, value: ['a'], default: ['a'] };
    expect(operationValue(array, 'append', '["b"]')).toEqual(['b']);
    expect(() => operationValue(array, 'override', '7')).toThrow('array');
  });
  it('calls actual D endpoints and passes the immutable version plus consent', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const fetcher = vi.fn().mockImplementation(async () => Response.json({ archive_id: 'fork' }));
    const api = new ArchiveClient(fetcher);
    const snapshot = { archive_id: 'snapshot', archive: fixture() };
    await api.fork('project', snapshot, [{ variable_id: 'theme', mode: 'override', value: 'ocean' }]);
    expect(fetcher.mock.calls[0][0]).toBe('/api/projects/project/forks');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ archive_id: 'snapshot', expected_revision: 'revision' });
    await api.execute('project', snapshot, 'final');
    expect(fetcher.mock.calls[1][0]).toBe('/api/projects/project/executions');
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({ stage: 'final', execute: true, record: true });
  });
  it('rejects tampered data from the authenticated artifact API', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const api = new ArchiveClient(vi.fn().mockResolvedValue(new Response('tampered')));
    await expect(api.bytes('project', artifact())).rejects.toThrow('hash');
  });
  it('rejects the wrong immutable archive snapshot', async () => {
    const api = new ArchiveClient(vi.fn().mockResolvedValue(Response.json({ archive_id: 'other', archive: fixture() })));
    await expect(api.snapshot('p', 'wanted')).rejects.toThrow('version');
  });
  it('hydrates cached sidecars by hash and current upstream producer and releases URLs', async () => {
    vi.stubGlobal('Blob', NodeBlob);
    const create = vi.fn().mockReturnValue('blob:verified'), revoke = vi.fn();
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke });
    const archive = fixture();
    const old = { ...artifact('old'), producer_node_id: 'image' };
    const current = { ...old, id: 'current', producer_run_id: 'upstream-current' };
    const primary = { ...artifact('layout-image'), producer_node_id: 'layout', producer_run_id: 'layout-current', sha256: 'b'.repeat(64) };
    const sidecar = { ...artifact('sidecar', PREVIEW_METADATA_MIME), producer_run_id: 'layout-current' };
    archive.artifacts = [old, current, primary, sidecar];
    archive.runs = [{ ...run('image', 'CACHE_HIT', ['current']), id: 'upstream-current' }, { ...run('layout', 'REBUILD', ['layout-image', 'sidecar']), id: 'layout-current' }];
    archive.extensions = { 'aica.preview': { runs: { historical: { source: 'live_api' } } } };
    const api = new ArchiveClient();
    vi.spyOn(api, 'bytes').mockImplementation(async (_p, a) => new NodeBlob([a.id === 'sidecar' ? JSON.stringify({ format: 'aica-preview/1', metadata: { primary_sha256: primary.sha256, regions: [{ artifact_id: 'old' }], reference_hashes: { old: old.sha256 } } }) : 'verified bytes']) as Blob);
    const loaded = await loadArchiveAssets(api, 'p', archive, new AbortController().signal);
    const metadata = loaded.archive.extensions?.['aica.preview'] as any;
    expect(metadata.runs['layout-current'].regions[0].artifact_id).toBe('current');
    expect(metadata.runs.historical.source).toBe('live_api');
    expect((archive.extensions?.['aica.preview'] as any).runs['layout-current']).toBeUndefined();
    loaded.dispose(); expect(revoke).toHaveBeenCalledTimes(4);
  });
});
