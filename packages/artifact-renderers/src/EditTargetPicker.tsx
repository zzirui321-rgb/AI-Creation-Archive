import { useEffect, useState } from 'react';
import { Check, RotateCcw } from 'lucide-react';
import { defaultUriResolver, safeMediaUri, type Artifact, type UriResolver } from './archiveViewModel';

export interface EditIntent {
  operation: 'replace' | 'attribute' | 'background' | 'append';
  instruction: string;
  targets: string[];
}

export interface EditTargetCandidate {
  id: string;
  label: string;
  score: number | null;
  mask_artifact: Artifact;
  rect: [number, number, number, number];
  editable_pixel_count: number;
  provenance: { source: string; [key: string]: unknown };
}

export interface SemanticEditProposal {
  id: string;
  format: 'aica-edit-proposal/1';
  source_sha256: string;
  width: number;
  height: number;
  intent: EditIntent;
  status: 'ready' | 'needs_confirmation' | 'not_found' | 'unavailable' | 'needs_target';
  reason: string;
  candidates: EditTargetCandidate[];
}

export interface EditTargetPickerProps {
  artifact: Artifact;
  proposal: SemanticEditProposal;
  resolveUri?: UriResolver;
  busy?: boolean;
  onConfirm: (selection: { proposalId: string; candidateId: string; sourceSha256: string }) => void;
  onRetry?: () => void;
}

const statusLabels: Record<SemanticEditProposal['status'], string> = {
  ready: 'Target located', needs_confirmation: 'Confirm target', not_found: 'No matching target',
  unavailable: 'Segmentation unavailable', needs_target: 'Target description required',
};

export function EditTargetPicker({ artifact, proposal, resolveUri = defaultUriResolver, busy = false, onConfirm, onRetry }: EditTargetPickerProps) {
  const [selectedId, setSelectedId] = useState(proposal.status === 'ready' ? proposal.candidates[0]?.id : undefined);
  const [showMask, setShowMask] = useState(true);
  const [failedSource, setFailedSource] = useState<string>();
  const [failedMask, setFailedMask] = useState<string>();
  const [loadedSource, setLoadedSource] = useState<string>();
  const [loadedMask, setLoadedMask] = useState<string>();
  useEffect(() => {
    setSelectedId(proposal.status === 'ready' ? proposal.candidates[0]?.id : undefined);
    setFailedSource(undefined); setFailedMask(undefined);
  }, [proposal.id, artifact.sha256]);
  const selected = proposal.candidates.find(item => item.id === selectedId);
  const uri = safeMediaUri(resolveUri(artifact));
  const maskUri = selected ? safeMediaUri(resolveUri(selected.mask_artifact)) : undefined;
  const sourceLoadKey = `${artifact.sha256}:${uri}`;
  const maskLoadKey = `${proposal.id}:${maskUri}`;
  const stale = artifact.sha256 !== proposal.source_sha256;
  const eligible = ['ready', 'needs_confirmation'].includes(proposal.status) && !stale;
  const canConfirm = eligible && selected && uri && maskUri && loadedSource === sourceLoadKey && loadedMask === maskLoadKey && failedSource !== uri && failedMask !== maskUri && !busy;
  return <section className="aica-artifacts aica-edit-picker" aria-label="Image edit target">
    <div className="aica-toolbar">
      <strong>{stale ? 'Source image changed' : statusLabels[proposal.status]}</strong>
      <span className="aica-spacer" />
      <label className="aica-edit-toggle"><input type="checkbox" checked={showMask} onChange={event => setShowMask(event.target.checked)} />Mask</label>
      {onRetry && <button type="button" title="Detect targets again" aria-label="Detect targets again" disabled={busy} onClick={onRetry}><RotateCcw size={16} /></button>}
    </div>
    <div className="aica-edit-canvas" style={{ aspectRatio: `${proposal.width} / ${proposal.height}` }}>
      {uri ? <img key={sourceLoadKey} src={uri} alt="Original image" onLoad={() => setLoadedSource(sourceLoadKey)} onError={() => setFailedSource(uri)} /> : <p role="alert">Original image unavailable</p>}
      {eligible && selected && maskUri && <>
        <img key={maskLoadKey} className="aica-edit-mask-probe" src={maskUri} alt="" onLoad={() => setLoadedMask(maskLoadKey)} onError={() => setFailedMask(maskUri)} />
        {showMask && <span className="aica-edit-mask" aria-hidden="true" style={{ maskImage: `url("${maskUri.replaceAll('"', '%22')}")`, maskMode: 'luminance' }} />}
      </>}
      {eligible && proposal.candidates.map((candidate, index) => <button type="button" key={candidate.id}
        className="aica-edit-bound" aria-label={`Select ${candidate.label} ${index + 1}`} aria-pressed={selectedId === candidate.id}
        disabled={busy} onClick={() => setSelectedId(candidate.id)} title={candidate.label}
        style={{ left: `${candidate.rect[0] * 100}%`, top: `${candidate.rect[1] * 100}%`, width: `${candidate.rect[2] * 100}%`, height: `${candidate.rect[3] * 100}%` }}>
        <span>{index + 1}</span>
      </button>)}
    </div>
    {stale && <p role="alert">The proposal belongs to another image revision.</p>}
    {(failedSource === uri || (maskUri && failedMask === maskUri)) && <p role="alert">Image or mask unavailable</p>}
    {eligible && <div className="aica-edit-candidates" role="group" aria-label="Detected targets">{proposal.candidates.map((candidate, index) =>
      <button type="button" key={candidate.id} disabled={busy} aria-pressed={selectedId === candidate.id} onClick={() => setSelectedId(candidate.id)}>
        <strong>{index + 1}. {candidate.label}</strong>
        <span>{candidate.provenance.source === 'placement_proposal' ? 'Placement proposal' : 'Semantic segmentation'}</span>
        {candidate.score !== null && <span>Detector score {candidate.score.toFixed(2)}</span>}
      </button>)}</div>}
    <div className="aica-toolbar">
      <span>{selected ? `${selected.editable_pixel_count.toLocaleString()} editable pixels` : ''}</span>
      <span className="aica-spacer" />
      <button type="button" disabled={!canConfirm} onClick={() => { if (selected && canConfirm) onConfirm({ proposalId: proposal.id, candidateId: selected.id, sourceSha256: proposal.source_sha256 }); }}>
        <Check size={16} />{busy ? 'Executing...' : 'Confirm edit'}
      </button>
    </div>
  </section>;
}
