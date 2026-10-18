import { ExternalLink } from 'lucide-react';
import { safeExternalUri, type Archive } from './archiveViewModel';

export interface ProvenanceAndLinksBarProps { archive: Archive; showEvidence?: boolean }

function platform(uri: string): string {
  const host = new URL(uri).hostname.replace(/^www\./, '');
  if (host === 'github.com' || host.endsWith('.github.com')) return 'GitHub';
  if (host === 'bilibili.com' || host.endsWith('.bilibili.com') || host === 'b23.tv') return 'Bilibili';
  if (host === 'youtube.com' || host.endsWith('.youtube.com') || host === 'youtu.be') return 'YouTube';
  if (host === 'huggingface.co' || host.endsWith('.huggingface.co')) return 'Hugging Face';
  return host;
}

export function ProvenanceAndLinksBar({ archive, showEvidence = false }: ProvenanceAndLinksBarProps) {
  const counts = new Map<string, number>();
  for (const node of archive.graph.nodes) counts.set(node.provenance.mode, (counts.get(node.provenance.mode) || 0) + 1);
  const evidence = [...new Set(archive.graph.nodes.flatMap(node => node.provenance.evidence.map(item => item.source)))];
  const links = [...new Set(archive.metadata.external_links)].flatMap(value => {
    const uri = safeExternalUri(value);
    return uri ? [{ uri, label: platform(uri) }] : [];
  });
  return <div className="aica-artifacts aica-provenance">
    <div className="aica-tags" aria-label="Provenance">{[...counts].map(([mode, count]) =>
      <span className={`aica-provenance-${mode}`} key={mode} title={`${count} ${mode} nodes`}>{mode[0].toUpperCase() + mode.slice(1)} · {count}</span>)}</div>
    <div className="aica-links">{links.map(link => <a key={link.uri} href={link.uri} target="_blank" rel="noopener noreferrer">
      {link.label}<ExternalLink size={13} aria-hidden="true" /></a>)}</div>
    {showEvidence && evidence.length > 0 && <details><summary>Evidence ({evidence.length})</summary><ul>
      {evidence.map(source => <li key={source}>{source}</li>)}</ul></details>}
  </div>;
}
