import { reuseStatistics, type Archive, type PreviewMode } from './archiveViewModel';

export function ReuseRatioBar({ archive, previewMode }: { archive: Archive; previewMode?: PreviewMode }) {
  const stats = reuseStatistics(archive, previewMode);
  const total = stats.reused + stats.recomputed + stats.rebuilt + stats.unknown;
  const percentage = total ? Math.round(stats.reused / total * 100) : 0;
  return <div className="aica-reuse" aria-label="Archive reuse summary">
    <div className="aica-reuse-heading"><span>Reuse Ratio</span><strong>{stats.reused}/{total} nodes reused</strong><span>{percentage}%</span></div>
    <div className="aica-reuse-track" role="progressbar" aria-label="Reuse Ratio" aria-valuemin={0} aria-valuemax={total || 1}
      aria-valuenow={stats.reused} aria-valuetext={`${stats.reused} of ${total} nodes reused (${percentage}%)`}>
      <span className="reuse-hit" style={{ width: `${total ? stats.reused / total * 100 : 0}%` }} />
      <span className="reuse-compute" style={{ width: `${total ? stats.recomputed / total * 100 : 0}%` }} />
      <span className="reuse-build" style={{ width: `${total ? stats.rebuilt / total * 100 : 0}%` }} />
    </div>
    <div className="aica-statistics" aria-label="Reuse statistics">
      <span><strong>{stats.reused}</strong> reused</span><span><strong>{stats.recomputed}</strong> recomputed</span>
      <span><strong>{stats.rebuilt}</strong> rebuilt</span><span><strong>{stats.unknown}</strong> unverified</span>
    </div>
  </div>;
}
