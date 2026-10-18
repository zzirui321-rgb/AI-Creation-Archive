import { useEffect, useRef, useState } from 'react';
import { platform } from './platform';
import type { WorkflowModule } from './workflow';
import type { ModulePrompt, ModulePage } from './semantic-graph.generated';
import './semantic-module-content.css';

type Image = { id: string; mime: string; label: string; available: boolean };
type Materials = { prompts: ModulePrompt[]; pages: ModulePage[]; artifacts: Image[]; notice?: string };
const labels = { recorded: '原始提示词', consolidated: 'AI 整合提示词 · 待审核', suggested: 'AI 扩展建议 · 非原始记录', edited: '我的提示词修改' };

function Prompts({ prompts }: { prompts: ModulePrompt[] }) {
  return <>{(['edited', 'consolidated', 'recorded', 'suggested'] as const).map(kind => {
    const items = prompts.filter(item => item.kind === kind);
    return !!items.length && <details className={`semantic-prompts prompt-${kind}`} key={kind} open><summary>{labels[kind]} · {items.length}</summary>{items.map((item, index) => <div key={index}><pre>{item.text}</pre><details><summary>查看提示词来源</summary><small>{item.source_pointer || '依据已上传材料整理的候选内容'}<br />{item.evidence_ids.join(' · ')}</small></details></div>)}</details>;
  })}</>;
}

function SourceImage({ image, module }: { image: Image; module: WorkflowModule }) {
  const [url, setUrl] = useState(''), [failed, setFailed] = useState(false), [visible, setVisible] = useState(false);
  const figure = useRef<HTMLElement | null>(null);
  useEffect(() => { const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); } }, { rootMargin: '250px' }); if (figure.current) observer.observe(figure.current); return () => observer.disconnect(); }, []);
  useEffect(() => {
    setUrl(''); setFailed(false);
    if (!visible || !image.available || !module.artifactBase) return;
    const controller = new AbortController(); let blobUrl = '';
    const path = module.artifactBase.replace('{id}', encodeURIComponent(image.id));
    void platform.authorizedFetch(path, { signal: controller.signal }).then(async response => {
      if (!response.ok || !['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/avif'].includes(response.headers.get('content-type')?.split(';')[0] || '')) throw new Error('图片不可用');
      const blob = await response.blob();
      if (!controller.signal.aborted) { blobUrl = URL.createObjectURL(blob); setUrl(blobUrl); }
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => { controller.abort(); if (blobUrl) URL.revokeObjectURL(blobUrl); };
  }, [image.id, image.available, module.artifactBase, visible]);
  return <figure ref={figure} className="semantic-source-image">{url ? <a href={url} target="_blank" rel="noopener noreferrer"><img src={url} alt={image.label} loading="lazy" /></a> : <p>{!image.available || failed ? '图片未上传或暂不可读取。请上传包含图片的 ZIP / 便携 JSON。' : '正在读取原始图片…'}</p>}<figcaption>{image.label}</figcaption></figure>;
}

function PromptEditor({ prompts, evidence, label, onChange }: { prompts: ModulePrompt[]; evidence: string[]; label: string; onChange: (prompts: ModulePrompt[]) => void }) {
  const value = prompts.filter(p => p.kind === 'edited').map(p => p.text).join('\n\n');
  const base = prompts.filter(p => p.kind === 'consolidated').map(p => p.text).join('\n\n') || prompts.filter(p => p.kind === 'recorded').map(p => p.text).join('\n\n');
  const replace = (text: string) => onChange([...prompts.filter(p => p.kind !== 'edited'), ...(text.trim() ? [{ text, kind: 'edited' as const, evidence_ids: evidence, source_pointer: null }] : [])]);
  return <label className="form-field">{label}<textarea aria-label={label} rows={6} value={value} placeholder="填写修改后的提示词，原始记录会保留" onChange={e => replace(e.target.value)} /><button type="button" className="button" onClick={() => replace(base)}>复制原文 / AI 整合内容开始编辑</button><small>修改随草稿保存为仓库新版本；清空可移除我的修改。</small></label>;
}

export default function SemanticModuleContent({ module, editing = false, onChange }: { module: WorkflowModule; editing?: boolean; onChange?: (content: Pick<WorkflowModule, 'prompts' | 'pages'>) => void }) {
  const [data, setData] = useState<Materials>({ prompts: module.prompts ?? [], pages: module.pages ?? [], artifacts: [] });
  const [loading, setLoading] = useState(false), [error, setError] = useState('');
  useEffect(() => {
    setData({ prompts: module.prompts ?? [], pages: module.pages ?? [], artifacts: [] }); setError('');
    if (!module.materialsUrl) return;
    const controller = new AbortController(); setLoading(true);
    void platform.authorizedFetch(module.materialsUrl, { signal: controller.signal }).then(async response => {
      const value = await response.json(); if (!response.ok) throw new Error(value.message || '材料读取失败');
      if (!controller.signal.aborted) setData(value);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [module.id, module.materialsUrl]);
  const prompts = module.prompts?.some(p => p.kind === 'edited') ? module.prompts : data.prompts;
  const pages = data.pages.map(page => module.pages?.find(p => p.id === page.id) ?? page);
  const update = (nextPrompts: ModulePrompt[], nextPages: ModulePage[]) => { setData(current => ({ ...current, prompts: nextPrompts, pages: nextPages })); onChange?.({ prompts: nextPrompts, pages: nextPages }); };
  const pageImages = new Set(pages.flatMap(page => page.artifact_ids));
  const otherImages = data.artifacts.filter(image => !pageImages.has(image.id));
  return <section className="semantic-module-content" aria-label="模块提示词、逐页内容与原图">
    {loading && <p role="status">正在读取模块的原始提示词和图片…</p>}
    {error && <p role="alert">{error}。已保存的提炼内容仍可查看。</p>}
    {data.notice && <p>{data.notice}</p>}
    <Prompts prompts={prompts} />
    {editing && onChange && !loading && <PromptEditor prompts={prompts} evidence={(module.evidence as { id: string }[]).map(item => item.id)} label="编辑模块提示词" onChange={next => update(next, pages)} />}
    {!loading && !data.prompts.length && !data.pages.length && <p>此模块没有可定位的原始提示词；未用猜测内容填充。</p>}
    {!!pages.length && <><h3>逐页内容 · {pages.length} 页</h3><div className="semantic-page-list">{pages.map(page => <article className="semantic-page" key={page.id}>
      <h4>{page.label}</h4><small>{page.summary.origin === 'llm' ? 'AI 页内容摘要 · 待审核' : '源档案页内容'}</small><p>{page.summary.text}</p>
      <Prompts prompts={page.prompts ?? []} />
      {editing && onChange && !loading && <PromptEditor prompts={page.prompts ?? []} evidence={page.evidence_ids} label={`编辑${page.label}提示词`} onChange={next => update(prompts, pages.map(p => p.id === page.id ? { ...p, prompts: next } : p))} />}
      <div className="semantic-image-grid">{page.artifact_ids.map(id => { const image = data.artifacts.find(image => image.id === id); return image ? <SourceImage key={id} image={image} module={module} /> : <p key={id}>此页引用图片 {id} 未随档案上传。</p>; })}</div>
      {!page.artifact_ids.length && <p className="muted">源记录没有明确关联本页图片，未按文件名猜配。</p>}
    </article>)}</div></>}
    {!!otherImages.length && <><h3>模块其他已登记图片</h3><div className="semantic-image-grid">{otherImages.map(image => <SourceImage key={image.id} image={image} module={module} />)}</div></>}
  </section>;
}
