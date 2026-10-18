import { platform } from './platform';
import type { AppSettings, ModelCredentials } from './settings';
import { validEndpoint } from './settings';
import { safeMedia, type WorkflowModule } from './workflow';
import type { ModulePreview, LocalEdit } from './workspace';

export function previewKind(module: WorkflowModule) { return /video|视频|wan[x.\d-]|t2v|i2v|sora|veo|kling/i.test(module.kind + ' ' + module.mode + ' ' + module.model + ' ' + module.title) ? 'video' : 'image'; }

export interface PreviewRequest { projectId: string; module: WorkflowModule; stage: 'draft' | 'final'; settings: AppSettings; credentials: ModelCredentials; signal: AbortSignal; reference?: string; localEdit?: LocalEdit; onProgress?: (message: string) => void }
export interface ModulePreviewService { generate(request: PreviewRequest): Promise<ModulePreview> }
export const modulePreviewService: ModulePreviewService = {
  async generate({ projectId, module, stage, settings, credentials, signal, reference, localEdit, onProgress }) {
    const kind = localEdit ? 'image' : previewKind(module); const config = settings[kind];
    const model = localEdit ? config.model : module.model || config.model;
    if (localEdit && (config.provider !== 'qwen' || !credentials.image.trim() || !localEdit.instruction.trim())) throw new Error('局部编辑需填写修改要求，并在设置中配置千问生图模型与 API Key。');
    if (!localEdit && !module.prompt.trim()) throw new Error('请先填写模块提示词。');
    if (!settings.previewEndpoint && !credentials[kind].trim()) {
      await new Promise<void>((resolve, reject) => { const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); }; const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 900); if (signal.aborted) abort(); else signal.addEventListener('abort', abort, { once: true }); });
      return { nodeId: module.id, prompt: module.prompt, model, stage, simulated: true, createdAt: Date.now() };
    }
    if (!settings.previewEndpoint || localEdit) {
      if (localEdit) {
        const response = await platform.authorizedFetch('/api/creation-parser/health', { signal });
        const health = response.headers.get('content-type')?.includes('application/json') ? await response.json() : null;
        if (!response.ok || !health?.features?.includes(localEdit.points ? 'local-image-edit-shapes' : 'local-image-edit')) throw new Error('本地服务尚未支持局部编辑，请重新启动 AiCA-graph-lab 后重试。');
      }
      const request = { request_id: Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join(''), project_id: projectId, node_id: module.id, prompt: localEdit?.instruction || module.prompt, model, kind, stage, provider: config.provider, api_base: config.apiBase, api_key: credentials[kind], params: module.params, reference, ...(localEdit ? { local_edit: { source_uri: localEdit.sourceUri, instruction: localEdit.instruction, rect: localEdit.rect, feather_px: localEdit.featherPx, shape: localEdit.shape, rotation: localEdit.rotation, points: localEdit.points } } : {}) };
      const base = '/api/creation-parser/previews';
      let identity: string | undefined;
      const cancel = () => { if (identity) void platform.authorizedFetch(`${base}/${identity}/cancel`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      try {
        const response = await platform.authorizedFetch(base, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) });
        if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('本地生成服务未启动，请启动 AiCA-graph-lab 后重试。');
        let job = await response.json();
        if (!response.ok) throw new Error(job.error || '无法创建预览任务。');
        identity = job.id;
        if (!/^[a-f0-9]{32}$/.test(identity ?? '')) throw new Error('预览服务返回无效任务 ID。');
        while (job.status !== 'ready') {
          if (job.status === 'error' || job.status === 'cancelled') throw new Error(job.error || job.message || '生成未完成。');
          onProgress?.(job.message || '正在生成…');
          await new Promise<void>((resolve, reject) => {
            const stop = () => { clearTimeout(timer); reject(new DOMException('已取消', 'AbortError')); };
            const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve(); }, 800);
            signal.addEventListener('abort', stop, { once: true });
            if (signal.aborted) { signal.removeEventListener('abort', stop); stop(); }
          });
          const status = await platform.authorizedFetch(`${base}/${identity}`, { signal });
          job = await status.json();
          if (!status.ok) throw new Error(job.error || '读取生成进度失败。');
        }
        const url = new URL(job.artifact?.url || '', window.location.origin);
        if (url.origin !== window.location.origin || !url.pathname.startsWith(base + '/') || !/^(image|video)\//.test(job.artifact?.mime ?? '')) throw new Error('本地服务没有返回有效产物。');
        let edit: LocalEdit | undefined;
        if (localEdit) {
          const source = new URL(job.local_edit?.source_url || '', window.location.origin);
          if (source.origin !== window.location.origin || source.pathname !== `${base}/${identity}/source`) throw new Error('局部编辑服务没有返回有效的原图快照。');
          edit = { ...localEdit, sourceUri: source.pathname + source.search, sourceModel: module.model, sourceSha256: job.local_edit.source_sha256, sourceWidth: job.local_edit.source_width, sourceHeight: job.local_edit.source_height };
        }
        return { id: identity, nodeId: module.id, prompt: module.prompt, model, stage, simulated: false, artifact: { uri: url.pathname + url.search, mime: job.artifact.mime, sha256: job.artifact.sha256 }, localEdit: edit, createdAt: job.created_at || Date.now() };
      } catch (error) {
        if (signal.aborted) cancel();
        throw error;
      } finally { signal.removeEventListener('abort', cancel); }
    }
    if (!validEndpoint(settings.previewEndpoint)) throw new Error('请在我的设置中填写有效的预览网关 URL。');
    const response = await fetch(settings.previewEndpoint, { method: 'POST', signal, headers: { 'Content-Type': 'application/json', ...(credentials[kind] ? { Authorization: `Bearer ${credentials[kind]}` } : {}) }, body: JSON.stringify({ project_id: projectId, node_id: module.id, prompt: module.prompt, model, kind, stage, mode: module.mode, harness: module.harness, params: module.params, provider: config.provider, api_base: config.apiBase }) });
    if (!response.ok) throw new Error(`预览网关返回 ${response.status}，请检查服务配置。`);
    const body = await response.json(); const artifact = body.artifact;
    if (!artifact || typeof artifact.url !== 'string' || typeof artifact.mime !== 'string' || !safeMedia(artifact.url, artifact.mime) || !/^(image|video)\//.test(artifact.mime)) throw new Error('网关未返回有效的 image/video 产物（artifact.url、artifact.mime）。');
    return { nodeId: module.id, prompt: module.prompt, model, stage, simulated: false, artifact: { uri: artifact.url, mime: artifact.mime }, createdAt: Date.now() };
  },
};
