import { Layers3, Square } from 'lucide-react';
import type { Workflow } from './workflow';

export default function IncrementalReuse({ workflow, decisions, runningId, onCancel }: { workflow: Workflow; decisions: Record<string, string> | null; runningId?: string; onCancel: () => void }) {
  const total = workflow.modules.filter(n => !n.dead).length;
  const hits = decisions ? Object.values(decisions).filter(value => value === 'CACHE HIT').length : 0;
  return <section className="execution-bar restored-execution-bar" aria-label="增量复用"><div><span className="execution-icon"><Layers3 size={19} /></span><div><strong>{runningId ? `正在预览 · ${workflow.modules.find(n => n.id === runningId)?.title ?? runningId}` : decisions ? `${hits} / ${total} 个步骤可复用` : '只重算真正改变的部分'}</strong><p>{decisions ? `${total - hits} 个步骤需执行 · 试错分支不参与重放` : '修改变量后，查看缓存复用与下游影响。'}</p></div></div>{runningId ? <button className="button" onClick={onCancel}><Square size={13} />取消</button> : <span className="small-badge">INCREMENTAL REPLAY</span>}</section>;
}
