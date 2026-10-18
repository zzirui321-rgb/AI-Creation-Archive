import { ArtifactRenderer } from './ArtifactRenderer';
import {
  cacheDecision, decisionLabel, latestRuns, primaryArtifacts, variableChanges,
  type Archive, type PreviewMode, type TextLoader, type UriResolver,
} from './archiveViewModel';
import { ReuseRatioBar } from './ReuseRatioBar';

export interface ArchiveDiffViewerProps {
  originalArchive: Archive;
  forkedArchive: Archive;
  previewMode?: PreviewMode;
  resolveUri?: UriResolver;
  loadText?: TextLoader;
  nodeMapping?: Record<string, string>;
}

export function ArchiveDiffViewer({ originalArchive, forkedArchive, previewMode, resolveUri, loadText,
  nodeMapping = {} }: ArchiveDiffViewerProps) {
  const changes = variableChanges(originalArchive, forkedArchive);
  const originalRuns = latestRuns(originalArchive, previewMode);
  const forkRuns = latestRuns(forkedArchive, previewMode);
  const originalNodes = new Map(originalArchive.graph.nodes.map(node => [nodeMapping[node.id] || node.id, node]));
  const rows = forkedArchive.graph.nodes.filter(node => node.execution).map(node => ({
    id: node.id, label: node.label, original: originalNodes.get(node.id), fork: node,
  }));
  const forkIds = new Set(forkedArchive.graph.nodes.map(node => node.id));
  const removed = originalArchive.graph.nodes.filter(node => node.execution && !forkIds.has(nodeMapping[node.id] || node.id));
  return <section className="aica-artifacts aica-diff" aria-label="Original and Fork comparison">
    <ReuseRatioBar archive={forkedArchive} previewMode={previewMode} />
    <div className="aica-diff-columns">{[
      { label: 'Original', archive: originalArchive }, { label: 'Fork', archive: forkedArchive },
    ].map(({ label, archive }) => {
      const artifacts = primaryArtifacts(archive, previewMode);
      return <div className="aica-diff-column" key={label}><h3>{label}</h3><p className="aica-muted">{archive.metadata.title}</p>
        {artifacts.length ? artifacts.map(artifact => <ArtifactRenderer key={artifact.id} artifact={artifact} archive={archive}
          previewMode={previewMode} resolveUri={resolveUri} loadText={loadText} showReuseRatio={false} />) : <div className="aica-empty">No completed artifact in this stage</div>}
      </div>;
    })}</div>
    {changes.length > 0 && <div className="aica-table-scroll"><table><caption>Variables</caption>
      <thead><tr><th>Variable</th><th>Original</th><th>Fork</th><th>Operations</th></tr></thead>
      <tbody>{changes.map(change => <tr key={change.name}><th scope="row">{change.name}</th>
        <td>{JSON.stringify(change.before) ?? '(absent)'}</td><td>{JSON.stringify(change.after) ?? '(absent)'}</td>
        <td>{change.operations.join(' → ') || 'Value changed'}</td></tr>)}</tbody></table></div>}
    <div className="aica-table-scroll"><table><caption>Execution changes</caption>
      <thead><tr><th>Node</th><th>State</th><th>Original</th><th>Fork</th></tr></thead>
      <tbody>{rows.map(row => {
        const originalNode = row.original;
        const originalRun = originalNode ? originalRuns.get(originalNode.id) : undefined;
        const forkRun = forkRuns.get(row.id);
        const originalHashes = originalArchive.artifacts.filter(item => originalRun?.artifact_ids.includes(item.id)).map(item => item.sha256).sort();
        const forkHashes = forkedArchive.artifacts.filter(item => forkRun?.artifact_ids.includes(item.id)).map(item => item.sha256).sort();
        const sameContent = originalHashes.length > 0 && originalHashes.every(Boolean) && forkHashes.every(Boolean)
          && JSON.stringify(originalHashes) === JSON.stringify(forkHashes);
        const decision = cacheDecision(forkRun);
        const branch = forkedArchive.graph.branches.find(item => item.id === row.fork.branch_id);
        return <tr key={row.id} data-node-id={row.id}><th scope="row">{row.label}</th>
          <td><span className={`aica-state state-${decision}`}>{branch?.state === 'dead_end' ? 'DEAD END' : decisionLabel(decision)}</span>
            {branch?.reason && <details><summary>Branch reason</summary>{branch.reason}</details>}</td>
          <td>{originalRun?.state || (originalNode ? 'No execution' : 'Added node')}</td>
          <td>{forkRun?.state || 'No execution'}{forkRun?.state === 'succeeded' && ` / ${sameContent ? 'same content' : 'changed content'}`}</td></tr>;
      })}{removed.map(node => <tr key={`removed-${node.id}`}><th scope="row">{node.label}</th>
        <td>Removed</td><td>{originalRuns.get(node.id)?.state || 'No execution'}</td><td>Removed node</td></tr>)}</tbody></table></div>
  </section>;
}
