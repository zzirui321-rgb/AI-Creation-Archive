import { platform } from './platform';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { modulePreviewService } from './preview-service';
import { defaultSettings, validEndpoint } from './settings';
import { exampleProjects } from './workspace';
const module = { ...exampleProjects[0].workflow.modules[2], prompt: '银蓝色月光', model: '' };
beforeEach(() => { vi.spyOn(platform, 'authorizedFetch').mockImplementation((input, init) => fetch(input, init)); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe('preview gateway boundary', () => {
  it('passes the edited module and default model to the configured gateway without putting the key in the payload', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ artifact: { url: 'https://example.com/result.png', mime: 'image/png' } }) }); vi.stubGlobal('fetch', fetcher);
    const result = await modulePreviewService.generate({ projectId: 'p', module, stage: 'draft', settings: { ...defaultSettings, previewEndpoint: 'http://127.0.0.1:8000/preview' }, credentials: { image: 'test-session-key', video: '' }, signal: new AbortController().signal });
    expect(result.simulated).toBe(false); expect(result.prompt).toBe(module.prompt);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ node_id: module.id, prompt: module.prompt, model: defaultSettings.image.model });
    expect(fetcher.mock.calls[0][1].headers.Authorization).toBe('Bearer test-session-key'); expect(fetcher.mock.calls[0][1].body).not.toContain('test-session-key'); expect(JSON.stringify(result)).not.toContain('test-session-key');
  });
  it('does not show a successful preview for malformed or unsafe gateway artifacts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ artifact: { url: 'javascript:alert(1)', mime: 'image/png' } }) }));
    await expect(modulePreviewService.generate({ projectId: 'p', module, stage: 'draft', settings: { ...defaultSettings, previewEndpoint: 'https://example.com/preview' }, credentials: { image: '', video: '' }, signal: new AbortController().signal })).rejects.toThrow('有效');
  });
  it('rejects credential-bearing and non-HTTP endpoint addresses', () => {
    expect(validEndpoint('https://user:secret@example.com')).toBe(false); expect(validEndpoint('https://example.com?key=x')).toBe(false); expect(validEndpoint('javascript:alert(1)')).toBe(false); expect(validEndpoint('http://127.0.0.1:8000/api/previews')).toBe(true);
  });
  it('routes configured credentials through the local adapter and returns the stored artifact', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:5173' } });
    const id = 'a'.repeat(32);
    const fetcher = vi.fn().mockResolvedValue({ ok: true, headers: { get: () => 'application/json' }, json: async () => ({ id, status: 'ready', artifact: { url: `/api/creation-parser/previews/${id}/artifact`, mime: 'image/png', sha256: 'hash' }, created_at: 123 }) });
    vi.stubGlobal('fetch', fetcher);
    const result = await modulePreviewService.generate({ projectId: 'p', module, stage: 'draft', settings: defaultSettings, credentials: { image: 'session-key', video: '' }, signal: new AbortController().signal });
    expect(fetcher.mock.calls[0][0]).toBe('/api/creation-parser/previews');
    expect(fetcher.mock.calls[0][1].headers.Authorization).toBeUndefined();
    expect(JSON.parse(fetcher.mock.calls[0][1].body).api_key).toBe('session-key');
    expect(result.artifact?.uri).toContain('/previews/' + id + '/artifact');
    expect(result.simulated).toBe(false);
    expect(JSON.stringify(result)).not.toContain('session-key');
  });
  it('sends a local selection through the native image adapter and stores the exact source snapshot', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:5173' } });
    const id = 'b'.repeat(32);
    const fetcher = vi.fn().mockResolvedValue({ ok: true, headers: { get: () => 'application/json' }, json: async () => ({ id, status: 'ready', artifact: { url: `/api/creation-parser/previews/${id}/artifact`, mime: 'image/png' }, local_edit: { source_url: `/api/creation-parser/previews/${id}/source`, source_sha256: 'source-hash', source_width: 100, source_height: 100 } }) });
    vi.stubGlobal('fetch', fetcher);
    fetcher.mockResolvedValueOnce({ ok: true, headers: { get: () => 'application/json' }, json: async () => ({ features: ['local-image-edit'] }) });
    const edit = { sourceUri: 'https://example.com/source.png', instruction: 'white jacket', rect: { x: .1, y: .2, width: .3, height: .4 }, featherPx: 4 };
    const result = await modulePreviewService.generate({ projectId: 'p', module: { ...module, prompt: '', model: 'old-model' }, localEdit: edit, stage: 'draft', settings: { ...defaultSettings, previewEndpoint: 'https://team.example/preview' }, credentials: { image: 'local-fake-key', video: '' }, signal: new AbortController().signal });
    const body = JSON.parse(fetcher.mock.calls[1][1].body);
    expect(body.local_edit.rect).toEqual(edit.rect);
    expect(body.prompt).toBe('white jacket');
    expect(body.model).toBe(defaultSettings.image.model);
    expect(fetcher.mock.calls[1][0]).toBe('/api/creation-parser/previews');
    expect(result.localEdit?.sourceUri).toContain(`/previews/${id}/source`);
    expect(result.localEdit?.sourceModel).toBe('old-model');
    expect(result.prompt).toBe('');
    expect(JSON.stringify(result)).not.toContain('local-fake-key');
  });

  it('refuses shape editing on an old backend before starting a paid generation', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, headers: { get: () => 'application/json' }, json: async () => ({ features: ['local-image-edit'] }) });
    vi.stubGlobal('fetch', fetcher);
    await expect(modulePreviewService.generate({ projectId: 'p', module, stage: 'draft', settings: defaultSettings, credentials: { image: 'fake-key', video: '' }, signal: new AbortController().signal, localEdit: { sourceUri: 'https://example.com/original.png', instruction: 'change clothes', rect: { x: 0, y: 0, width: 1, height: 1 }, featherPx: 4, shape: 'freehand', points: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 0, y: 1 }] } })).rejects.toThrow('重新启动');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/creation-parser/health');
  });

});
