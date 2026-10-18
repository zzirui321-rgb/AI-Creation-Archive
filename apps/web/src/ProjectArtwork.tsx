import { usePlatformMedia } from './platform-media';
import Artwork from './Artwork';
import GalleryArt from './GalleryArt';
import ArchiveArtwork from './ArchiveArtwork';
import { safeMedia } from './workflow';
import type { CommunityProject } from './community';
import type { Values } from './demo';

export default function ProjectArtwork({ project, values, compact = false }: { project: CommunityProject & { coverArtifact?: { uri: string; mime: string } }; values: Values; compact?: boolean }) {
  const mediaUrl = usePlatformMedia(project.coverArtifact?.uri);
  if (project.coverArtifact && safeMedia(project.coverArtifact.uri, project.coverArtifact.mime)) {
    if (project.coverArtifact.mime.startsWith('image/')) return <img className="creation-result-cover" src={mediaUrl || undefined} alt={`${project.title} 的确认结果`} loading="lazy" />;
    if (project.coverArtifact.mime.startsWith('video/')) return <video className="creation-result-cover" src={mediaUrl || undefined} title={project.title} preload="metadata" muted playsInline />;
  }
  if (project.english === 'My Creation Archive') return <ArchiveArtwork title={project.title} />;
  if (project.theme === 'neon') return <Artwork values={values} compact={compact} />;
  return <GalleryArt theme={project.theme} accent={project.accent} variant={values.character !== project.values.character || values.style !== project.values.style || values.detail !== project.values.detail} label={values.character} />;
}
