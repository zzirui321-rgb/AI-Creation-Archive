import type { ImageProviderConfig } from '@aica/artifact-renderers';
export type ModelConfig = { provider: string; model: string; apiBase: string; draftModel?: string; hqModel?: string; timeoutSeconds?: number; allowFallback?: boolean };
export type AppSettings = { displayName: string; autoSave: boolean; highlightWords: boolean; image: ModelConfig; video: ModelConfig; previewEndpoint: string };
export const defaultSettings: AppSettings = { displayName: 'Creator', autoSave: true, highlightWords: true, image: { provider: 'qwen', model: 'qwen-image-2.0', apiBase: 'https://dashscope.aliyuncs.com/api/v1' }, video: { provider: 'custom', model: '', apiBase: '' }, previewEndpoint: '' };
const key = 'aica.settings.v1';
export function readSettings(): AppSettings {
  try { const v = JSON.parse(localStorage.getItem(key) ?? 'null'); return v ? { ...defaultSettings, displayName: typeof v.displayName === 'string' ? v.displayName.slice(0, 40) : 'Creator', autoSave: typeof v.autoSave === 'boolean' ? v.autoSave : true, highlightWords: typeof v.highlightWords === 'boolean' ? v.highlightWords : true, image: config(v.image, defaultSettings.image), video: config(v.video, defaultSettings.video), previewEndpoint: typeof v.previewEndpoint === 'string' ? v.previewEndpoint : '' } : structuredClone(defaultSettings); } catch { return structuredClone(defaultSettings); }
}
function config(value: any, base: ModelConfig): ModelConfig { return { provider: typeof value?.provider === 'string' ? value.provider : base.provider, model: typeof value?.model === 'string' ? value.model : base.model, apiBase: typeof value?.apiBase === 'string' ? value.apiBase : base.apiBase }; }
export function saveSettings(settings: AppSettings) { localStorage.setItem(key, JSON.stringify({ displayName: settings.displayName, autoSave: settings.autoSave, highlightWords: settings.highlightWords, image: config(settings.image, defaultSettings.image), video: config(settings.video, defaultSettings.video), previewEndpoint: settings.previewEndpoint })); }
export function validEndpoint(value: string) { if (!value) return true; try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash; } catch { return false; } }
export type ModelCredentials = { image: string; video: string };
const credentialsKey = 'aica.model-credentials.v1';
export function readCredentials(): ModelCredentials {
  try { const value = JSON.parse(localStorage.getItem(credentialsKey) ?? '{}'); return { image: typeof value.image === 'string' ? value.image : '', video: typeof value.video === 'string' ? value.video : '' }; } catch { return { image: '', video: '' }; }
}
export function saveCredentials(credentials: ModelCredentials) {
  const value = { image: credentials.image.trim(), video: credentials.video.trim() };
  if (value.image || value.video) localStorage.setItem(credentialsKey, JSON.stringify(value));
  else localStorage.removeItem(credentialsKey);
}

export function imageProviderConfig(settings: AppSettings, credentials: ModelCredentials): ImageProviderConfig | undefined {
  const image = settings.image;
  if (image.provider !== 'local_fallback' && !credentials.image) return undefined;
  if (!['custom', 'openai', 'siliconflow', 'fal', 'qwen', 'sdcpp', 'local_fallback'].includes(image.provider)) throw new Error('请选择受支持的生图接口协议');
  return { provider: image.provider as ImageProviderConfig['provider'], api_base: image.apiBase, api_key: credentials.image, model: image.model, draft_model: image.draftModel || '', hq_model: image.hqModel || '', timeout_seconds: image.timeoutSeconds || 10, allow_fallback: image.allowFallback ?? true };
}
