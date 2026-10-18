import { useEffect, useRef, useState } from 'react';
import { platform, loadProject } from './platform';
import { imageProviderConfig, type AppSettings, type ModelCredentials } from './settings';
import type { WorkspaceProject } from './workspace';
import './repository-images.css';

type EditTarget = { node_id: string; module_label: string; original_id: string; artifact_id: string };
type ImageItem = { id: string; label: string; mime: string; node_ids: string[] };
function useImage(path: string) {
  const [url, setUrl] = useState(''), [error, setError] = useState('');
  useEffect(() => {
    setUrl(''); setError(''); if (!path) return;
    const controller = new AbortController(); let objectUrl = '';
    void platform.authorizedFetch(path, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('图片没有随档案上传或暂不可读取');
      const blob = await response.blob(); if (!blob.type.startsWith('image/')) throw new Error('图片格式不可预览');
      if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); }
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path]);
  return { url, error };
}
function Thumbnail({ path, label }: { path: string; label: string }) {
  const { url, error } = useImage(path);
  return url ? <img src={url} alt={label} loading="lazy" /> : <span>{error || '读取缩略图…'}</span>;
}

export default function RepositoryImages({ project, moduleId, settings, credentials, beforeEdit, onSaved, onBusy, initialInstruction = '' }: {
  project: WorkspaceProject; moduleId?: string; settings: AppSettings; credentials: ModelCredentials;
  beforeEdit?: () => Promise<void>; onSaved?: (project: WorkspaceProject) => void; onBusy?: (busy: boolean) => void;
  initialInstruction?: string;
}) {
  const [images, setImages] = useState<ImageItem[]>([]), [commit, setCommit] = useState('');
  const [selected, setSelected] = useState(''), [page, setPage] = useState(0), [instruction, setInstruction] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [selectRegion, setSelectRegion] = useState(false), [region, setRegion] = useState<number[]>();
  const start = useRef<number[] | undefined>(undefined), requestId = useRef(''), pendingBody = useRef('');
  const [propagate, setPropagate] = useState(true), [targets, setTargets] = useState<EditTarget[]>([]);
  const batch = useRef<{ targets: EditTarget[]; index: number; head: string; reference?: string } | undefined>(undefined);
  const base = `/api/repos/${project.id}`;
  useEffect(() => {
    const controller = new AbortController(); setError('');
    void platform.authorizedFetch(`${base}/images?branch=${encodeURIComponent(project.branch ?? 'main')}`, { signal: controller.signal }).then(async response => {
      const data = await response.json(); if (!response.ok) throw new Error(data.message || '图片目录读取失败');
      if (controller.signal.aborted) return;
      const list: ImageItem[] = data.images;
      setImages(list); setCommit(data.commit_id); setSelected(current => !moduleId && data.latest_result_id ? data.latest_result_id : list.some(item => item.id === current) ? current : list.find(item => moduleId && item.node_ids.includes(moduleId))?.id ?? list[0]?.id ?? '');
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [base, project.branch, project.updatedAt, moduleId]);
  useEffect(() => {
    if (!moduleId || !project.owned) return;
    const controller = new AbortController();
    void platform.authorizedFetch(`${base}/modules/${encodeURIComponent(moduleId)}/image-edit-plan?branch=${encodeURIComponent(project.branch ?? 'main')}`, { signal: controller.signal }).then(async response => {
      const data = await response.json(); if (!response.ok) throw new Error(data.message || '关联图片读取失败');
      if (!controller.signal.aborted) setTargets(data.targets);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [base, moduleId, project.branch, project.updatedAt, project.owned]);
  const imagePath = (id: string, thumbnail = false) => `${base}/images/${encodeURIComponent(id)}?commit_id=${commit}${thumbnail ? '&thumbnail=true' : ''}`;
  const { url, error: imageError } = useImage(selected && commit ? imagePath(selected) : '');
  useEffect(() => { setRegion(undefined); requestId.current = ''; pendingBody.current = ''; }, [selected, selectRegion]);
  useEffect(() => { requestId.current = ''; pendingBody.current = ''; batch.current = undefined; }, [instruction, propagate]);
  useEffect(() => { if (initialInstruction.trim()) setInstruction(initialInstruction.slice(0, 4000)); }, [initialInstruction]);
  async function generate() {
    if (!moduleId || busy || !selected || !instruction.trim()) return;
    setBusy(true); onBusy?.(true); setError(''); setNotice('');
    try {
      const config = imageProviderConfig(settings, credentials);
      if (!config || config.provider === 'local_fallback') throw new Error('请先在模型与 API 设置中填写支持原图编辑的模型和 API Key。');
      await beforeEdit?.();
      if (!batch.current) {
        const latest = await platform.graph(project.id, { branch: project.branch ?? 'main' });
        let work: EditTarget[] = [{ node_id: moduleId, module_label: '', original_id: selected, artifact_id: selected }];
        if (propagate) {
          const planResponse = await platform.authorizedFetch(`${base}/modules/${encodeURIComponent(moduleId)}/image-edit-plan?branch=${encodeURIComponent(project.branch ?? 'main')}`);
          const plan = await planResponse.json(); if (!planResponse.ok) throw new Error(plan.message || '关联图片读取失败');
          if (plan.commit_id !== latest.commit.id) throw new Error('仓库版本已变化，请重新开始。');
          work = plan.targets; setTargets(work);
          if (!work.length) throw new Error('当前模块及其下游没有登记的图片，请先补全 Graph 图片关联。');
        }
        batch.current = { targets: work, index: 0, head: latest.commit.id };
      }
      const job = batch.current;
      while (job.index < job.targets.length) {
        const target = job.targets[job.index];
        setNotice(`正在修改 ${job.index + 1} / ${job.targets.length}：${target.module_label}。已完成图片已保存，请保持页面打开。`);
        const identifier = requestId.current ||= '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));
        const body = pendingBody.current ||= JSON.stringify({ branch: project.branch ?? 'main', expected_head: job.head, request_id: identifier,
          original_artifact_id: target.artifact_id, instruction, region: !propagate && selectRegion ? region ?? null : null,
          reference_artifact_id: propagate && target.node_id !== moduleId ? job.reference ?? null : null,
          image_config: { ...config, timeout_seconds: Math.max(config.timeout_seconds || 10, 120), allow_fallback: false } });
        const response = await platform.authorizedFetch(`${base}/modules/${encodeURIComponent(target.node_id)}/image-edits`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
        });
        const result = await response.json(); if (!response.ok) throw new Error(result.message || result.detail || '原图修改失败');
        job.head = result.commit_id; job.index++;
        if (target.node_id === moduleId && !job.reference) job.reference = result.artifact_id;
        setCompleted(true);
        setImages(current => [...current.filter(image => image.id !== target.artifact_id && image.id !== result.artifact_id), {
          id: result.artifact_id, label: '修改结果 · ' + target.module_label, mime: 'image/png', node_ids: [target.node_id],
        }]);
        setCommit(result.commit_id); requestId.current = ''; pendingBody.current = '';
        if (job.index === job.targets.length) setSelected(result.artifact_id);
      }
      setNotice(`已更新并保存 ${job.targets.length} 张关联图片；未关联的图片保留原样。`);
      batch.current = undefined;
      try {
        const { repository } = await platform.repository(project.id);
        const next = await loadProject(repository, project.branch ?? 'main');
        onSaved?.(next); window.dispatchEvent(new CustomEvent('aica:repository-saved', { detail: next }));
      } catch { setNotice('修改图片已生成并保存，结果已在下方显示；仓库列表刷新暂未完成。请勿重复生成。'); }
    } catch (reason) { setError((reason as Error).message + (batch.current ? `（已保存 ${batch.current.index} / ${batch.current.targets.length} 张；点击继续可从未完成处恢复，不会重做已保存图片。）` : '')); }
    finally { setBusy(false); onBusy?.(false); }
  }
  return <section className="repository-images surface-section" aria-label="原图预览与增量修改">
    <h3>{moduleId ? '描述改动，基于原图继续创作' : '档案图片预览'} <small>{images.length} 张</small></h3>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!images.length && !error && <p>正在读取图片目录；大档案首次准备可能需要几秒。</p>}
    <div className="repository-image-grid">{images.slice(page * 8, page * 8 + 8).map(image => <button key={image.id} disabled={busy || !!batch.current} aria-pressed={selected === image.id} onClick={() => setSelected(image.id)} title={image.label}>
      <Thumbnail path={imagePath(image.id, true)} label={image.label} /><span>{image.label.split('/').at(-1)}</span>
    </button>)}</div>
    {images.length > 8 && <div className="repository-image-pages"><button disabled={!page || busy} onClick={() => setPage(page - 1)}>上一页</button><span>{page + 1} / {Math.ceil(images.length / 8)}</span><button disabled={(page + 1) * 8 >= images.length || busy} onClick={() => setPage(page + 1)}>下一页</button></div>}
    {selected && <><p>{completed ? '已生成的修改结果：' : '当前图片：'}{images.find(image => image.id === selected)?.label}</p>{url ? <><div className={`repository-image-canvas ${selectRegion ? 'select-region' : ''}`}
      onPointerDown={event => { if (!selectRegion || busy) return; const box = event.currentTarget.getBoundingClientRect(); start.current = [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height]; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => { if (!start.current) return; const box = event.currentTarget.getBoundingClientRect(); const end = [Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), Math.max(0, Math.min(1, (event.clientY - box.top) / box.height))]; setRegion([Math.min(start.current[0], end[0]), Math.min(start.current[1], end[1]), Math.abs(start.current[0] - end[0]), Math.abs(start.current[1] - end[1])]); }}
      onPointerUp={() => { start.current = undefined; requestId.current = ''; pendingBody.current = ''; }} onPointerCancel={() => { start.current = undefined; }}>
      <img src={url} alt="选中的原图" draggable={false} />{selectRegion && region && <div className="edit-region" style={{ left: `${region[0] * 100}%`, top: `${region[1] * 100}%`, width: `${region[2] * 100}%`, height: `${region[3] * 100}%` }} />}
    </div><a href={url} target="_blank" rel="noopener noreferrer">打开原尺寸图片</a></> : <p>{imageError || '正在读取原图…'}</p>}</>}
    {moduleId && project.owned && <><label className="form-field">你想改什么？<textarea rows={3} maxLength={4000} value={instruction} disabled={busy} placeholder="例如：把主角的外套改成红色，其他内容保持原样。" onChange={event => setInstruction(event.target.value)} /></label>
      <label><input type="checkbox" checked={propagate} disabled={busy} onChange={event => { setPropagate(event.target.checked); setSelectRegion(false); }} /> 同步当前模块及下游关联图片（{targets.length} 张，每张会调用一次绘图服务）</label>
      {propagate && <p>沿 Graph 依赖自动更新；优先修改当前模块，再以修改后的设定图参考同步后续页面。{[...new Set(targets.map(target => target.module_label))].join(' → ')}</p>}
      {!propagate && <label><input type="checkbox" checked={selectRegion} disabled={busy} onChange={event => setSelectRegion(event.target.checked)} /> 框选局部修改区域（可选）</label>}
      <p>{propagate ? '每张页面基于自己的原图修改；模型负责识别同一人物，完成后请检查人物和文字是否保持一致。' : selectRegion ? '在原图上拖出矩形，框外像素将按原图精确保留。' : '模型参考原图理解改动；如需精确保留区域外像素，请框选区域。'} 千问旧 qwen-image 配置兼容使用 qwen-image-2.0 进行原图编辑。</p>
      <button className="button primary" disabled={busy || !url || !instruction.trim() || selectRegion && (!region || region[2] < .001 || region[3] < .001)} onClick={() => void generate()}>{busy ? '正在同步关联图片…' : batch.current ? '继续未完成的修改' : propagate ? `修改并同步 ${targets.length} 张关联图片` : '修改这张图并保存新版本'}</button>
    </>}
  </section>;
}
