import { useEffect, useState } from 'react';
import { Download, Grid2X2, Image as ImageIcon, RefreshCw } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  artifactMetadata, artifactRun, cacheDecision, decisionLabel, defaultUriResolver, generationSource, latestRuns, safeMediaUri,
  type Archive, type Artifact, type PreviewMetadata, type PreviewMode, type TextLoader, type UriResolver,
} from './archiveViewModel';
import { ReuseRatioBar } from './ReuseRatioBar';

export interface ArtifactRendererProps {
  artifact: Artifact;
  archive?: Archive;
  previewMode?: PreviewMode;
  resolveUri?: UriResolver;
  loadText?: TextLoader;
  metadata?: PreviewMetadata;
  interactive?: boolean;
  onPanelSelect?: (artifact: Artifact) => void;
  className?: string;
  showReuseRatio?: boolean;
}

async function fetchText(uri: string, signal: AbortSignal): Promise<string> {
  const response = await fetch(uri, { signal });
  if (!response.ok) throw new Error(`Text request failed (${response.status})`);
  if (Number(response.headers.get('content-length')) > 2_000_000) throw new Error('Text preview exceeds 2 MB');
  if (!response.body) return response.text();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 2_000_000) {
        await reader.cancel();
        throw new Error('Text preview exceeds 2 MB');
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const combined = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { combined.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(combined);
}

function TextContent({ artifact, uri, loader }: { artifact: Artifact; uri?: string; loader?: TextLoader }) {
  const [state, setState] = useState<{ text?: string; error?: string }>({});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({});
    async function read() {
      try {
        let text: string;
        if (loader) text = await loader(artifact, controller.signal);
        else {
          if (!uri) throw new Error('No browser-accessible text URI');
          text = await fetchText(uri, controller.signal);
        }
        if (new TextEncoder().encode(text).byteLength > 2_000_000) throw new Error('Text preview exceeds 2 MB');
        if (!controller.signal.aborted) setState({ text });
      } catch (error) {
        if (!controller.signal.aborted) setState({ error: error instanceof Error ? error.message : 'Unable to load text' });
      }
    }
    void read();
    return () => controller.abort();
  }, [artifact.id, artifact.uri, artifact.sha256, uri, loader, attempt]);
  if (state.error) return <div className="aica-empty" role="alert">{state.error}
    <button type="button" title="Retry text preview" aria-label="Retry text preview" onClick={() => setAttempt(value => value + 1)}>
      <RefreshCw size={16} /></button></div>;
  if (state.text === undefined) return <div className="aica-empty" role="status">Loading artifact...</div>;
  if (artifact.mime_type === 'text/markdown') return <div className="aica-markdown">
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{state.text}</ReactMarkdown></div>;
  let text = state.text;
  if (artifact.mime_type === 'application/json') {
    try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* Preserve malformed source for inspection. */ }
  }
  return <pre className="aica-code"><code>{text}</code></pre>;
}

function Media({ artifact, uri, loadText }: { artifact: Artifact; uri?: string; loadText?: TextLoader }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [uri, artifact.sha256]);
  const mime = artifact.mime_type.split(';')[0].toLowerCase();
  if (artifact.availability === 'missing') return <div className="aica-empty" role="alert">Artifact is missing</div>;
  if ((mime.startsWith('text/') && mime !== 'text/html') || mime === 'application/json' || (!mime.startsWith('image/') && /javascript|xml|yaml/.test(mime))) {
    return <TextContent artifact={artifact} uri={uri} loader={loadText} />;
  }
  if (!uri) return <div className="aica-empty" role="alert">Artifact URI is not available in this browser</div>;
  if (failed) return <div className="aica-empty" role="alert">Unable to load artifact</div>;
  if (mime.startsWith('image/')) return <img className="aica-image" src={uri} alt={`Artifact ${artifact.id}`}
    loading="lazy" onError={() => setFailed(true)} />;
  if (mime.startsWith('video/')) return <video className="aica-video" src={uri} controls preload="metadata" onError={() => setFailed(true)} />;
  if (mime.startsWith('audio/')) return <audio className="aica-audio" src={uri} controls preload="metadata" onError={() => setFailed(true)} />;
  if (mime === 'text/html') return <iframe className="aica-web" src={uri} title={`Web artifact ${artifact.id}`} sandbox="" referrerPolicy="no-referrer" />;
  return <div className="aica-empty">{artifact.mime_type}<a href={uri} download>Download artifact</a></div>;
}

export function ArtifactRenderer({ artifact, archive, previewMode, resolveUri = defaultUriResolver, loadText,
  metadata: suppliedMetadata, interactive = true, onPanelSelect, className = '', showReuseRatio = true }: ArtifactRendererProps) {
  const [view, setView] = useState<'image' | 'panels'>('image');
  const [selectedId, setSelectedId] = useState<string>();
  useEffect(() => { setSelectedId(undefined); setView('image'); }, [artifact.id]);
  const metadata = suppliedMetadata || artifactMetadata(archive, artifact);
  const run = artifactRun(archive, artifact);
  const stage = run?.preview_stage || metadata.stage || previewMode;
  const uri = safeMediaUri(resolveUri(artifact));
  const downloadUri = safeMediaUri(resolveUri({ ...artifact, preview_uri: undefined }));
  const regions = metadata.regions || [];
  const layered = metadata.composition_mode === 'layer_stack' || metadata.composition_mode === 'masked_replace';
  const selected = regions.find(region => region.artifact_id === selectedId);
  const runs = archive ? latestRuns(archive, previewMode) : new Map();
  const source = generationSource(metadata);
  return <section className={`aica-artifacts aica-artifact ${className}`} aria-label={`Artifact ${artifact.id}`}>
    {archive && showReuseRatio && <ReuseRatioBar archive={archive} previewMode={previewMode} />}
    <div className="aica-toolbar">
      <span className={`aica-stage ${stage === 'draft' ? 'is-draft' : ''}`}>
        {stage === 'draft' ? 'Draft Preview' : stage ? 'High-Quality' : 'Archived artifact'}</span>
      <span className={`aica-source source-${source.tone}`}>{source.label}</span>
      {metadata.contains_substitute && <span className="aica-source source-fallback">Contains Local Fallback</span>}
      {run && <span className={`aica-state state-${cacheDecision(run)}`}>{decisionLabel(cacheDecision(run))}</span>}
      <span className="aica-spacer" />
      {regions.length > 0 && interactive && <div className="aica-segmented" role="group" aria-label="Artifact view">
        <button type="button" title="Composite image" aria-label="Composite image" aria-pressed={view === 'image'} onClick={() => setView('image')}><ImageIcon size={16} /></button>
        <button type="button" title={layered ? 'Individual layers' : 'Individual panels'} aria-label={layered ? 'Individual layers' : 'Individual panels'} aria-pressed={view === 'panels'} onClick={() => setView('panels')}><Grid2X2 size={16} /></button>
      </div>}
      {downloadUri && <a className="aica-icon" href={downloadUri} download title="Download artifact" aria-label="Download artifact"><Download size={16} /></a>}
    </div>
    {view === 'panels' && archive ? <div className="aica-panel-grid">{regions.map(region => {
      const child = archive.artifacts.find(item => item.id === region.artifact_id);
      const decision = cacheDecision(runs.get(region.node_id || ''));
      const childSource = child ? generationSource(artifactMetadata(archive, child)) : undefined;
      return <div className="aica-panel" key={region.artifact_id}>
        {child ? <Media artifact={child} uri={safeMediaUri(resolveUri(child))} loadText={loadText} /> : <span>Missing child artifact</span>}
        <span className={`aica-state state-${decision}`}>{decisionLabel(decision)}</span>
        {childSource && <span className={`aica-source source-${childSource.tone}`}>{childSource.label}</span>}
        <button type="button" className="aica-panel-label" aria-pressed={selectedId === region.artifact_id}
          onClick={() => { setSelectedId(region.artifact_id); if (child) onPanelSelect?.(child); }}>
          {region.role ? `${region.role}: ` : ''}{region.caption || region.node_id || region.artifact_id}
        </button>
      </div>;
    })}</div> : <Media artifact={artifact} uri={uri} loadText={loadText} />}
    {selected && view === 'panels' && <dl className="aica-panel-details">
      <dt>Node</dt><dd>{selected.node_id || 'Unspecified'}</dd>
      <dt>Prompt</dt><dd>{selected.prompt || 'Not recorded'}</dd>
      <dt>Region</dt><dd>{selected.rect.x}, {selected.rect.y} / {selected.rect.width} x {selected.rect.height}</dd>
    </dl>}
    {archive && metadata.attachment_artifact_ids?.map(id => {
      const related = archive.artifacts.find(item => item.id === id);
      if (!related) return null;
      const relatedSource = generationSource(artifactMetadata(archive, related));
      return <div className="aica-related-artifact" key={id}><div className="aica-toolbar">
        <span>{related.mime_type}</span><span className={`aica-source source-${relatedSource.tone}`}>{relatedSource.label}</span>
      </div><Media artifact={related} uri={safeMediaUri(resolveUri(related))} loadText={loadText} /></div>;
    })}
  </section>;
}
