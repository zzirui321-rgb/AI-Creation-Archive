import { ArrowUpRight, GitFork } from 'lucide-react';
import { ArtifactRenderer } from './ArtifactRenderer';
import { ProvenanceAndLinksBar } from './ProvenanceAndLinksBar';
import { builtWith, primaryArtifacts, safeExternalUri, type Archive, type PreviewMode, type UriResolver } from './archiveViewModel';

export interface ExploreArchiveCardProps {
  archive: Archive;
  href?: string;
  onOpen?: (archive: Archive) => void;
  onFork?: (archive: Archive) => void;
  resolveUri?: UriResolver;
  previewMode?: PreviewMode;
}

export function ExploreArchiveCard({ archive, href, onOpen, onFork, resolveUri, previewMode }: ExploreArchiveCardProps) {
  const preview = primaryArtifacts(archive, previewMode)[0];
  const link = href && (href.startsWith('/') && !href.startsWith('//') && !href.includes('\\') ? href : safeExternalUri(href));
  return <article className="aica-artifacts aica-explore-card">
    <div className="aica-card-preview">{preview ? <ArtifactRenderer artifact={preview} archive={archive} previewMode={previewMode} resolveUri={resolveUri} interactive={false} showReuseRatio={false} />
      : <div className="aica-empty">No preview artifact</div>}</div>
    <div className="aica-card-body"><div className="aica-card-heading"><h3>{archive.metadata.title}</h3>
      {onOpen ? <button type="button" onClick={() => onOpen(archive)} aria-label={`Open ${archive.metadata.title}`} title="Open archive"><ArrowUpRight size={18} /></button>
        : link && <a href={link} className="aica-icon" aria-label={`Open ${archive.metadata.title}`} title="Open archive"><ArrowUpRight size={18} /></a>}
      {onFork && <button type="button" onClick={() => onFork(archive)} aria-label={`Fork ${archive.metadata.title}`} title="Fork archive"><GitFork size={18} /></button>}
    </div><p className="aica-muted">{archive.metadata.creator || 'Unknown creator'}</p>
    <div className="aica-tags" aria-label="Built with">{builtWith(archive).map(tag => <span key={tag}>{tag}</span>)}</div>
    <ProvenanceAndLinksBar archive={archive} /></div>
  </article>;
}

export { ExploreArchiveCard as ExploreCard };
