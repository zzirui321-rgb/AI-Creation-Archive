import { useEffect, useState } from 'react';
import { platform } from './platform';

type Project = { id: string; title: string; head: string | null };
type Snapshot = { import_id?: string; id?: string; created_at: string };
async function api<T>(path: string, body?: object, method = body ? 'POST' : 'GET'): Promise<T> {
  const result = await platform.authorizedFetch('/api' + path, { method,
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  const value = await result.json();
  if (!result.ok) throw new Error(value.message || `HTTP ${result.status}`);
  return value;
}

export default function McpUploadPanel({ onSelect, disabled }: {
  onSelect?: (projectId: string, importId: string, title: string) => Promise<void>; disabled: boolean;
}) {
  const [projects, setProjects] = useState<Project[]>([]), [selected, setSelected] = useState('');
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]), [snapshot, setSnapshot] = useState('');
  const [title, setTitle] = useState('MCP 创作记录'), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [token, setToken] = useState<{ id: string; access_token: string; project_id: string }>();
  const [reveal, setReveal] = useState(false);
  async function refresh() {
    const value = await api<{ projects: Project[] }>('/projects?limit=200'); setProjects(value.projects);
    if (selected && onSelect) {
      const result = await api<{ archives: Snapshot[] }>(`/projects/${selected}/archives?limit=200`);
      setSnapshots(result.archives);
    }
  }
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); try { await action(); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  useEffect(() => { setSnapshot(''); setSnapshots([]); void act(refresh); }, [selected]);
  return <details style={{ margin: '20px 0' }}><summary>MCP 自动上传与已接收档案</summary>
    <p>先登录，创建或选择平台项目，再生成仅允许此项目上传的 30 天令牌。令牌只显示一次，请在 MCP 启动环境中设置 AICA_UPLOAD_TOKEN。</p>
    <label className="form-field">新平台项目名称<input value={title} maxLength={200} onChange={e => setTitle(e.target.value)} /></label>
    <button className="button" disabled={busy || disabled || !title.trim()} onClick={() => void act(async () => {
      const value = await platform.createProject(title); setSelected(value.project.id);
    })}>创建 MCP 上传项目</button>
    <label className="form-field">平台项目<select value={selected} onChange={e => setSelected(e.target.value)}><option value="">选择项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.title} · {p.id}</option>)}</select></label>
    {selected && <><p>平台项目 UUID：<code>{selected}</code></p><button className="button" disabled={busy || disabled} onClick={() => void act(async () => {
      setToken(await api('/mcp/upload-tokens', { project_id: selected }));
    })}>生成上传令牌</button></>}
    {token && <div><label className="form-field">AICA_UPLOAD_TOKEN（仅本次显示）<input type={reveal ? 'text' : 'password'} readOnly value={token.access_token} autoComplete="off" onFocus={e => e.target.select()} /></label>
      <button className="button" onClick={() => setReveal(!reveal)}>{reveal ? '遮蔽' : '显示并复制令牌'}</button>
      <p>令牌绑定项目：<code>{token.project_id}</code>。不要粘贴到聊天或提交到 Git。</p>
      <button className="button" onClick={() => setToken(undefined)}>隐藏令牌</button><button className="button" disabled={busy} onClick={() => void act(async () => { await api(`/mcp/upload-tokens/${token.id}`, undefined, 'DELETE'); setToken(undefined); })}>撤销此令牌</button></div>}
    <p>MCP HTTPS 地址：<code>{`https://${window.location.hostname}:8443`}</code>。<a href="/api/platform/ca-certificate" download>下载局域网 CA 证书</a>，请核对部署文档的指纹；配置 AICA_UPLOAD_CA_FILE 指向证书后重启新版 MCP。HTTP 地址不能用于自动上传。</p>
    {onSelect && <><button className="button" disabled={busy || disabled} onClick={() => void act(refresh)}>刷新已上传快照</button>
      {!!snapshots.length && <><label className="form-field">不可变上传快照<select value={snapshot} onChange={e => setSnapshot(e.target.value)}><option value="">选择快照</option>{snapshots.map(s => { const id = s.import_id || s.id || ''; return <option key={id} value={id}>{s.created_at} · {id}</option>; })}</select></label>
        <button className="button primary" disabled={!snapshot || busy || disabled} onClick={() => void act(() => onSelect(selected, snapshot, projects.find(p => p.id === selected)?.title || 'MCP 创作记录'))}>准备此快照的 AI 提炼</button></>}</>}
    {error && <p role="alert" className="inline-error">{error}</p>}
  </details>;
}
