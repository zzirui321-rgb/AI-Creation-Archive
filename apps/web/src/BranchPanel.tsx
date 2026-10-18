import { useEffect, useState } from 'react';
import { platform, switchBranch } from './platform';
import type { WorkspaceProject } from './workspace';

export default function BranchPanel({ project, onChange }: { project: WorkspaceProject; onChange: (project: WorkspaceProject) => void }) {
  const [branches, setBranches] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { let cancelled = false; void platform.branches(project.id).then(data => { if (!cancelled) setBranches(data.branches.map(branch => branch.name)); }).catch(err => { if (!cancelled) setError((err as Error).message); }); return () => { cancelled = true; }; }, [project.id, project.branch]);
  async function select(branch: string) {
    setBusy(true); setError('');
    try { if (!window.confirm('切换分支前请确认当前编辑已保存。继续切换？')) return; onChange(await switchBranch(project, branch)); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function create() {
    setBusy(true); setError('');
    try { await platform.createBranch(project.id, name, project.branch ?? 'main'); setBranches(current => [...current, name]); setName(''); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="surface-section" style={{ margin: '16px 5%', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }} aria-label="仓库分支">
    <label>分支 <select aria-label="当前分支" disabled={busy} value={project.branch ?? 'main'} onChange={event => void select(event.target.value)}>{branches.map(branch => <option key={branch}>{branch}</option>)}</select></label>
    {project.owned && <><input aria-label="新分支名称" placeholder="experiment/style" value={name} maxLength={80} onChange={event => setName(event.target.value)} /><button className="button" disabled={busy || !name.trim()} onClick={() => void create()}>新建分支</button></>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
