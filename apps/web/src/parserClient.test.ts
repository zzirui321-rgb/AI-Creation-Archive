import { afterEach, expect, it, vi } from 'vitest';
vi.mock('./platform', () => ({ platform: { authorizedFetch: (...args: Parameters<typeof fetch>) => fetch(...args) } }));
import { importParserBundle, parserRequest, readSemanticGraph } from './parserClient';

afterEach(() => vi.unstubAllGlobals());

it('uploads exact transfer bytes with a matching hash and separate parser identities', async () => {
  const bytes = new TextEncoder().encode('transfer bytes').buffer;
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ project: { id: 'parser-project' } }) }).mockResolvedValueOnce({ ok: true, json: async () => ({ import_id: 'parser-import' }) });
  vi.stubGlobal('fetch', fetcher);
  await expect(importParserBundle(bytes, 'Archive', new AbortController().signal)).resolves.toEqual({ projectId: 'parser-project', importId: 'parser-import' });
  expect(fetcher.mock.calls[1][0]).toBe('/api/projects/parser-project/imports');
  const request = fetcher.mock.calls[1][1];
  expect(request.body).toBe(bytes);
  expect(request.headers['Idempotency-Key']).toBe(`sha256:${request.headers['X-AiCA-Bundle-SHA256']}`);
  expect(request.headers['X-AiCA-Bundle-SHA256']).toHaveLength(64);
  expect(request.headers.Authorization).toBeUndefined();
});

it('preserves backend error codes instead of presenting a successful graph', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ code: 'analysis_input_conflict', message: 'Input changed' }) }));
  await expect(parserRequest('/projects/p/semantic-parses')).rejects.toThrow('analysis_input_conflict: Input changed');
});

it.each([{ executable: true, import_id: 'i' }, { executable: false, import_id: 'wrong' }])('rejects unexpected executable or source contracts: %j', async value => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ graph: { format: 'aica-semantic-graph/0.1', executable: value.executable, source: { import_id: value.import_id } } }) }));
  await expect(readSemanticGraph({ projectId: 'p', importId: 'i' }, { id: 'parse', state: 'succeeded', graph_id: 'g', revision_id: 'r', error_code: null, usage: null }, new AbortController().signal)).rejects.toThrow('contract or source');
});
