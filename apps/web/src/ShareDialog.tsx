import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, Check, Copy, Image, Link, LoaderCircle, Sparkles, X } from 'lucide-react';
import type { CommunityProject } from './community';
import type { Values } from './demo';
import ProjectArtwork from './ProjectArtwork';
import SegmentedControl from './SegmentedControl';
import { createLocalShare } from './local-sharing';
import type { WorkspaceProject } from './workspace';
import { createShareCard } from './share-card';

export default function ShareDialog({ project, previewValues, link, localRemix, onClose }: { project: CommunityProject; previewValues?: Values; link: string; localRemix: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const artwork = useRef<HTMLDivElement>(null);
  const [shareLink, setShareLink] = useState(link);
  const [tab, setTab] = useState('link');
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [cardUrl, setCardUrl] = useState('');
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const generation = useRef(0);
  useEffect(() => { dialog.current?.showModal(); return () => { generation.current++; if (copyTimer.current) clearTimeout(copyTimer.current); }; }, []);
  useEffect(() => () => { if (cardUrl) URL.revokeObjectURL(cardUrl); }, [cardUrl]);
  async function copy() {
    setBusy(true);
    try { const nextLink = localRemix && shareLink === link ? await createLocalShare(project as WorkspaceProject) : shareLink; setShareLink(nextLink); await navigator.clipboard.writeText(nextLink); setCopied(true); setError(''); if (copyTimer.current) clearTimeout(copyTimer.current); copyTimer.current = setTimeout(() => setCopied(false), 2200); }
    catch (error) { setError((error as Error).message || '自动复制不可用，请选中上方链接后手动复制。'); } finally { setBusy(false); }
  }
  async function generate() {
    if (busy) return;
    const svg = artwork.current?.querySelector<SVGSVGElement | HTMLImageElement>('svg, img'); if (!svg) return;
    const token = ++generation.current; setBusy(true); setError('');
    try {
      const blob = await createShareCard(project, svg, shareLink);
      if (token === generation.current) setCardUrl(URL.createObjectURL(blob));
    } catch (err) { if (token === generation.current) setError((err as Error).message); }
    finally { if (token === generation.current) setBusy(false); }
  }
  const isLocalHost = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
  return <dialog ref={dialog} className="share-dialog" aria-labelledby="share-heading" onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === dialog.current) onClose(); }}>
    <div className="share-dialog-top"><div><span>PASS THE INSPIRATION ON</span><h2 id="share-heading">让灵感，继续流动。</h2></div><button className="icon-button" aria-label="关闭分享" onClick={onClose}><X size={20} /></button></div>
    <div className="share-project"><div ref={artwork}><ProjectArtwork project={(project as WorkspaceProject).coverArtifact?.mime.startsWith('video/') ? { ...project, coverArtifact: undefined } : project} values={previewValues ?? project.values} /></div><div><strong>{project.title}</strong><span>{project.author} · {project.category}{localRemix ? ' · 本地 Remix' : ''}</span></div></div>
    <SegmentedControl label="作品分享方式" value={tab} onChange={value => { setTab(value); setError(''); }} options={[{ id: 'link', label: '生成链接', icon: <Link size={14} /> }, { id: 'card', label: '生成分享卡片', icon: <Image size={14} /> }]} />
    {tab === 'link' ? <div className="share-link-panel"><label htmlFor="share-link-value">作品链接</label><input id="share-link-value" readOnly value={shareLink} onFocus={e => e.currentTarget.select()} /><button className="button primary full" disabled={busy} onClick={copy}>{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? '链接已复制' : '复制作品链接'}</button><p>{localRemix ? '生成链接后会保存只读分享快照，同一台电脑的其他浏览器可查看并 Fork；仍需本地服务运行。' : isLocalHost ? '当前为本地链接，其他人无法通过它访问你的电脑。公开分享需要部署后的地址。' : '此链接指向当前环境中的作品详情。'}</p></div> : <div className="share-card-panel">{cardUrl ? <a className="share-card-preview" href={cardUrl} target="_blank" rel="noreferrer" aria-label="打开生成的分享卡片"><img src={cardUrl} alt={`${project.title} 的 AiCA 分享卡片`} /></a> : <div className="share-card-placeholder"><span><Sparkles size={25} /></span><strong>把这份创作，装进一张卡片。</strong><p>作品封面、作者署名、工具与链接<br />生成 1080 × 1500 的 PNG 图片。</p></div>}{cardUrl ? <div className="share-card-actions"><button className="button" disabled={busy} onClick={generate}>重新生成</button><a className="button primary" href={cardUrl} download={`aica-${project.id}-share.png`}><ArrowDownToLine size={15} />下载分享卡片</a></div> : <button className="button primary full" disabled={busy} onClick={generate}>{busy ? <LoaderCircle className="spin" size={15} /> : <Image size={15} />}{busy ? '正在生成卡片…' : '生成 PNG 分享卡片'}</button>}<p className="share-card-note">卡片使用已确认的图片结果；尚未确认时使用示意封面。图片在浏览器内生成。</p></div>}
    {error && <p className="share-dialog-error" role="alert">{error}</p>}
  </dialog>;
}
