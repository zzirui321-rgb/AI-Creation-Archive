import { useEffect, useRef, useState } from 'react';
import { platform, saveParsedGraph } from './platform';
import type { ParseJob } from './platform-client.mjs';
import type { WorkspaceProject } from './workspace';
import McpUploadPanel from './McpUploadPanel';
import { parseFailure } from './parseFailure';

export default function SemanticImportPanel({ onReady, queuedFile }: { onReady: (project: WorkspaceProject) => void; queuedFile?: File }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [review, setReview] = useState<Record<string, unknown> & { input_sha256: string }>();
  const [source, setSource] = useState<{ projectId: string; importId: string; title: string }>();
  const [job, setJob] = useState<ParseJob>();
  const [consent, setConsent] = useState(false);
  const alive = useRef(true);
  const consumedFile = useRef<File | undefined>(undefined);
  const parseRequestId = useRef<string | undefined>(undefined);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (queuedFile && !busy && consumedFile.current !== queuedFile) { consumedFile.current = queuedFile; void upload(queuedFile); }
  }, [queuedFile, busy]);

  async function upload(file?: File) {
    if (!file || busy) return;
    setBusy(true); setError(''); setReview(undefined); setSource(undefined); setJob(undefined); setConsent(false); parseRequestId.current = undefined;
    try {
      const isZip = file.name.toLowerCase().endsWith('.zip');
      if (!/\.(zip|aica|json)$/i.test(file.name)) throw new Error('请选择 .aica / aica.json 或 A 导出的 ZIP。');
      if (isZip && file.size > 512 * 1024 * 1024) throw new Error('ZIP 超过协议规定的 512 MiB。');
      const text = isZip ? '' : await file.text();
      const archive = isZip ? undefined : JSON.parse(text.replace(/^\uFEFF/, ''));
      if (archive && archive.format === 'aica-semantic-graph/0.1') throw new Error('这已经是提炼后的 Graph，请使用下方直接导入，无需再次调用 AI。');
      if (archive && !archive.aica_version) throw new Error('AI 提炼需要正式 .aica 档案（包含 aica_version）；展示记录请在下方直接导入。');
      await platform.me();
      const info = await platform.parserInfo();
      if (!info.configured) throw new Error('服务器尚未配置解析模型，请先配置后再上传。');
      setMessage('正在上传并准备解析材料，尚未调用模型…');
      const title = (typeof archive?.metadata?.title === 'string' ? archive.metadata.title : file.name.replace(/\.(zip|aica|json)$/i, '')).slice(0, 180) || '创作档案';
      const { project } = await platform.createProject(title);
      const receipt = isZip ? await platform.uploadBundle(project.id, await file.arrayBuffer()) : await platform.uploadArchive(project.id, text.replace(/^\uFEFF/, ''));
      const prepared = await platform.prepareGraph(project.id, receipt.import_id);
      if (!Array.isArray(prepared.input.facts) || !prepared.input.facts.length) throw new Error('档案中没有可提炼的节点或文字材料；请补充完整档案和附件。未调用 AI。');
      if (!alive.current) return;
      setSource({ projectId: project.id, importId: receipt.import_id, title }); setReview(prepared.input);
      const missing = 'missing_artifact_ids' in receipt ? receipt.missing_artifact_ids.length : 0;
      setMessage(`${info.parser_version} · ${title} · 输入已准备，请审阅后确认 AI 提炼。${missing ? `有 ${missing} 份引用附件未随 JSON 上传，本次仅依据档案内的节点、文字和附件元数据；如需分析附件正文，请上传完整 ZIP。` : ''}`);
    } catch (err) { if (alive.current) setError((err as Error).message); }
    finally { if (alive.current) setBusy(false); }
  }

  async function parse() {
    if (!source || !review || !consent || busy || job) return;
    setBusy(true); setError('');
    try {
      const requestId = parseRequestId.current ??= 'web-' + Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('');
      let result = (await platform.parseGraph(source.projectId, source.importId, review.input_sha256, requestId)).parse;
      while (alive.current) {
        setJob(result); setMessage(`解析状态：${result.state}`);
        if (!['queued', 'running'].includes(result.state)) break;
        await new Promise(resolve => setTimeout(resolve, 2000));
        if (!alive.current) return;
        result = (await platform.parseJob(source.projectId, result.id)).parse;
      }
      if (alive.current && result.state !== 'succeeded') throw new Error(parseFailure(result));
      if (alive.current) await save(result);
    } catch (err) { if (alive.current) setError((err as Error).message); }
    finally { if (alive.current) setBusy(false); }
  }

  async function save(result = job) {
    if (!source || result?.state !== 'succeeded') return;
    setBusy(true); setError('');
    try {
      setMessage('AI 提炼已成功，正在确认自动保存并打开 Graph…');
      const project = await saveParsedGraph(source.projectId, result.id);
      if (alive.current) onReady(project);
    } catch (err) { setError(`上库失败：${(err as Error).message}。解析结果仍在服务端，不会再次调用模型。`); }
    finally { if (alive.current) setBusy(false); }
  }

  async function prepareAgain() {
    if (!source || busy) return;
    setBusy(true); setError('');
    try {
      const prepared = await platform.prepareGraph(source.projectId, source.importId);
      setReview(prepared.input); setJob(undefined); setConsent(false); parseRequestId.current = undefined;
      setMessage('已使用服务器保存的档案重新准备材料，无需重新上传；尚未调用模型，请审阅并重新确认。');
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return <section className="surface-section" style={{ marginBottom: 24 }} aria-label="提炼 Graph">
    <span className="kicker">SOURCE → REUSABLE MODULES → GRAPH</span><h2>用解析模块提炼创作图</h2>
    <p>上传已经生成的 .aica / aica.json 或完整 ZIP，交给 AI 提炼人物、风格、意图等可复用模块。原始节点不会直接冒充提炼结果。</p>
    {review?.output_strategy === 'modules_only_pages_restored_from_source' && <p role="status">页面较多：AI 提炼共享模块和模块提示词，逐页原文、提示词与图片完整保留；本次不逐页生成 AI 扩展，使用紧凑输出格式，输出额度为最多 8192 tokens（可能产生相应模型费用）。</p>}
    <McpUploadPanel disabled={busy} onSelect={async (projectId, importId, title) => {
      const prepared = await platform.prepareGraph(projectId, importId);
      if (!Array.isArray(prepared.input.facts) || !prepared.input.facts.length) throw new Error('此快照没有可提炼材料。');
      parseRequestId.current = undefined;
      setSource({ projectId, importId, title }); setReview(prepared.input); setJob(undefined); setConsent(false); setError('');
      setMessage('已准备 MCP 上传快照，请审阅并确认后再调用 AI。');
    }} />
    <label className="button">选择 .aica / JSON / ZIP 提炼<input aria-label="上传待提炼档案" type="file" accept=".aica,.json,.zip" disabled={busy} onChange={event => { void upload(event.target.files?.[0]); event.target.value = ''; }} /></label>
    {message && <p role="status">{message}</p>}
    {review && <><details><summary>查看将发送给解析模型的材料</summary><pre style={{ maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{JSON.stringify(review, null, 2)}</pre></details>
      <label style={{ display: 'block', margin: '16px 0' }}><input type="checkbox" checked={consent} disabled={busy || !!job} onChange={event => setConsent(event.target.checked)} /> 我已审阅并同意将以上材料发送给配置的 DeepSeek，执行一次解析（可能产生模型费用），成功后自动保存到我的私有仓库。</label>
      <button className="button primary" disabled={!consent || busy || !!job} onClick={() => void parse()}>{busy ? '处理中…' : '开始提炼 Graph'}</button></>}
    {job?.state === 'succeeded' && <button className="button primary" disabled={busy} onClick={() => void save()}>打开已保存的 Graph / 重试保存</button>}
    {job?.state === 'failed' && source && <button className="button" disabled={busy} onClick={() => void prepareAgain()}>重新准备已上传档案（不调用 AI）</button>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
}
