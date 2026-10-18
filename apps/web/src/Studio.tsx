import { exportAgentWorkflow, isRemote, platform } from './platform';
import ArchiveAssets from './ArchiveAssets';
import ThreeDPanel, { type RodinJob } from './ThreeDPanel';
import { attachModel, generationTarget } from './three-d';
import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, Box, CheckCircle2, ChevronRight, GitBranch, RotateCcw, Save, Upload, X } from 'lucide-react';
import GraphExplorer from './GraphExplorer';
import { adaptAnalysis } from './importing/parser';
import { groupArtifacts, resultFormat } from './result-projection';
import ResultGallery from './ResultGallery';
import IncrementalReuse from './IncrementalReuse';
import PromptWords from './PromptWords';
import PromptEditor from './PromptEditor';
import { creationView, environmentOf, groupFor, phaseLabel } from './creation-view';
import ModulePreviewDock from './ModulePreviewDock';
import PublishDialog from './PublishDialog';
import { confirmPreview, historyOf, rememberPreview, restorePreview } from './preview-history';
import { moduleImpact, displayValue, redact, type ModuleArtifact, type WorkflowModule } from './workflow';
import type { ModulePreview, WorkspaceProject, LocalEdit } from './workspace';
import type { AppSettings, ModelCredentials } from './settings';
import { modulePreviewService, previewKind } from './preview-service';

export default function Studio({ project, settings, credentials, onSave, onFork }: { project: WorkspaceProject; settings: AppSettings; credentials: ModelCredentials; onSave: (project: WorkspaceProject) => Promise<WorkspaceProject>; onFork: (project: WorkspaceProject) => void }) {
  const [draft, setDraft] = useState(() => structuredClone(project)); const [selectedId, setSelectedId] = useState<string | null>(null); const [activeWord, setActiveWord] = useState(''); const [publish, setPublish] = useState(false);
  const [runningId, setRunningId] = useState<string>(); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [publishSuccess, setPublishSuccess] = useState(''); const [savedVersion, setSavedVersion] = useState(() => JSON.stringify(project)); const abort = useRef<AbortController | null>(null); const detail = useRef<HTMLDialogElement>(null);
  const [promptMode, setPromptMode] = useState('override'); const [appendValue, setAppendValue] = useState(''); const [overrideValue, setOverrideValue] = useState('');
  const originalPrompts = useRef(Object.fromEntries(project.workflow.modules.map(n => [n.id, n.prompt])));
  const [detailOpen, setDetailOpen] = useState(false); const [analysisRequested, setAnalysisRequested] = useState(false);
  const [lastLocalEdit, setLastLocalEdit] = useState<{ nodeId: string; edit: LocalEdit }>();
  const [previewMessage, setPreviewMessage] = useState(''); const [useReference, setUseReference] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [threeD, setThreeD] = useState<ReturnType<typeof generationTarget>>();
  const threeDDialog = useRef<HTMLDialogElement>(null);
  const generateTarget = generationTarget(draft, selectedId, 'generate');
  const regenerateTarget = generationTarget(draft, selectedId, 'regenerate');
  useEffect(() => {
    if (!threeD) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = threeDDialog.current;
    dialog?.showModal();
    return () => { dialog?.close(); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [threeD]);
  function openThreeD(mode: 'generate' | 'regenerate', artifact?: ModuleArtifact) {
    const target = generationTarget(draft, selectedId, mode, artifact?.mime.startsWith('image/') ? artifact.id : undefined);
    if (target) { closeDetails(); setThreeD(target); }
  }
  const selected = draft.workflow.modules.find(n => n.id === selectedId);
  const basePrompt = selected ? draft.basePrompts?.[selected.id] ?? originalPrompts.current[selected.id] ?? selected.prompt : '';
  const selectedGroup = selected ? groupFor(draft.workflow, selected.id) : undefined;
  const selectedArtifacts = groupArtifacts(draft.workflow, selectedGroup);
  const selectedEnvironment = selected ? environmentOf(selected) : {};
  const histories = historyOf(draft);
  const shownPreview = selectedId ? (draft.preview?.nodeId === selectedId ? draft.preview : histories.find(value => value.nodeId === selectedId)) : draft.preview;
  const referenceArtifact = selectedArtifacts.find(a => a.mime.startsWith('image/')) ?? selected?.artifacts.find(a => a.mime.startsWith('image/')) ?? draft.baselineArtifact ?? draft.workflow.artifacts?.find(a => a.mime.startsWith('image/'));
  const modifiedIds = draft.workflow.modules.filter(n => (draft.basePrompts && n.prompt !== draft.basePrompts[n.id]) || (draft.baseModels && n.model !== draft.baseModels[n.id])).map(n => n.id);
  const decisions = modifiedIds.length || analysisRequested ? moduleImpact(draft.workflow, modifiedIds) : null;
  const dirty = JSON.stringify(draft) !== savedVersion;
  const latest = useRef({ draft, dirty, autoSave: settings.autoSave }); latest.current = { draft, dirty, autoSave: settings.autoSave };
  useEffect(() => () => { const value = latest.current; if (value.draft.owned && value.dirty && value.autoSave) { void onSave(value.draft).catch(() => {}); } }, [onSave]);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    const job = project.workflow.importSource?.jobId;
    if (!job || project.workflow.creationView?.format === resultFormat) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await platform.authorizedFetch(`/api/creation-parser/jobs/${encodeURIComponent(job)}/result`, { signal: controller.signal });
        if (!response.ok) throw new Error('读取失败');
        const imported = adaptAnalysis(await response.json(), job, window.location.origin);
        if (controller.signal.aborted) return;
        setDraft(current => {
          const previous = new Map(current.workflow.modules.map(n => [n.id, n]));
          const ids = new Set(imported.workflow.modules.map(n => n.id));
          const modules = [...imported.workflow.modules.map(n => previous.has(n.id) ? { ...n, prompt: previous.get(n.id)!.prompt, model: previous.get(n.id)!.model } : n), ...current.workflow.modules.filter(n => !ids.has(n.id))];
          const basePrompts = { ...Object.fromEntries(modules.map(n => [n.id, n.prompt])), ...current.basePrompts };
          const baseModels = { ...Object.fromEntries(modules.map(n => [n.id, n.model])), ...current.baseModels };
          return { ...current, workflow: { ...imported.workflow, modules }, basePrompts, baseModels };
        });
      } catch { if (!controller.signal.aborted) setNotice('暂时无法更新旧档案的结果关联；原始记录和已有修改仍已保留。'); }
    })();
    return () => controller.abort();
  }, [project.id]);

  useEffect(() => {
    if (!draft.owned || !settings.autoSave || !dirty) return;
    const timer = setTimeout(() => { void onSave(draft).then(() => { setSavedVersion(JSON.stringify(draft)); setError(''); }).catch(reason => setError(`自动保存失败：${reason.message}`)); }, 600);
    return () => clearTimeout(timer);
  }, [draft, settings.autoSave, dirty, onSave]);
  useEffect(() => { if (!dirty) return; const prevent = (e: BeforeUnloadEvent) => e.preventDefault(); window.addEventListener('beforeunload', prevent); return () => window.removeEventListener('beforeunload', prevent); }, [dirty]);
  useEffect(() => {
    if (!dirty || settings.autoSave) return;
    const guard = (event: MouseEvent) => {
      const target = (event.target as Element).closest('a[href^="#/"], .site-header nav button, .header-create, .account-menu button');
      if (!target || target.getAttribute('href') === window.location.hash) return;
      if (!window.confirm('有尚未保存的修改。离开后将丢弃这些修改，是否继续？')) { event.preventDefault(); event.stopImmediatePropagation(); }
    };
    document.addEventListener('click', guard, true); return () => document.removeEventListener('click', guard, true);
  }, [dirty, settings.autoSave]);
  useEffect(() => {
    if (!detailOpen || !selectedId || !detail.current) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    const dialog = detail.current;
    dialog.showModal(); document.body.style.overflow = 'hidden';
    return () => { dialog.close(); document.body.style.overflow = overflow; if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [detailOpen, selectedId]);
  function closeDetails() { setDetailOpen(false); setActiveWord(''); }
  async function save(next = draft) { const saved = await onSave(next); setSavedVersion(JSON.stringify(next)); return saved; }
  async function saveModel(job: RodinJob) {
    const next = attachModel(latest.current.draft, job);
    await save(next); setDraft(next); setNotice('3D 已保存到图谱，原图与来源关系保留。');
  }
  function updateModule(changes: Partial<WorkflowModule>) {
    if (!selected || !draft.owned || runningId) return;
    setDraft(current => ({ ...current, workflow: { ...current.workflow, modules: current.workflow.modules.map(n => n.id === selected.id ? { ...n, ...changes } : n) }, previewHistory: historyOf(current), preview: undefined, status: 'draft', updatedAt: Date.now() })); setAnalysisRequested(false); setError('');
  }
  function select(id: string) { setSelectedId(id); setDetailOpen(true); setActiveWord(''); setPromptMode('override'); setAppendValue(''); setOverrideValue(draft.workflow.modules.find(n => n.id === id)?.prompt ?? ''); }
  async function generate(stage: 'draft' | 'final', targetId = selectedId, localEdit?: LocalEdit) {
    const target = draft.workflow.modules.find(n => n.id === targetId);
    if (!target || !draft.owned || abort.current) return;
    if (target.artifacts.some(artifact => artifact.mime === 'model/gltf-binary')) { setError('要生成新的 3D 版本，请回到来源图片节点，修改并确认图片后再使用生成 3D 模块。'); return; }
    if (stage === 'final') {
      try { const next = confirmPreview({ ...draft, preview: shownPreview }); setDraft(next); setSelectedId(next.preview?.nodeId ?? target.id); await save(next); setNotice('已确认最终版本，生成结果已记录为新节点，原节点结果保留。'); setError(''); } catch (e) { setError((e as Error).message); }
      return;
    }
    if (localEdit && (settings.image.provider !== 'qwen' || !credentials.image.trim() || settings.image.model === 'qwen-image')) { setError('请先在我的设置中配置千问图像编辑模型（例如 qwen-image-2.0）与 API Key。'); return; }
    setLastLocalEdit(localEdit ? { nodeId: target.id, edit: localEdit } : undefined);
    setSelectedId(target.id);
    closeDetails();
    const controller = new AbortController(); abort.current = controller; setRunningId(target.id); setPreviewMessage('正在准备生成…'); setError(''); setNotice('');
    try {
      const preview = await modulePreviewService.generate({ projectId: draft.id, module: structuredClone(target), stage, settings, credentials, signal: controller.signal, localEdit, reference: useReference ? referenceArtifact?.uri : undefined, onProgress: setPreviewMessage });
      if (controller.signal.aborted) return;
      const next = rememberPreview(draft, preview); setDraft(next); setNotice(preview.simulated ? '模拟预览完成；结果区记录了实际提示词，未调用生成模型。' : '真实模块产物已返回，可查看预览。');
    } catch (e) { if ((e as Error).name === 'AbortError') setNotice('已停止等待本次生成；供应商任务可能仍在运行并计费。'); else setError((e as Error).message); }
    finally { if (abort.current === controller) { abort.current = null; setRunningId(undefined); setPreviewMessage(''); } }
  }
  function restoreVersion(preview: ModulePreview) {
    if (!draft.owned || runningId) return;
    const next = restorePreview(draft, preview); setDraft(next); setSelectedId(preview.nodeId); setPromptMode('override'); setAppendValue(''); setOverrideValue(preview.prompt); setNotice('已恢复该版本的提示词、模型与预览。');
  }
  function openRemix() {
    const target = selected ?? draft.workflow.modules.find(n => n.id === 'character' && !n.dead) ?? draft.workflow.modules.find(n => n.prompt && !n.dead);
    if (!target) return;
    select(target.id);
  }
  async function download(format: 'aica' | 'bundle' = 'aica') {
    setExporting(true); setError('');
    try {
      let blob: Blob, filename: string;
      if (isRemote(draft)) {
        const snapshot = draft.owned ? await save(draft) : project;
        const result = await exportAgentWorkflow(snapshot, format);
        blob = result.blob; filename = result.filename;
        setNotice(`已导出固定版本 ${result.commitId.slice(0, 8)}：包含当前工作流、已保存图片及修改记录；导入 Agent 后阅读接手说明。`);
      } else {
        const record = redact({ format: 'aica-ui-module-record', title: draft.title, steps: draft.workflow.modules, edges: draft.workflow.edges,
          recorded_messages: draft.workflow.recordedMessages, preview_history: historyOf(draft), cover_artifact: draft.coverArtifact });
        blob = new Blob([JSON.stringify(record, null, 2)], { type: 'application/json' }); filename = `${draft.id}-module-record.json`;
      }
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { setError(`导出失败：${(reason as Error).message}`); }
    finally { setExporting(false); }
  }
  return <main className="module-studio">{publishSuccess && <div className="publish-success-banner" role="status" aria-live="polite" aria-atomic="true"><CheckCircle2 size={22} aria-hidden="true" /><div><strong>{publishSuccess}</strong><p>作品已保存到平台社区，可在发现页或我的创造中查看。</p></div><a href="#/">去发现页<ArrowRight size={14} /></a><button className="icon-button" aria-label="关闭发布成功提示" onClick={() => setPublishSuccess('')}><X size={18} /></button></div>}<div className="breadcrumb">创作档案<ChevronRight size={12} />{draft.parentId ? 'Fork 衍生版本' : draft.owned ? '我的原创记录' : '社区示例'}<ChevronRight size={12} />{draft.title}</div><section className="module-studio-heading"><div><span className="kicker">UNDERSTAND THE PROCESS. CONTINUE THE IDEA.</span><h1>{draft.owned ? '你的创作工作台' : '每一份创作，都有来路。'}</h1><p>{draft.owned ? `${draft.status === 'published' ? '平台已发布' : '平台私有草稿'} · ${dirty ? settings.autoSave ? '正在自动保存…' : '有未保存修改' : '已保存'}` : '先理解每一步，再 Fork 为自己的新版本。'}</p>{draft.parentId && <a className="parent-project-link" href={`#/works/${draft.parentId}`}><GitBranch size={13} />查看原作品与创作来源<ArrowRight size={12} /></a>}</div><div className="module-studio-actions"><button className="button" disabled={exporting || !!runningId} onClick={() => void download()}><ArrowDownToLine size={14} />{exporting ? "正在打包…" : isRemote(draft) ? "导出 Agent 工作流 .aica" : "导出记录"}</button>{isRemote(draft) && <button className="button" disabled={exporting || !!runningId} onClick={() => void download("bundle")}>完整接手包 ZIP</button>}{draft.owned ? <><button className="button" disabled={!!runningId} onClick={async () => { try { await save(); setNotice('草稿已保存。'); } catch { setError('保存失败，请检查平台连接。'); } }}><Save size={14} />保存草稿</button><button className="button" disabled={!!runningId} onClick={openRemix}>编辑 Remix</button><button className="button" disabled={!!runningId} onClick={() => onFork(draft)}><GitBranch size={14} />再创作</button><button className="button primary" disabled={!!runningId} onClick={() => { setPublishSuccess(''); setPublish(true); }}><Upload size={14} />{draft.status === 'published' ? '更新发布' : '发布作品'}</button></> : <button className="button primary" onClick={() => onFork(draft)}><GitBranch size={15} />Fork & Remix</button>}</div></section>
    <div className="rodin-actions" role="group" aria-label="3D 生成操作">
      <button className="button primary" disabled={!generateTarget || !!runningId} onClick={() => openThreeD('generate')}><Box size={15} />生成 3D</button>
      <button className="button" disabled={!regenerateTarget || !!runningId} onClick={() => openThreeD('regenerate')}><RotateCcw size={15} />重新生成 3D</button>
      <small>{selected ? `当前节点：${selected.title}` : '选择图片或模型节点，可生成新的 3D 版本'}</small>
    </div>
    {threeD && <dialog className="rodin-dialog" ref={threeDDialog} aria-label={threeD.mode === 'regenerate' ? '重新生成 3D' : '生成 3D'} onCancel={event => { event.preventDefault(); setThreeD(undefined); }} onClick={event => { if (event.target === threeDDialog.current) setThreeD(undefined); }}>
      <header><h2>{threeD.mode === 'regenerate' ? '重新生成 3D' : '生成 3D'}</h2><button className="icon-button" aria-label="关闭3D生成窗口" onClick={() => setThreeD(undefined)} autoFocus><X size={18} /></button></header>
      <ThreeDPanel key={`${threeD.mode}-${threeD.nodeId}-${threeD.artifact.id}`} project={draft} nodeId={threeD.nodeId} artifact={threeD.artifact} mode={threeD.mode} initialPrompt={threeD.prompt} beforeGenerate={() => save(latest.current.draft)} onSave={saveModel} onFork={() => { setThreeD(undefined); onFork(draft); }} />
    </dialog>}
    <div className="module-workspace workspace"><div className="module-process-column">
    {!!draft.workflow.recordedMessages?.length && <details className="inline-note"><summary>用户原始要求 · {draft.workflow.recordedMessages.length} 条记录</summary>{draft.workflow.recordedMessages.map(message => <div key={message.id}><small>{message.timestamp} · {message.source_pointer}</small><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{message.text}</pre></div>)}</details>}
    {!!draft.workflow.historyIssues?.length && <details className="inline-note"><summary>部分历史附件未展开，原附件仍可查看</summary><pre>{displayValue(draft.workflow.historyIssues)}</pre></details>}
    <GraphExplorer workflow={draft.workflow} selected={selectedId} onSelect={select} decisions={decisions} runningId={runningId} activeWord={activeWord} onRemix={openRemix} />
    {selected && detailOpen ? <dialog className="module-inspector module-detail-dialog" ref={detail} aria-label="创作细节" onCancel={e => { e.preventDefault(); closeDetails(); }} onClick={e => { if (e.target === detail.current) closeDetails(); }}><header><div><span className="kicker">STEP {String(selectedGroup ? creationView(draft.workflow).nodes.findIndex(g => g.id === selectedGroup.id) + 1 : draft.workflow.modules.findIndex(n => n.id === selected.id) + 1).padStart(2, '0')} / 原始记录 {draft.workflow.modules.findIndex(n => n.id === selected.id) + 1}</span><h2>{selectedGroup && selectedGroup.member_node_ids.length > 1 ? `${phaseLabel(selectedGroup.label)} · ${selected.title}` : selected.title}</h2></div><button className="icon-button" aria-label="关闭模块详情" onClick={closeDetails} autoFocus><X size={18} /></button></header><div className="prompt-section-heading"><div><h3>创作描述与提示词</h3><p>{draft.owned ? '直接点击文字即可修改；悬停词语或选中文字追踪相关内容。' : '查看创作记录；Fork 后可直接修改。'}</p></div>{!draft.owned && <button className="button" onClick={() => onFork(draft)}><GitBranch size={14} />Fork 后编辑</button>}</div>
    {selectedGroup && selectedGroup.member_node_ids.length > 1 && <label className="form-field creation-record-picker">{selectedGroup.pending_association ? '候选提示词与结果记录' : '本次创作记录'} · {selectedGroup.member_node_ids.length} 条<select aria-label="选择组内创作记录" value={selected.id} disabled={!!runningId} onChange={e => select(e.target.value)}>{selectedGroup.member_node_ids.map(id => { const node = draft.workflow.modules.find(n => n.id === id); return node && <option key={id} value={id}>{node.kind === 'intent' ? '用户原始要求' : node.kind === 'tool' ? `执行提示词 · ${node.title}` : node.kind === 'decision' ? 'AI 回复' : node.kind === 'result' ? '生成结果 · 可填写提示词' : node.title}{node.status === 'failed' ? ' · 失败记录' : ''}{node.model ? ` · ${node.model}` : ''}</option>; })}</select><small>{selectedGroup.pending_association ? '候选提示词按文件名称匹配，尚未验证；可选择核对，或在结果记录中补充提示词。' : '编辑和预览作用于当前选中记录，历史生成结果保持不变。'}</small></label>}
    {selectedGroup?.user_node_id && selected.id !== selectedGroup.user_node_id && <div className="iteration-user-request"><span>用户原始要求</span><p>{draft.workflow.modules.find(n => n.id === selectedGroup.user_node_id)?.prompt || selectedGroup.user_prompt}</p><small>下方编辑的是本次执行提示词，原始要求保留用于追溯。</small></div>}
    {draft.owned && promptMode === 'keep' && <p className="inline-note">当前保留起点提示词；直接输入会切换到覆盖模式，也可以选择追加。</p>}
    {draft.owned && <div className="module-prompt-modes" role="group" aria-label="提示词修改模式">{[['keep', 'A · 保留'], ['override', 'B · 覆盖'], ['append', 'C · 追加']].map(([mode, label]) => <button key={mode} disabled={!!runningId} aria-pressed={promptMode === mode} onClick={() => { setPromptMode(mode); updateModule({ prompt: mode === 'keep' ? basePrompt : mode === 'append' ? [basePrompt, appendValue].filter(Boolean).join('，') : overrideValue }); }}>{label}</button>)}</div>}
    {promptMode === 'append' && draft.owned && <><PromptWords text={basePrompt} activeWord={activeWord} onWord={setActiveWord} enabled={settings.highlightWords} /><h3 className="append-prompt-title">追加描述</h3></>}
    <PromptEditor key={selected.id} value={promptMode === 'append' && draft.owned ? appendValue : selected.prompt} disabled={!draft.owned || !!runningId} highlight={settings.highlightWords} activeWord={activeWord} onWord={setActiveWord} onChange={value => { if (promptMode === 'append') setAppendValue(value); else { setOverrideValue(value); if (promptMode === 'keep') setPromptMode('override'); } updateModule({ prompt: promptMode === 'append' ? [basePrompt, value].filter(Boolean).join('，') : value }); }} />
    {draft.owned && <div className="module-edit-form" id="node-remix-editor"><div className="prompt-edit-meta"><span>{selected.prompt.length} 字</span><button disabled={!!runningId} onClick={() => { setPromptMode('override'); setAppendValue(''); setOverrideValue(basePrompt); updateModule({ prompt: basePrompt }); }}><RotateCcw size={12} />恢复起点提示词</button></div><label className="form-field">此记录使用的模型 ID<input aria-label="模块模型 ID" value={selected.model} placeholder={`留空使用设置中的 ${previewKind(selected) === 'video' ? settings.video.model || '生视频模型' : settings.image.model}`} disabled={!!runningId} onChange={e => updateModule({ model: e.target.value })} /></label><p className="inline-note">上方提示词可直接编辑。未记录的模型、思考深度与环境信息不会自动补造。</p></div>}
    {selectedGroup?.grouping === 'result' && <ResultGallery artifacts={selectedArtifacts} text={selectedGroup.result_text} pending={selectedGroup.pending_association} localError={error} localModel={settings.image.model} renderGenerationActions={artifact => <div className="rodin-actions" role="group" aria-label="当前结果的3D操作"><button className="button primary" disabled={!!runningId || !generationTarget(draft, selected.id, 'generate', artifact.mime.startsWith('image/') ? artifact.id : undefined)} onClick={() => openThreeD('generate', artifact)}><Box size={15} />生成 3D</button><button className="button" disabled={!!runningId || !generationTarget(draft, selected.id, 'regenerate', artifact.mime.startsWith('image/') ? artifact.id : undefined)} onClick={() => openThreeD('regenerate', artifact)}><RotateCcw size={15} />重新生成 3D</button></div>} onLocalEdit={draft.owned && !runningId ? edit => void generate('draft', selected.id, edit) : undefined} />}
    <div className="creation-environment"><span><small>思考深度 / Reasoning effort</small><strong>{displayValue(selectedEnvironment.reasoning_effort ?? selectedEnvironment.thinking_depth) || '未记录'}</strong></span><span><small>思考预算 / Thinking budget</small><strong>{displayValue(selectedEnvironment.thinking_budget) || '未记录'}</strong></span><span><small>工具 / 服务商</small><strong>{displayValue(selectedEnvironment.tool || selectedEnvironment.provider) || '未记录'}</strong></span></div>
    <div className="module-facts"><span><Box size={14} /><small>执行模型</small><strong>{selected.model || '文件未记录'}</strong></span><span><small>Agent 模型</small><strong>{selected.agentModel || '文件未记录'}</strong></span><span><small>模式</small><strong>{selected.mode || '未提供'}</strong></span><span><small>来源</small><strong>{({ demo: '本地演示', recorded: 'Recorded', extracted: 'Extracted', inferred: 'Inferred', unknown: '未知' } as Record<string, string>)[selected.provenance] ?? selected.provenance}</strong></span></div>
    {!!selected.runHistory?.length && <details><summary>实际运行历史 · {selected.runHistory.length} 次</summary><pre>{displayValue(selected.runHistory)}</pre></details>}
    {!!selected.fieldStatus && <details><summary>Agent 与字段来源</summary><pre>{displayValue({ agent: selected.agent, fields: selected.fieldStatus })}</pre></details>}
    <div className="module-detail-grid"><details open><summary>Harness / 执行环境</summary><pre>{selected.harness || '文件未提供 Harness 信息。'}</pre></details><details open><summary>记录中的参数与输入输出</summary><pre>{JSON.stringify({ status: selected.status ?? '未记录', environment: selectedEnvironment, params: selected.params, inputs: selected.inputs, outputs: selected.outputs }, null, 2)}</pre></details><details open><summary>依赖与来源证据</summary><pre>{JSON.stringify({ upstream: draft.workflow.edges.filter(e => e.target === selected.id).map(e => e.source), downstream: draft.workflow.edges.filter(e => e.source === selected.id).map(e => e.target), evidence: selected.evidence }, null, 2)}</pre></details>{selected.artifacts.length > 0 && <details open><summary>模块产物引用</summary><pre>{displayValue(selected.artifacts)}</pre></details>}</div>{draft.owned && <div className="node-remix-actions">{referenceArtifact && previewKind(selected) === 'image' && <label className="preview-reference"><input type="checkbox" checked={useReference} disabled={!!runningId} onChange={event => setUseReference(event.target.checked)} />参考原图生成<small>需使用支持图像编辑的模型</small></label>}<button className="button" disabled={!!runningId} onClick={() => { setAnalysisRequested(true); setNotice('修改影响已分析，可在 Graph 下方查看增量复用。'); }}>分析影响</button><button className="button primary" disabled={!!runningId || !selected.prompt.trim() || selected.dead} onClick={() => generate('draft')}>生成修改预览<ArrowRight size={14} /></button></div>}
    </dialog> : null}
    {project.archiveCommit && <ArchiveAssets project={project} />}
    <IncrementalReuse workflow={draft.workflow} decisions={decisions} runningId={runningId} onCancel={() => abort.current?.abort()} />

    </div><ModulePreviewDock project={draft} selected={selected ? { ...selected, artifacts: selectedGroup?.grouping === 'result' ? selectedArtifacts : selected.artifacts, resultText: selectedGroup?.result_text, resultPending: selectedGroup?.pending_association } : undefined} preview={shownPreview} running={!!runningId} progress={previewMessage} error={error} history={histories} canRetryLocal={lastLocalEdit?.nodeId === selectedId} onRestore={restoreVersion} onEdit={() => { if (selected) select(selected.id); }} onSelectVersion={id => { setSelectedId(id); setDetailOpen(false); setLastLocalEdit(undefined); setError(''); }} onGenerate={stage => void generate(stage, selectedId, stage === 'draft' ? lastLocalEdit?.nodeId === selectedId ? lastLocalEdit.edit : shownPreview?.localEdit : undefined)} onCancel={() => abort.current?.abort()} /></div>{error && <p className="inline-error workbench-feedback" role="alert">{error}</p>}{notice && <p className="inline-success workbench-feedback" role="status">{notice}</p>}{publish && <PublishDialog project={draft} onClose={() => setPublish(false)} onPublish={async next => { await save(next); setDraft(next); setError(''); setNotice(''); setPublishSuccess(draft.status === 'published' ? '作品更新发布成功' : '作品发布成功'); }} />}
  </main>;
}
