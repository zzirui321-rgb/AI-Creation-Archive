import { platform, loadProject } from './platform';
import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, FileJson, GitBranch, UploadCloud } from 'lucide-react';
import { categories } from './community';
import { importCreation, type ImportedRecord } from './importing/parser';
import { creationView, phaseLabel } from './creation-view';
import { newId, type WorkspaceProject } from './workspace';
import McpUploadPanel from './McpUploadPanel';

export default function ImportPage({ author, onImport, authenticated, onLogin }: { author: string; onImport: (project: WorkspaceProject) => Promise<unknown>; authenticated: boolean; onLogin: () => void }) {
  const [remoteProject, setRemoteProject] = useState<WorkspaceProject>();
  const [parsed, setParsed] = useState<ImportedRecord | null>(null); const [title, setTitle] = useState(''); const [description, setDescription] = useState(''); const [category, setCategory] = useState('图片');
  const [fileName, setFileName] = useState(''); const [error, setError] = useState(''); const [dragging, setDragging] = useState(false); const [busy, setBusy] = useState(false); const sequence = useRef(0); const controller = useRef<AbortController | null>(null); const [progress, setProgress] = useState('');
  useEffect(() => () => controller.current?.abort(), []);
  async function read(file?: File) {
    if (!file) return;
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    const token = ++sequence.current; setError(''); setParsed(null); setBusy(true); setFileName(file.name); setProgress('正在上传创作记录…');
    try {
      if (!/\.(json|aica)$/i.test(file.name)) throw new Error('请选择 .json 或 .aica 文件。');
      const next = await importCreation(file, current.signal, text => { if (token === sequence.current) setProgress(text); });
      if (next.repository) {
        const { repository } = await platform.repository(next.repository.id);
        setRemoteProject(await loadProject(repository));
      } else setRemoteProject(undefined);
      if (token !== sequence.current || current.signal.aborted) return;
      setParsed(next); setTitle(next.title); setDescription(next.description);
    } catch (e) {
      if (token === sequence.current && !current.signal.aborted) setError((e as Error).message);
    } finally { if (token === sequence.current) { setBusy(false); controller.current = null; } }
  }
  async function create() {
    if (!parsed || !title.trim()) { setError('请填写作品标题。'); return; }
    try { const modules = parsed.workflow.modules; await onImport({ id: remoteProject?.id ?? newId(), archiveCommit: remoteProject?.archiveCommit, branch: remoteProject?.branch, title: title.trim(), english: 'My Creation Archive', description: description.trim(), author, avatar: author.slice(0, 1), category, theme: 'type', accent: '#9ecddd', height: 310, likes: 0, forks: 0, date: new Date().toISOString().slice(0, 10), tools: [...new Set(modules.map(n => n.model).filter(Boolean))].slice(0, 3), values: { character: title.trim(), style: '导入创作档案', detail: '' }, workflow: parsed.workflow, previewHistory: parsed.previewHistory, coverArtifact: parsed.coverArtifact, owned: true, status: 'draft', updatedAt: Date.now(), basePrompts: Object.fromEntries(modules.map(n => [n.id, n.prompt])), baseModels: Object.fromEntries(modules.map(n => [n.id, n.model])) }); } catch (reason) { setError(`导入未保存：${(reason as Error).message}`); }
  }
  return <main className="content-page import-page"><a className="back-link" href="#/creations"><ArrowLeft size={15} />返回我的创造</a><div className="content-page-heading"><span className="kicker">EVERY PROCESS DESERVES AN ARCHIVE</span><h1>从自己的创作开始。</h1><p>上传创作过程 JSON，解析后在原有 Creation Graph 中查看和继续创作。</p></div><section className="surface-section" style={{ marginBottom: 24 }} aria-label="MCP 自动上传设置">{authenticated ? <McpUploadPanel disabled={busy} /> : <><span className="kicker">CONNECT YOUR MCP</span><h2>连接 MCP 与平台</h2><p>登录平台后，创建上传项目并生成 MCP 专用令牌。</p><button className="button primary" onClick={onLogin}>登录平台账号</button></>}</section><div className="import-layout"><section className="surface-section"><label className={`archive-dropzone ${dragging ? 'is-dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); void read(e.dataTransfer.files[0]); }}><input type="file" accept=".json,.aica,application/json" aria-label="上传 MCP 解析文件" onChange={e => { void read(e.target.files?.[0]); e.target.value = ''; }} /><span className="upload-orb"><UploadCloud size={29} /></span><strong>{busy ? progress : '选择文件，或拖放到这里'}</strong><p>.aica / .json · 无固定文件大小上限</p><span className="button">选择创作记录<FileJson size={15} /></span></label>{busy && <p className="inline-note" role="status" aria-live="polite">{progress}</p>}{fileName && <div className="import-file-name"><FileJson size={17} />{fileName}{parsed && <Check size={16} />}</div>}{error && <p className="inline-error" role="alert">{error}</p>}
    {parsed && <div className="import-review"><div className="import-stat-row"><span><strong>{creationView(parsed.workflow).nodes.length}</strong>生成结果节点</span><span><strong>{creationView(parsed.workflow).raw_node_count}</strong>原始过程记录</span><span><strong>{parsed.workflow.modules.filter(n => n.prompt).length}</strong>提示词</span></div><label className="form-field">作品标题<input value={title} maxLength={100} onChange={e => setTitle(e.target.value)} /></label><label className="form-field">作品分类<select value={category} onChange={e => setCategory(e.target.value)}>{categories.filter(c => c !== '全部').map(c => <option key={c}>{c}</option>)}</select></label><label className="form-field">作品说明<textarea rows={3} value={description} maxLength={2000} onChange={e => setDescription(e.target.value)} /></label><ol className="import-module-list">{creationView(parsed.workflow).nodes.slice(0, 8).map(group => { const n = parsed.workflow.modules.find(n => group.member_node_ids.includes(n.id))!; return <li key={n.id}><GitBranch size={14} /><strong>{phaseLabel(group.label)}{group.member_node_ids.length > 1 ? ` · ${group.member_node_ids.length} 条记录` : ''}</strong><span>{n.model || '未提供模型'}</span></li>; })}</ol>{parsed.workflow.modules.length > 8 && <p className="muted">完整记录保留在节点内部和来源资料中，画布只展示生成结果节点。</p>}{parsed.workflow.warnings.map(w => <p className="inline-note" key={w}>{w}</p>)}<button className="button primary full" disabled={!title.trim()} onClick={() => void create()}>创建我的创作档案<ArrowRight size={16} /></button></div>}
    </section><aside className="import-guide surface-section"><span className="kicker">CAPTURE → UNDERSTAND → CONTINUE</span><h2>你的记录，你的起点。</h2><ol><li><strong>上传创作过程记录</strong><p>保留提示词、模型、模式、Harness，以及明确的依赖关系。</p></li><li><strong>导入并检查 Graph</strong><p>自动识别步骤、对话与工具记录；候选关系会明确标注。</p></li><li><strong>继续创作与发布</strong><p>修改模块生成预览，在本机社区发布；原始记录仍作为创作起点。</p></li></ol><a className="button" href="/examples/module-record.json" download>下载解析结果示例<FileJson size={15} /></a><p className="inline-note">支持 .aica、steps / modules、工具调用、对话与事件 JSON。解析后的节点在原有创作过程画布中展示，未记录的信息不会补造。</p></aside></div></main>;
}
