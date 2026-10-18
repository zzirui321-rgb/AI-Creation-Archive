import { useEffect, useRef, useState } from 'react';
import { Box, LoaderCircle, RotateCcw } from 'lucide-react';
import { platform, isRemote } from './platform';
import type { WorkspaceProject } from './workspace';
import type { ModuleArtifact } from './workflow';
import ModelViewer from './ModelViewer';
import './three-d.css';

export type RodinJob = { id: string; state: string; error?: string; recipe: Record<string, string>; artifact?: ModuleArtifact; created_at: number };
const active = (job?: RodinJob) => !!job && ['queued', 'submitting', 'running', 'downloading'].includes(job.state);
async function request(path: string, init?: RequestInit) {
  const response = await platform.authorizedFetch('/api/3d' + path, init);
  const value = await response.json();
  if (!response.ok) throw new Error(value.message || '3D 请求失败');
  return value;
}
export default function ThreeDPanel({ project, nodeId, artifact, beforeGenerate, onSave, mode = 'generate', initialPrompt = '', onFork }: {
  project: WorkspaceProject; nodeId: string; artifact: ModuleArtifact;
  beforeGenerate: () => Promise<unknown>; onSave: (job: RodinJob) => Promise<void>;
  mode?: 'generate' | 'regenerate'; initialPrompt?: string; onFork?: () => void;
}) {
  const [configured, setConfigured] = useState(false), [tier, setTier] = useState('');
  const [prompt, setPrompt] = useState(initialPrompt), [consent, setConsent] = useState(false), [busy, setBusy] = useState(false);
  const [jobs, setJobs] = useState<RodinJob[]>([]), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const pending = useRef<string>('');
  const canGenerate = project.owned && isRemote(project) && artifact.uri.startsWith('/api/');
  const current = jobs.find(job => active(job));
  useEffect(() => {
    let disposed = false;
    void request('/capabilities').then(value => { if (!disposed) { setConfigured(value.configured); setTier(value.tier); } }).catch(reason => { if (!disposed) setError(reason.message); });
    if (project.owned && isRemote(project)) void request(`/repos/${project.id}/jobs`).then(value => { if (!disposed) setJobs(value.jobs.filter((job: RodinJob) => job.recipe.source_node === nodeId && job.recipe.source_artifact === artifact.id)); }).catch(reason => { if (!disposed) setError(reason.message); });
    return () => { disposed = true; };
  }, [project.id, nodeId, artifact.id]);
  useEffect(() => {
    if (!current) return;
    let disposed = false; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next: RodinJob = await request(`/jobs/${current!.id}`);
        if (disposed) return;
        setJobs(values => values.map(value => value.id === next.id ? next : value));
        if (active(next)) timer = setTimeout(poll, 3000);
        else if (next.state === 'succeeded') setNotice('3D 已生成并保存在后端。点击下方保存到图谱，可随作品发布和 Fork。');
      } catch (reason) { if (!disposed) { setError((reason as Error).message); timer = setTimeout(poll, 8000); } }
    }
    timer = setTimeout(poll, 1200);
    return () => { disposed = true; clearTimeout(timer); };
  }, [current?.id]);
  async function generate() {
    if (busy || current || !consent) return;
    setBusy(true); setError(''); setNotice('');
    try {
      if (!pending.current) {
        await beforeGenerate();
        const graph = await platform.graph(project.id, { branch: project.branch ?? 'main' });
        pending.current = JSON.stringify({ request_id: crypto.randomUUID(), expected_head: graph.commit.id, branch: project.branch ?? 'main',
          node_id: nodeId, artifact_id: artifact.id, source_uri: artifact.uri, prompt, consent });
      }
      const job: RodinJob = await request(`/repos/${project.id}/jobs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: pending.current });
      setJobs(values => [job, ...values.filter(value => value.id !== job.id)]); pending.current = ''; setConsent(false);
    } catch (reason) { setError((reason as Error).message + '；重新点击会查询同一请求，不会重新提交收费任务。'); }
    finally { setBusy(false); }
  }
  return <section className="rodin-panel" aria-label="图片生成3D模块"><header><Box size={20} /><div><strong>把这张图，变成可旋转的 3D</strong><small>Hyper3D Rodin · {tier || '基础档'} · PBR · GLB</small></div></header>
    <p>基于当前选中的实际图片建模。先 Fork、改服装颜色或表情并确认图片，再生成不同版本；原图会保留。</p>
    {mode === 'regenerate' && <p>沿用此模型的来源图片和建模描述，生成一个新版本，已有模型会保留。</p>}
    {!project.owned ? <><p>先 Fork 到自己的创作空间，再确认生成。</p>{onFork && <button className="button primary" onClick={onFork}>Fork 后{mode === 'regenerate' ? '重新生成 3D' : '生成 3D'}</button>}</> : !canGenerate ? <p>请先将图片与档案上传保存到平台；示意封面和本地路径不能提交建模。</p> : <>
      {!configured && <p role="status">后端尚未配置 Hyper3D Key。</p>}
      <label className="form-field">建模补充描述（可选）<textarea rows={2} maxLength={2000} value={prompt} disabled={busy || !!current} onChange={event => { setPrompt(event.target.value); if (!pending.current) setError(''); }} placeholder="保留人物面部、衣服和姿态；生成独立完整模型。" /></label>
      <label className="rodin-consent"><input type="checkbox" checked={consent} disabled={busy || !!current} onChange={event => setConsent(event.target.checked)} />同意将这张图片发送至 Hyper3D，并消耗一次基础档生成额度（具体费用以账号为准）</label>
      <button className="button primary" disabled={!configured || !consent || busy || !!current} onClick={() => void generate()}>{busy || current ? <LoaderCircle size={15} className="spin" /> : mode === 'regenerate' ? <RotateCcw size={15} /> : <Box size={15} />}{current ? '3D 建模中，可稍后返回查看' : busy ? '正在处理…' : pending.current ? '查询同一生成请求' : mode === 'regenerate' ? '确认重新生成 3D' : '确认生成 3D'}</button>
    </>}{error && <p role="alert" className="inline-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {jobs.map(job => <article key={job.id} className="rodin-result"><strong>{job.state === 'succeeded' ? '3D 模型已生成' : active(job) ? 'Hyper3D 正在处理' : job.state === 'pending' ? '等待继续查询' : job.state === 'unknown' ? '提交结果待人工确认' : '生成未完成'}</strong><small>{new Date(job.created_at * 1000).toLocaleString('zh-CN')}</small>{job.error && <p>{job.error}</p>}{job.artifact && <><ModelViewer uri={job.artifact.uri} /><button className="button" disabled={busy} onClick={async () => { setBusy(true); setError(''); try { await onSave(job); setNotice('已保存为独立 3D 节点，可从图谱打开、发布和 Fork。'); } catch (reason) { setError('模型已生成，保存失败：' + (reason as Error).message + '。可再次保存，不会重新计费生成。'); } finally { setBusy(false); } }}>保存模型到创作图谱</button></>}{['pending', 'running', 'downloading'].includes(job.state) && !current && <button className="button" onClick={async () => { try { await request(`/jobs/${job.id}/resume`, { method: 'POST' }); setJobs(values => values.map(value => value.id === job.id ? { ...value, state: 'running', error: undefined } : value)); } catch (reason) { setError((reason as Error).message); } }}>继续查询原任务（不重新生成）</button>}</article>)}
  </section>;
}
