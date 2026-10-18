import { platform } from './platform';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Play, GitBranch, Upload, RefreshCw, Square } from 'lucide-react';
import { ArchiveClient, variableFields, operationValue, loadArchiveAssets, primaryArtifacts, ArtifactRenderer, ArchiveDiffViewer, ProvenanceAndLinksBar, latestRuns, type ArchiveSnapshot, type RuntimePlan, type VariableOperation, type RuntimeJob } from '@aica/artifact-renderers';
import '@aica/artifact-renderers/styles.css';
import GraphExplorer from './GraphExplorer';
import { parseWorkflow } from './workflow';
import './executable.css';
import SemanticReview from './SemanticReview';
import { imageProviderConfig, type AppSettings, type ModelCredentials } from './settings';

const api = new ArchiveClient(platform.authorizedFetch, '/runtime-api');
type Assets = Awaited<ReturnType<typeof loadArchiveAssets>>;

export default function ExecutableWorkspace({ projectId = '', settings, credentials }: { projectId?: string; settings: AppSettings; credentials: ModelCredentials }) {
  const imageConfig = useMemo(() => imageProviderConfig(settings, credentials), [settings, credentials]);
  const [projects, setProjects] = useState<{ id: string; title: string }[]>([]);
  const [snapshot, setSnapshot] = useState<ArchiveSnapshot>();
  const [plan, setPlan] = useState<RuntimePlan>();
  const [golden, setGolden] = useState<string[]>([]);
  const [assets, setAssets] = useState<Assets>();
  const [assetSnapshot, setAssetSnapshot] = useState('');
  const [original, setOriginal] = useState<ArchiveSnapshot>();
  const [originalAssets, setOriginalAssets] = useState<Assets>();
  const [originalAssetId, setOriginalAssetId] = useState('');
  const [edits, setEdits] = useState<Record<string, { mode: VariableOperation['mode']; text: string }>>({});
  const [stage, setStage] = useState<'draft' | 'final'>('draft');
  const [selected, setSelected] = useState<string | null>(null);
  const [compare, setCompare] = useState(false);
  const [executeConsent, setExecuteConsent] = useState(false);
  const [recordConsent, setRecordConsent] = useState(false);
  const [job, setJob] = useState<RuntimeJob>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [epoch, setEpoch] = useState(0);
  const request = useRef<AbortController | undefined>(undefined);
  const archive = assetSnapshot === snapshot?.archive_id ? assets?.archive : undefined;
  const comparison = originalAssetId === original?.archive_id ? originalAssets : undefined;
  const running = !!job && ['queued', 'running'].includes(job.state);
  const workflow = useMemo(() => snapshot ? { ...parseWorkflow(JSON.stringify(snapshot.archive)).workflow, goldenPath: golden } : undefined, [snapshot, golden]);
  useEffect(() => {
    const abort = new AbortController();
    api.projects(abort.signal).then(result => { if (!abort.signal.aborted) setProjects(result.projects); }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    return () => abort.abort();
  }, [epoch, projectId]);
  useEffect(() => {
    const abort = new AbortController();
    setSnapshot(undefined); setAssets(undefined); setOriginal(undefined); setOriginalAssets(undefined); setEdits({}); setJob(undefined); setCompare(false); setSelected(null); setPlan(undefined); setError('');
    if (projectId) api.snapshot(projectId, undefined, abort.signal).then(value => { if (!abort.signal.aborted) setSnapshot(value); }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    return () => { abort.abort(); request.current?.abort(); };
  }, [projectId, epoch]);
  useEffect(() => {
    if (!snapshot || !projectId) return;
    const abort = new AbortController(); let loaded: Assets | undefined;
    setAssets(undefined); setPlan(undefined);
    loadArchiveAssets(api, projectId, snapshot.archive, abort.signal).then(value => { loaded = value; if (abort.signal.aborted) value.dispose(); else { setAssets(value); setAssetSnapshot(snapshot.archive_id); } }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    api.plan(projectId, snapshot.archive_id, stage, abort.signal, imageConfig).then(value => { if (!abort.signal.aborted) setPlan(value); }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    api.goldenPath(projectId, snapshot.archive_id, abort.signal).then(value => { if (!abort.signal.aborted) setGolden(value.node_ids); }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    return () => { abort.abort(); loaded?.dispose(); };
  }, [snapshot, projectId, stage, imageConfig]);
  useEffect(() => {
    if (!original) return;
    const abort = new AbortController(); let loaded: Assets | undefined;
    loadArchiveAssets(api, projectId, original.archive, abort.signal).then(value => { loaded = value; if (abort.signal.aborted) value.dispose(); else { setOriginalAssets(value); setOriginalAssetId(original.archive_id); } }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    return () => { abort.abort(); loaded?.dispose(); };
  }, [original, projectId]);
  async function act(action: (signal: AbortSignal) => Promise<void>) {
    if (busy || running) return;
    const abort = new AbortController(); request.current = abort; setBusy(true); setError('');
    try { await action(abort.signal); } catch (reason) { if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : '请求失败'); }
    finally { if (request.current === abort) { request.current = undefined; setBusy(false); } }
  }
  async function run(signal: AbortSignal) {
    if (!snapshot || !executeConsent || !recordConsent) return;
    const needsImages = snapshot.archive.graph.nodes.some(node => ['image_gen', 'image-provider'].includes(node.execution?.tool || ''));
    if (needsImages && !imageConfig) throw new Error('请先在模型设置中填写生图 API Key，或明确选择 Local Fallback 离线预览。');
    const accepted = await api.execute(projectId, snapshot, stage, signal, imageConfig);
    for (;;) {
      const { execution } = await api.job(projectId, accepted.job_id, signal);
      if (signal.aborted) return;
      if (!['queued', 'running'].includes(execution.state)) {
        const completed = await api.snapshot(projectId, execution.result_archive_id, signal);
        if (signal.aborted) return;
        setSnapshot(completed); setJob(execution);
        if (execution.state !== 'succeeded') throw new Error(execution.result?.message || `Execution ${execution.state}`);
        setMessage('本次执行已完成'); return;
      }
      setJob(execution);
      await new Promise<void>(resolve => { const finish = () => { signal.removeEventListener('abort', stop); resolve(); }; const timer = setTimeout(finish, 350); const stop = () => { clearTimeout(timer); finish(); }; signal.addEventListener('abort', stop, { once: true }); });
    }
  }
  const runs = archive ? latestRuns(archive, stage) : new Map();
  const decisions = Object.fromEntries(Object.entries(plan?.decisions || {}).map(([id, value]) => [id, (runs.get(id)?.cache?.decision || value).replaceAll('_', ' ')]));
  const selectedNode = archive?.graph.nodes.find(node => node.id === selected);
  const selectedRun = selected ? runs.get(selected) : undefined;
  const outputs = archive ? (selectedRun?.state === 'succeeded' ? archive.artifacts.filter(item => Object.values(selectedRun.outputs).includes(item.id)) : primaryArtifacts(archive, stage)) : [];
  return <main className="exec-workspace"><header><div><a href="#/"><ArrowLeft size={14} />返回发现</a><h1>{archive?.metadata.title || '可执行创作档案'}</h1></div><label className="button"><Upload size={14} />导入 .aica / JSON / ZIP<input aria-label="导入可执行档案" type="file" accept=".json,.aica,.zip" disabled={busy || running} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void act(async signal => { const id = await api.upload(file, signal); window.location.hash = `/execute/${id}`; }); }} /></label><button title="刷新档案" aria-label="刷新档案" className="button" disabled={busy || running} onClick={() => setEpoch(value => value + 1)}><RefreshCw size={14} /></button></header>
    <aside className="exec-projects">{projects.map(project => <a key={project.id} href={`#/execute/${project.id}`} aria-current={projectId === project.id ? 'page' : undefined}>{project.title}</a>)}</aside>
    <p className="exec-provider" data-provider={imageConfig?.provider || 'unconfigured'}>{imageConfig ? `${imageConfig.provider} · ${imageConfig.model || 'Offline'} · ${imageConfig.timeout_seconds || 10}s` : '尚未配置本次生图 API Key'} <a className="button" href="#/settings">模型与 API 设置</a></p>
    {error && <p role="alert" className="inline-error">{error}</p>}{message && <p role="status">{message}</p>}
    {snapshot && <div className="exec-layout"><section className="exec-main">{workflow && <GraphExplorer workflow={workflow} selected={selected} onSelect={setSelected} decisions={decisions} runningId={running ? selected || undefined : undefined} activeWord="" onRemix={() => document.querySelector('.exec-variables')?.scrollIntoView()} />}
      <div className="exec-stage" role="group" aria-label="产物阶段">{(['draft', 'final'] as const).map(mode => <button className="button" key={mode} aria-pressed={stage === mode} disabled={busy || running} onClick={() => { setStage(mode); setSelected(null); }}>{mode === 'draft' ? 'Stage 1 · Draft' : 'Stage 2 · Final'}</button>)}<label><input aria-label="Original / Fork" type="checkbox" checked={compare} disabled={!comparison} onChange={event => setCompare(event.target.checked)} />Original / Fork</label><button className="button" onClick={() => setSelected(null)}>全部最终产物</button></div>
      {archive && assets && <><ProvenanceAndLinksBar archive={archive} />{compare && comparison ? <ArchiveDiffViewer originalArchive={comparison.archive} forkedArchive={archive} previewMode={stage} resolveUri={item => assets.resolveUri(item) || comparison.resolveUri(item)} /> : <div className="exec-artifacts">{outputs.filter(item => item.mime_type !== 'application/vnd.aica.preview+json').map(item => <div key={item.id}><ArtifactRenderer artifact={item} archive={archive} previewMode={stage} resolveUri={assets.resolveUri} /><p className="exec-artifact-hash">Artifact {item.id}<br />SHA256 {item.sha256}</p></div>)}{!outputs.length && <p>此修订尚无该阶段产物。</p>}</div>}</>}
      {selectedNode && <details open className="exec-node-detail"><summary>{selectedNode.label} / {selectedNode.id}</summary><pre>{JSON.stringify({ execution: selectedNode.execution, provenance: selectedNode.provenance, run: selectedRun }, null, 2)}</pre></details>}
      <details className="exec-history"><summary>所有节点与试错分支</summary>{snapshot.archive.graph.nodes.map(node => <div key={node.id}><strong>{node.label}</strong> · {node.provenance.mode} · {snapshot.archive.graph.branches.find(branch => branch.id === node.branch_id)?.state}<p>{snapshot.archive.graph.branches.find(branch => branch.id === node.branch_id)?.reason}</p><pre>{JSON.stringify(node.provenance.evidence)}</pre></div>)}</details>
    </section><aside className="exec-variables"><h2>Fork Variables</h2>{variableFields(snapshot.archive).map(({ id, variable, question, modes }) => { const edit = edits[id] || { mode: 'keep' as const, text: variable.type === 'string' ? String(variable.value) : JSON.stringify(variable.value) }; return <fieldset key={id} disabled={busy || running}><legend>{question}</legend><label className="exec-variable-id">{id}</label><div role="group" aria-label={`${id} 修改方式`}>{modes.map(mode => <button className="button" aria-pressed={edit.mode === mode} key={mode} onClick={() => setEdits(values => ({ ...values, [id]: { ...edit, mode, text: mode === 'append' ? variable.type === 'array' ? '[]' : '' : edit.text } }))}>{mode}</button>)}</div><textarea aria-label={`${id} 变量值`} disabled={edit.mode === 'keep'} value={edit.text} onChange={event => setEdits(values => ({ ...values, [id]: { ...edit, text: event.target.value } }))} /></fieldset>; })}
      <button className="button full" disabled={busy || running || !Object.values(edits).some(value => value.mode !== 'keep')} onClick={() => void act(async signal => { const operations: VariableOperation[] = variableFields(snapshot.archive).map(({ id, variable }) => { const edit = edits[id]; const mode = edit?.mode || 'keep'; return { variable_id: id, mode, ...(mode === 'keep' ? {} : { value: operationValue(variable, mode, edit.text) }) }; }); const result = await api.fork(projectId, snapshot, operations, signal); setOriginal(snapshot); setCompare(false); setEdits({}); setSnapshot(await api.snapshot(projectId, result.archive_id, signal)); setMessage(`Fork 完成：${result.dirty_node_ids.length} 个脏节点`); })}><GitBranch size={14} />创建 Fork</button>
      <h2>下次执行计划</h2><ul className="exec-plan">{plan?.node_ids.map(id => <li key={id}>{id}<span className={`aica-state state-${plan.decisions?.[id] || 'unknown'}`}>{plan.decisions?.[id]?.replaceAll('_', ' ') || '待检查'}</span></li>)}</ul>{plan?.blockers.map((blocker, index) => <p key={index} className="inline-error">{blocker.node_id}: {blocker.message}</p>)}
      <label><input type="checkbox" aria-label="允许执行工作流" checked={executeConsent} disabled={busy || running} onChange={event => setExecuteConsent(event.target.checked)} />允许执行工作流</label><label><input type="checkbox" aria-label="记录执行和产物" checked={recordConsent} disabled={busy || running} onChange={event => setRecordConsent(event.target.checked)} />记录执行和产物</label>
      <button className="button primary full" disabled={busy || running || !plan?.executable || !executeConsent || !recordConsent || Object.values(edits).some(edit => edit.mode !== 'keep')} onClick={() => void act(run)}><Play size={14} />执行 {stage === 'draft' ? 'Draft' : 'Final'}</button>
      {job && <p data-job-state={job.state} data-job-id={job.id} role="status">{job.state}</p>}{running && <button className="button" onClick={() => void api.cancel(projectId, job!.id).catch(reason => setError(reason.message))}><Square size={14} />取消执行</button>}
    </aside></div>}
    <SemanticReview projectId={projectId} archiveId={snapshot?.archive_id} title={snapshot?.archive.metadata.title} />
  </main>;
}
