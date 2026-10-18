import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Sparkles, X, Zap } from 'lucide-react';
import {
  ArchiveDiffViewer, ArtifactRenderer, ExploreArchiveCard, ProvenanceAndLinksBar, artifactMetadata,
  cacheDecision, decisionLabel, latestRuns, primaryArtifacts, replayNodes,
  type Archive, type Artifact, type PreviewMode,
} from '../src';
import '../src/styles.css';
import './review.css';

interface ReviewData { original: Archive; fork: Archive; extras: Artifact[] }

function reviewUri(artifact: Artifact): string {
  const uri = artifact.preview_uri || artifact.uri;
  return uri.startsWith('artifacts/') ? `/demo/generated/${uri}` : uri;
}

function ExecutionView({ archive, mode }: { archive: Archive; mode: PreviewMode }) {
  const runs = latestRuns(archive, mode);
  return <section className="aica-artifacts review-execution" aria-label="Execution graph">
    <h2>Creation Graph</h2><div className="review-golden-path"><strong>Golden Path</strong>
      <span>Recommended from successful history</span><p>{archive.graph.golden_path.join(' → ')}</p></div>
    <div className="aica-table-scroll"><table><thead><tr><th>Node</th><th>State</th><th>Dependencies</th><th>Provenance</th><th>Branch</th></tr></thead>
      <tbody>{archive.graph.nodes.map(node => {
        const run = runs.get(node.id);
        const branch = archive.graph.branches.find(item => item.id === node.branch_id);
        const dependencies = archive.graph.edges.filter(edge => edge.target === node.id).map(edge => edge.source);
        return <tr key={node.id} data-node-id={node.id}><th scope="row">{node.label}</th><td>
          <span className={`aica-state state-${cacheDecision(run)}`}>{branch?.state === 'dead_end' ? 'DEAD END' : decisionLabel(cacheDecision(run))}</span>
          {run?.error && <p className="review-error-code">{run.error}</p>}</td><td>{dependencies.join(', ') || 'Root'}</td>
          <td>{node.provenance.mode}</td><td>{branch?.state}{branch?.reason && <p>{branch.reason}</p>}</td></tr>;
      })}</tbody></table></div>
  </section>;
}

function Review() {
  const [data, setData] = useState<ReviewData>();
  const [error, setError] = useState('');
  const [tab, setTab] = useState('Artifacts');
  const [notice, setNotice] = useState('');
  const [mode, setMode] = useState<PreviewMode>('draft');
  const [selectedArchive, setSelectedArchive] = useState<'original' | 'fork'>('fork');
  useEffect(() => {
    fetch('/demo/generated/archives.json').then(response => {
      if (!response.ok) throw new Error('Run the offline review fixture builder first.');
      return response.json();
    }).then(setData).catch(reason => setError(String(reason)));
  }, []);
  if (error) return <main><h1>AiCA</h1><p role="alert">{error}</p></main>;
  if (!data) return <main><h1>AiCA</h1><p role="status">Loading archives...</p></main>;
  const activeArchive = data[selectedArchive];
  const mainArtifact = primaryArtifacts(activeArchive, mode)[0];
  const metadata = artifactMetadata(activeArchive, mainArtifact);
  const activeRuns = latestRuns(activeArchive, mode);
  const supporting = ['story', 'music'].flatMap(node => (activeRuns.get(node)?.artifact_ids || [])
    .flatMap(id => activeArchive.artifacts.filter(item => item.id === id)));
  return <><header className="review-header"><strong>AiCA</strong><span>Artifact Review</span>
    <span className="review-status">Synthetic offline fixture</span></header>
    <main><div className="review-title"><div><p className="review-eyebrow">EXECUTABLE CREATION ARCHIVE</p><h1>Creation study</h1></div>
      <div className="review-tags"><span>{replayNodes(activeArchive).length} active nodes</span><span>1 dead-end branch</span></div></div>
      <div className="review-stage-switch" role="group" aria-label="Execution stage">
        <button type="button" aria-pressed={mode === 'draft'} onClick={() => setMode('draft')}><Zap size={16} />Stage 1: Draft Preview</button>
        <button type="button" aria-pressed={mode === 'high_quality'} onClick={() => setMode('high_quality')}><Sparkles size={16} />Stage 2: High-quality Replay (Final)</button>
      </div>
      <nav aria-label="Review views">{['Artifacts', 'Original / Fork', 'Execution', 'Explore'].map(label =>
        <button key={label} type="button" aria-current={tab === label ? 'page' : undefined} onClick={() => setTab(label)}>{label}</button>)}</nav>
      {notice && <p role="status" className="review-notice">{notice}<button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={16} /></button></p>}
      {tab === 'Artifacts' && <><div className="review-artifact-layout"><section><h2>Composite artifact</h2>
        <ArtifactRenderer artifact={mainArtifact} archive={activeArchive} previewMode={mode} resolveUri={reviewUri} />
      </section><aside><h2>Archive</h2><dl><dt>Revision</dt><dd>{activeArchive.graph.revision_id}</dd><dt>Format</dt><dd>.aica v0.1</dd>
        <dt>Output</dt><dd>{mainArtifact.mime_type}</dd><dt>Canvas</dt><dd>{metadata.canvas?.width} x {metadata.canvas?.height}</dd>
        <dt>SHA256</dt><dd className="review-hash">{mainArtifact.sha256}</dd></dl>
        <ProvenanceAndLinksBar archive={activeArchive} showEvidence /></aside></div>
        <section className="review-multimodal"><h2>Additional artifacts</h2><div className="review-extra-grid">
          {supporting.map(item => <div key={item.id}><h3>{item.mime_type}</h3><ArtifactRenderer artifact={item}
            archive={activeArchive} previewMode={mode} showReuseRatio={false} resolveUri={reviewUri} /></div>)}
          {data.extras.map(item => <div key={item.id}><h3>{item.mime_type}</h3><ArtifactRenderer artifact={item}
            metadata={{ source: 'local_materialize' }} resolveUri={reviewUri} /></div>)}
        </div></section></>}
      {tab === 'Original / Fork' && <ArchiveDiffViewer originalArchive={data.original} forkedArchive={data.fork} previewMode={mode} resolveUri={reviewUri} />}
      {tab === 'Execution' && <ExecutionView archive={activeArchive} mode={mode} />}
      {tab === 'Explore' && <div className="review-explore-grid">{[data.original, data.fork].map(archive =>
        <ExploreArchiveCard key={archive.metadata.project_id} archive={archive} previewMode={mode} resolveUri={reviewUri} onOpen={item => {
          setSelectedArchive(item.metadata.project_id === data.original.metadata.project_id ? 'original' : 'fork');
          setTab('Artifacts');
        }} onFork={item => setNotice(`Fork requested: ${item.metadata.title}`)} />)}</div>}
    </main></>;
}

createRoot(document.getElementById('root')!).render(<Review />);
