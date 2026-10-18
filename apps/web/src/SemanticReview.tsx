import { parseFailure } from './parseFailure';
import SemanticModuleContent from './SemanticModuleContent';
import type { WorkspaceProject } from './workspace';
import { platform, saveParsedGraph } from './platform';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, ReactFlow, type Edge, type Node } from '@xyflow/react';
import { Check, FileSearch, Upload, Play, Download } from 'lucide-react';
import type { GraphDocument } from '../../../packages/runtime/design/graph-v0.1/graph.generated';
import { importParserBundle, parserJson, parserRequest, readSemanticGraph, type ParseJob, type ParserSelection, type PreparedInput } from './parserClient';
import '@xyflow/react/dist/style.css';
import './semantic.css';

export default function SemanticReview({ projectId = '', archiveId = '', title = '' }: { projectId?: string; archiveId?: string; title?: string }) {
  const [selection, setSelection] = useState<ParserSelection>();
  const [input, setInput] = useState<PreparedInput>();
  const [sourceNodes, setSourceNodes] = useState<{ id: string; label: string }[]>([]);
  const [scope, setScope] = useState<string[] | null>(null);
  const pending = useRef<{ key: string; requestId: string } | undefined>(undefined);
  const [provider, setProvider] = useState<{ configured: boolean; model: string; parser_version: string }>();
  const [jobs, setJobs] = useState<ParseJob[]>([]);
  const [job, setJob] = useState<ParseJob>();
  const [graph, setGraph] = useState<GraphDocument>();
  const [selected, setSelected] = useState<string>();
  const [evidence, setEvidence] = useState<unknown>();
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [savedRepository, setSavedRepository] = useState('');
  const [savedProject, setSavedProject] = useState<WorkspaceProject>();
  const request = useRef<AbortController | undefined>(undefined);
  const prefix = selection ? `/projects/${selection.projectId}` : '';
  const running = !!job && ['queued', 'running'].includes(job.state);
  useEffect(() => {
    const abort = new AbortController();
    parserRequest<{ configured: boolean; model: string; parser_version: string }>('/graph-parser', { signal: abort.signal }).then(setProvider).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    return () => { abort.abort(); request.current?.abort(); };
  }, []);
  useEffect(() => {
    request.current?.abort(); setSavedRepository(''); setSavedProject(undefined); setSelection(undefined); setInput(undefined); setGraph(undefined); setJob(undefined); setJobs([]); setEvidence(undefined); setConsent(false); setBusy(false); setError(''); setSourceNodes([]); setScope(null); pending.current = undefined;
    try { const saved = localStorage.getItem(`aica.parser:${projectId}:${archiveId}`); if (saved) setSelection(JSON.parse(saved)); } catch { /* A fresh import remains available. */ }
  }, [projectId, archiveId]);
  useEffect(() => {
    if (!selection) return;
    const abort = new AbortController();
    setSourceNodes([]); setScope(null); setInput(undefined); setConsent(false);
    parserRequest<{ archive: { graph: { nodes: { id: string; label: string }[] } } }>(`${prefix}/archive?import_id=${selection.importId}`, { signal: abort.signal }).then(value => { if (!abort.signal.aborted) setSourceNodes(value.archive.graph.nodes); }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    parserRequest<{ parses: ParseJob[] }>(`${prefix}/semantic-parses`, { signal: abort.signal }).then(value => { if (!abort.signal.aborted) setJobs(value.parses.filter(item => (item as ParseJob & { import_id: string }).import_id === selection.importId)); }).catch(reason => { if (!abort.signal.aborted) setError(reason.message); });
    return () => abort.abort();
  }, [selection, prefix]);
  async function act(action: (signal: AbortSignal) => Promise<void>) {
    if (busy || running) return;
    const abort = new AbortController(); request.current = abort; setBusy(true); setError(''); setEvidence(undefined);
    try { await action(abort.signal); } catch (reason) { if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (request.current === abort && !abort.signal.aborted) setBusy(false); }
  }
  async function attach(bytes: ArrayBuffer, signal: AbortSignal) {
    const value = await importParserBundle(bytes, title || 'Imported transfer archive', signal);
    if (signal.aborted) return;
    setSelection(value); setInput(undefined); setGraph(undefined); setJob(undefined); setJobs([]); setConsent(false);
    try { localStorage.setItem(`aica.parser:${projectId}:${archiveId}`, JSON.stringify(value)); } catch { /* The server retains the import even without browser storage. */ }
  }
  async function exportSnapshot(signal: AbortSignal) {
    const response = await platform.authorizedFetch(`/runtime-api/projects/${projectId}/transfer?archive_id=${encodeURIComponent(archiveId)}`, { signal });
    if (!response.ok) throw new Error(`Archive export failed: ${response.status}`);
    return response;
  }
  async function showGraph(value: ParseJob, signal: AbortSignal) {
    if (!selection) return;
    setSavedRepository('');
    const document = await readSemanticGraph(selection, value, signal);
    if (!signal.aborted) { setGraph(document); setSelected(undefined); setEvidence(undefined); }
    const project = await saveParsedGraph(selection.projectId, value.id);
    if (!signal.aborted) { setSavedRepository(project.id); setSavedProject(project); }
  }
  const flow = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const levels = new Map(graph.nodes.map(node => [node.id, 0]));
    for (let round = 0; round < graph.nodes.length; round++) {
      let changed = false;
      for (const edge of graph.edges.filter(edge => edge.kind !== 'semantic' && edge.state === 'verified')) {
        const level = Math.min(graph.nodes.length, (levels.get(edge.source) || 0) + 1);
        if (level > (levels.get(edge.target) || 0)) { levels.set(edge.target, level); changed = true; }
      }
      if (!changed) break;
    }
    const rows = new Map<number, number>();
    const nodes: Node[] = graph.nodes.map(node => {
      const level = levels.get(node.id) || 0, row = rows.get(level) || 0; rows.set(level, row + 1);
      return { id: node.id, position: { x: level * 260, y: row * 145 }, data: { label: <><strong>{node.label}</strong><small>{node.kind} · {node.purpose.review}</small></> }, selected: selected === node.id, style: { width: 210, background: '#19262b', color: '#edf2ed', border: '1px solid #779b9b', borderRadius: 4, padding: 14 } };
    });
    const edges: Edge[] = graph.edges.map(edge => {
      const verified = edge.kind !== 'semantic' && edge.state === 'verified';
      return { id: edge.id, source: edge.source, target: edge.target, label: edge.kind === 'semantic' ? `semantic · ${edge.relation}` : `${edge.kind} · ${edge.state}`, style: { stroke: verified ? '#7fca9b' : '#d9b15a', strokeDasharray: verified ? undefined : '5 5' }, labelStyle: { fill: '#142126', fontSize: 11 }, labelBgStyle: { fill: '#e6ece7' } };
    });
    return { nodes, edges };
  }, [graph, selected]);
  const step = graph?.nodes.find(node => node.id === selected);
  const selectedModule = savedProject?.workflow.modules.find(node => node.id === selected);
  return <section className="semantic-review" aria-label="语义图解析与证据"><header><div><h2><FileSearch size={18} />语义图与来源证据</h2><p>待审核派生视图 · 不可执行</p></div><span>{provider?.configured ? `${provider.model} · ${provider.parser_version}` : '解析模型未配置'}</span></header>
    <div className="semantic-toolbar">{projectId && archiveId && <><button className="button" disabled={busy || running} onClick={() => void act(async signal => { const response = await exportSnapshot(signal); await attach(await response.arrayBuffer(), signal); })}><Check size={14} />载入当前档案快照</button><button className="button" disabled={busy || running} onClick={() => void act(async signal => { const response = await exportSnapshot(signal); const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = url; link.download = 'archive.aica.zip'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); })}><Download size={14} />导出完整档案</button></>}
      <label className="button"><Upload size={14} />导入 A 传输 ZIP<input type="file" aria-label="导入语义分析传输包" accept=".zip" disabled={busy || running} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void act(async signal => { if (file.size > 512 * 1024 * 1024) throw new Error('Transfer exceeds 512 MiB'); await attach(await file.arrayBuffer(), signal); }); }} /></label>
      {selection && <button className="button" disabled={busy || running || (sourceNodes.length > 0 && scope?.length === 0)} onClick={() => void act(async signal => { const result = await parserRequest<{ input: PreparedInput }>(`${prefix}/semantic-parse-input`, parserJson({ import_id: selection.importId, node_ids: scope }, signal)); if (!signal.aborted) { setInput(result.input); setConsent(false); } })}><FileSearch size={14} />准备解析输入</button>}</div>
    {selection && <p className="semantic-identity">Source import: {selection.importId}</p>}
    {selection && sourceNodes.length > 0 && <fieldset className="semantic-scope" disabled={busy || running}><legend>解析范围</legend><label><input type="checkbox" aria-label="全部原始节点" checked={scope === null} onChange={event => { setScope(event.target.checked ? null : []); setInput(undefined); setConsent(false); setError(''); }} />全部原始节点（{sourceNodes.length}）</label>{scope !== null && <div>{sourceNodes.map(node => <label key={node.id}><input type="checkbox" aria-label={`解析节点 ${node.id}`} checked={scope.includes(node.id)} onChange={event => { setScope(current => event.target.checked ? [...(current || []), node.id] : (current || []).filter(id => id !== node.id)); setInput(undefined); setConsent(false); setError(''); }} />{node.label} · {node.id}</label>)}</div>}</fieldset>}
    {error && <p role="alert" className="inline-error">{error}</p>}
    {savedRepository && <p role="status">已自动保存到我的仓库。<a href={`#/works/${savedRepository}`}>查看仓库中的 Graph</a></p>}
    {input && <><details open><summary>待发送输入：{input.facts.length} 项事实 / {input.source_node_ids.length} 个原始节点</summary><pre>{JSON.stringify(input, null, 2)}</pre></details><label className="semantic-consent"><input type="checkbox" checked={consent} aria-label="允许发送已审核事实到解析模型" disabled={busy || running} onChange={event => setConsent(event.target.checked)} />允许发送以上已审核事实到解析模型（可能产生 API 费用）</label><button className="button" disabled={!consent || !provider?.configured || busy || running} onClick={() => void act(async signal => {
      const key = `aica.parse-request:${selection!.importId}:${input.input_sha256}`;
      if (pending.current?.key !== key) {
        let saved: string | null = null; try { saved = localStorage.getItem(key); } catch { /* The in-memory request still preserves retry identity. */ }
        pending.current = { key, requestId: saved || crypto.randomUUID() };
        try { localStorage.setItem(key, pending.current.requestId); } catch { /* Browser storage is optional. */ }
      }
      const result = await parserRequest<{ parse: ParseJob }>(`${prefix}/semantic-parses`, parserJson({ request_id: pending.current.requestId, import_id: selection!.importId, input_sha256: input.input_sha256, node_ids: scope, allow_external_processing: true }, signal));
      let value = result.parse;
      while (!signal.aborted) {
        setJob(value);
        if (!['queued', 'running'].includes(value.state)) { setJobs(current => [value, ...current.filter(item => item.id !== value.id)]); if (value.state !== 'unknown') { pending.current = undefined; try { localStorage.removeItem(key); } catch { /* Browser storage is optional. */ } } if (value.state === 'succeeded') await showGraph(value, signal); else throw new Error(parseFailure(value)); break; }
        await new Promise<void>(resolve => { const timer = setTimeout(stop, 700); function stop() { clearTimeout(timer); signal.removeEventListener('abort', stop); resolve(); } signal.addEventListener('abort', stop, { once: true }); });
        if (signal.aborted) return;
        value = (await parserRequest<{ parse: ParseJob }>(`${prefix}/semantic-parses/${value.id}`, { signal })).parse;
      }
    })}><Play size={14} />提交语义解析</button></>}
    {job && <p role="status">Parse: {job.state} {job.error_code || ''}</p>}
    {jobs.length > 0 && <div className="semantic-history">{jobs.map(item => <button className="button" key={item.id} disabled={busy || running || item.state !== 'succeeded'} onClick={() => void act(signal => showGraph(item, signal))}>{item.state} · {item.id.slice(0, 8)}</button>)}</div>}
    {graph && <><p className="semantic-identity">{graph.state} · executable=false · {graph.graph_id} / {graph.revision_id}</p><div className="semantic-canvas"><ReactFlow key={graph.revision_id} nodes={flow.nodes} edges={flow.edges} fitView nodesDraggable={false} nodesConnectable={false} onNodeClick={(_, node) => { setSelected(node.id); setEvidence(undefined); }}><Background /><Controls showInteractive={false} /></ReactFlow></div><details open><summary>范围与缺项</summary><pre>{JSON.stringify(graph.coverage, null, 2)}</pre></details>
      {step && <div className="semantic-step"><h3>{step.label}</h3><p>{step.purpose.text}</p>{selectedModule && <SemanticModuleContent module={selectedModule} />}<pre>{JSON.stringify({ kind: step.kind, purpose: step.purpose, source_node_ids: step.source_node_ids, run_ids: step.run_ids, artifact_ids: step.artifact_ids, requirements: step.requirements }, null, 2)}</pre><div className="semantic-toolbar">{step.evidence_ids.map(id => <button className="button" key={id} disabled={busy} onClick={() => void act(async signal => { const result = await parserRequest(`${prefix}/semantic-graphs/${graph.graph_id}/evidence/${encodeURIComponent(id)}?revision_id=${encodeURIComponent(graph.revision_id)}`, { signal }); if (!signal.aborted) setEvidence(result); })}>Evidence {id}</button>)}</div></div>}
      {evidence !== undefined && <pre aria-label="来源证据">{JSON.stringify(evidence, null, 2)}</pre>}
      <details><summary>变量建议（待审核）· {graph.variables.length}</summary><pre>{JSON.stringify(graph.variables, null, 2)}</pre></details></>}
  </section>;
}
