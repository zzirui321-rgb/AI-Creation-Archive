import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Crosshair, Upload, Zap, Sparkles, ArrowRight, RotateCcw } from 'lucide-react';
import { ArtifactRenderer, EditTargetPicker, type Artifact, type EditIntent, type SemanticEditProposal } from '../src';
import '../src/styles.css';
import './semantic-edit.css';

async function api<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`/edit-api/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || value.code || 'Request failed');
  return value;
}

async function encoded(file: Blob): Promise<string> {
  if (file.size > 20 * 1024 * 1024) throw new Error('Image exceeds 20 MB');
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',', 2)[1]); reader.onerror = reject; reader.readAsDataURL(file); });
}

function Review() {
  const [source, setSource] = useState<Artifact>();
  const [proposal, setProposal] = useState<SemanticEditProposal>();
  const [result, setResult] = useState<{ artifact: Artifact; metadata: Record<string, unknown> }>();
  const [operation, setOperation] = useState<EditIntent['operation']>('replace');
  const [targets, setTargets] = useState('fox');
  const [instruction, setInstruction] = useState('Replace the fox with a small mechanical robot, watercolor illustration');
  const [stage, setStage] = useState<'draft' | 'final'>('draft');
  const [provider, setProvider] = useState('sdcpp');
  const [endpoint, setEndpoint] = useState('http://127.0.0.1:8000/v1/images/generations');
  const [model, setModel] = useState('flux3-image-klein');
  const [apiKey, setApiKey] = useState('');
  const [newContent, setNewContent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const upload = useRef<HTMLInputElement>(null);
  const revision = useRef(0);
  const proposalIdentity = useRef('');
  proposalIdentity.current = JSON.stringify({ source: source?.sha256, operation, targets, instruction });
  useEffect(() => { setProposal(undefined); setResult(undefined); revision.current += 1; }, [source?.sha256, operation, targets, instruction]);

  async function load(file: Blob) {
    const expected = ++revision.current;
    setBusy(true); setError('');
    try {
      const data = await api<{ artifact: Artifact }>('images', { image_base64: await encoded(file) });
      if (expected === revision.current) { setSource(data.artifact); setProposal(undefined); setResult(undefined); }
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }

  useEffect(() => {
    let active = true;
    void fetch('/demo/generated/semantic-edit/input.png').then(response => { if (!response.ok) return; return response.blob(); })
      .then(blob => { if (active && blob) void load(blob); }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  async function detect() {
    if (!source) return;
    const identity = proposalIdentity.current;
    setBusy(true); setError(''); setProposal(undefined); setResult(undefined);
    try {
      const data = await api<SemanticEditProposal>('proposals', { source_id: source.id, intent: { operation, instruction, targets: targets.split(',').map(item => item.trim()).filter(Boolean) } });
      if (identity === proposalIdentity.current) setProposal(data);
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }

  async function execute(selection: { proposalId: string; candidateId: string; sourceSha256: string }) {
    if (!source || source.sha256 !== selection.sourceSha256) return;
    const identity = proposalIdentity.current;
    setBusy(true); setError('');
    try {
      const value = await api<{ artifact: Artifact; metadata: Record<string, unknown> }>('execute', {
        source_id: source.id, proposal_id: selection.proposalId, candidate_id: selection.candidateId, stage, new_content: newContent,
        image_config: { provider, api_base: endpoint, api_key: apiKey, model, timeout_seconds: 180, allow_fallback: false },
      });
      if (identity === proposalIdentity.current) setResult(value);
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  }

  return <div className="semantic-review"><header><strong>AiCA</strong><span>Image Edit Review</span><span className="semantic-local">Local B workspace</span></header>
    <main><aside className="semantic-controls"><h1>Image Edit</h1>
      <input ref={upload} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void load(file); }} />
      <button type="button" disabled={busy} onClick={() => upload.current?.click()}><Upload size={16} />Open image</button>
      <label>Operation<select value={operation} disabled={busy} onChange={event => setOperation(event.target.value as EditIntent['operation'])}>
        <option value="replace">Replace object</option><option value="attribute">Edit attribute</option><option value="background">Replace background</option><option value="append">Append object</option>
      </select></label>
      <label>{operation === 'background' || operation === 'append' ? 'Objects to preserve' : 'Target object'}<input value={targets} disabled={busy} onChange={event => setTargets(event.target.value)} /></label>
      <label>Edit instruction<textarea aria-label="Edit instruction" value={instruction} disabled={busy} onChange={event => setInstruction(event.target.value)} rows={5} /></label>
      <button type="button" disabled={busy || !source || !instruction.trim()} onClick={() => void detect()}><Crosshair size={16} />{busy ? 'Processing...' : 'Locate targets'}</button>
      <div className="semantic-stages" role="group" aria-label="Preview stage"><button type="button" disabled={busy} aria-pressed={stage === 'draft'} onClick={() => setStage('draft')}><Zap size={16} />Draft</button>
        <button type="button" disabled={busy} aria-pressed={stage === 'final'} onClick={() => setStage('final')}><Sparkles size={16} />Final</button></div>
      <label className="semantic-checkbox"><input type="checkbox" checked={newContent} disabled={busy} onChange={event => setNewContent(event.target.checked)} />Generate new region content</label>
      <details><summary>Image provider</summary><label>Provider<select value={provider} disabled={busy} onChange={event => setProvider(event.target.value)}><option value="sdcpp">Local sdcpp</option><option value="openai">OpenAI compatible</option><option value="local_fallback">Local Fallback</option></select></label>
        <label>Endpoint<input value={endpoint} disabled={busy} onChange={event => setEndpoint(event.target.value)} /></label><label>Model<input value={model} disabled={busy} onChange={event => setModel(event.target.value)} /></label>
        <label>API key<input type="password" autoComplete="off" value={apiKey} disabled={busy} onChange={event => setApiKey(event.target.value)} /></label></details>
    </aside><section className="semantic-workspace">{error && <p className="semantic-error" role="alert">{error}</p>}
      <div className="semantic-columns"><div><h2>Original / Target</h2>{source && proposal ? <EditTargetPicker artifact={source} proposal={proposal} busy={busy} onConfirm={selection => void execute(selection)} onRetry={() => void detect()} />
        : source ? <ArtifactRenderer artifact={source} metadata={{ source: 'imported_verified' }} /> : <div className="semantic-empty">No image selected</div>}</div>
      <div><h2>Result</h2>{result ? <ArtifactRenderer artifact={result.artifact} metadata={result.metadata} previewMode={stage === 'draft' ? 'draft' : 'high_quality'} /> : <div className="semantic-empty">{busy ? 'Processing...' : 'No edit yet'}</div>}
        {result && <><dl className="semantic-receipt"><dt>Source</dt><dd>{String(result.metadata.source)}</dd><dt>Execution</dt><dd>{String((result.metadata.provider_cache as { decision?: string })?.decision || '')}</dd><dt>Protected pixels</dt><dd>{Number(result.metadata.protected_pixel_count).toLocaleString()}</dd><dt>SHA256</dt><dd>{result.artifact.sha256}</dd></dl>
          <button type="button" disabled={busy} onClick={() => { setSource(result.artifact); setResult(undefined); setProposal(undefined); }}><ArrowRight size={16} />Continue from result</button></>}
      </div></div>{source && <div className="semantic-source"><span>Source SHA256</span><code>{source.sha256}</code><button type="button" disabled={busy} title="Clear proposal" aria-label="Clear proposal" onClick={() => { setProposal(undefined); setResult(undefined); }}><RotateCcw size={16} /></button></div>}
    </section></main></div>;
}

createRoot(document.getElementById('root')!).render(<Review />);
