import { useEffect, useState } from 'react';
import { platform } from './platform';
import type { WorkspaceProject } from './workspace';

type Asset = { id: string; label: string; mime: string; size: number; available: boolean };
export default function ArchiveAssets({ project }: { project: WorkspaceProject }) {
  const [assets, setAssets] = useState<Asset[]>([]), [page, setPage] = useState(0), [error, setError] = useState('');
  const [selected, setSelected] = useState<Asset>(), [url, setUrl] = useState(''), [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const base = `/api/repos/${project.id}`, query = `?commit_id=${project.archiveCommit}`;
  useEffect(() => {
    const controller = new AbortController(); setError(''); setAssets([]); setPage(0); setSelected(undefined);
    void platform.authorizedFetch(base + '/archive-info' + query, { signal: controller.signal }).then(async response => {
      const data = await response.json(); if (!response.ok) throw new Error(data.message || '归档附件读取失败');
      if (!controller.signal.aborted) setAssets(data.artifacts);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [base, query]);
  useEffect(() => {
    setUrl(''); setText(''); if (!selected) return;
    const controller = new AbortController(); let objectUrl = '';
    void platform.authorizedFetch(`${base}/artifacts/${encodeURIComponent(selected.id)}${query}`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('附件未上传或暂不可读取');
      const blob = await response.blob(); if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
      if (/json|text\//.test(selected.mime)) {
        const preview = await blob.slice(0, 256 * 1024).text();
        if (!controller.signal.aborted) setText(preview + (blob.size > 256 * 1024 ? '\n…预览已截断，请下载完整文件。' : ''));
      }
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [base, query, selected]);
  async function download(bundle = false) {
    setBusy(true); setError('');
    try {
      const response = await platform.authorizedFetch(base + '/archive' + query, bundle ? { headers: { Accept: 'application/vnd.aica.bundle+zip' } } : {});
      if (!response.ok) throw new Error(bundle ? '完整版本包下载失败；请检查附件是否齐全' : '原始归档下载失败');
      const link = document.createElement('a'), blobUrl = URL.createObjectURL(await response.blob());
      link.href = blobUrl; link.download = bundle ? 'archive.aica.zip' : 'aica.json'; link.click(); setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="surface-section" aria-label="MCP 原始归档与附件">
    <h3>MCP 原始归档与附件 · {assets.length} 个</h3>
    <p>Graph 直接来自上传记录，未经 AI 提炼。原始归档和附件固定到当前版本。</p>
    <button className="button" disabled={busy} onClick={() => void download()}>下载原始 aica.json</button>
    <button className="button" disabled={busy} onClick={() => void download(true)}>下载完整版本包（含已保存结果）</button>
    {error && <p role="alert">{error}</p>}
    <ul>{assets.slice(page * 12, page * 12 + 12).map(asset => <li key={asset.id}>
      <button className="button" disabled={!asset.available} onClick={() => { setError(''); setSelected(asset); }}>{asset.label.split(/[\\/]/).at(-1)} · {asset.mime} · {Math.ceil(asset.size / 1024)} KB{!asset.available && '（未上传）'}</button>
    </li>)}</ul>
    {assets.length > 12 && <div><button disabled={!page} onClick={() => setPage(page - 1)}>上一页附件</button> {page + 1} / {Math.ceil(assets.length / 12)} <button disabled={(page + 1) * 12 >= assets.length} onClick={() => setPage(page + 1)}>下一页附件</button></div>}
    {selected && <div><h4>{selected.label.split(/[\\/]/).at(-1)}</h4>{url ? <>
      <a href={url} download={selected.label.split(/[\\/]/).at(-1) || 'artifact'}>下载选中附件</a>
      {['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/avif'].includes(selected.mime) && <img src={url} alt="归档附件预览" style={{ maxWidth: '100%', maxHeight: 600, objectFit: 'contain' }} />}
      {text && <pre style={{ maxHeight: 400, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text}</pre>}
    </> : <p>正在读取附件…</p>}</div>}
  </section>;
}
