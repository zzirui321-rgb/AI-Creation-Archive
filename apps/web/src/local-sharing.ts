import type { WorkspaceProject } from './workspace';

const catalogKey = 'aica.shared.catalog.v1';
export function readSharedCatalog(): WorkspaceProject[] {
  try {
    const values = JSON.parse(localStorage.getItem(catalogKey) ?? '[]');
    return Array.isArray(values) ? values.filter(p => /^shared-[a-f0-9]{32}$/.test(p?.id ?? '') && !p.owned && typeof p.title === 'string' && Array.isArray(p.workflow?.modules) && Array.isArray(p.workflow?.edges)) : [];
  } catch { return []; }
}
export function saveSharedCatalog(projects: WorkspaceProject[]) { localStorage.setItem(catalogKey, JSON.stringify(projects)); }

export async function createLocalShare(project: WorkspaceProject): Promise<string> {
  const response = await fetch('/api/creation-parser/shares', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(project) });
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('分享服务未启动，请启动本地解析与生成服务。');
  const result = await response.json();
  if (!response.ok || !/^[a-f0-9]{32}$/.test(result.id ?? '')) throw new Error(result.error || '无法创建分享链接。');
  return `${window.location.origin}/#/works/shared-${result.id}`;
}

export async function readLocalShare(id: string, signal: AbortSignal): Promise<WorkspaceProject> {
  if (!/^shared-[a-f0-9]{32}$/.test(id)) throw new Error('分享标识无效。');
  const response = await fetch(`/api/creation-parser/shares/${id.slice(7)}`, { signal });
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('无法读取分享，请确认本地服务正在运行。');
  const project = await response.json();
  if (!response.ok) throw new Error(project.error || '分享不存在。');
  if (!Array.isArray(project.workflow?.modules) || !Array.isArray(project.workflow?.edges) || typeof project.title !== 'string') throw new Error('分享记录格式无效。');
  return { ...project, id, owned: false };
}
