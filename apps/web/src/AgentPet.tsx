import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, GitBranch, Send, Sparkles, X } from 'lucide-react';

export default function AgentPet({ projectTitle, openGraph }: { projectTitle?: string; openGraph: () => void }) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<{ role: 'user' | 'assistant'; text: string }[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);
  useEffect(() => { if (open && messages.length) endRef.current?.scrollIntoView({ block: 'nearest' }); }, [messages, open]);
  function close() { setOpen(false); buttonRef.current?.focus(); }
  function ask(text: string) {
    if (!text.trim()) return;
    const q = text.trim().slice(0, 500);
    const answer = /缓存|cache|重算/i.test(q) ? '展开作品模块，Fork 后编辑提示词。界面会按依赖关系解释修改影响。CACHE HIT 表示可以复用；RECOMPUTE 表示需要重算；REBUILD 表示重新组合产物。目前这些决策由本地演示器模拟。'
      : /fork|修改|角色|变量/i.test(q) ? '进入作品详情，点击「Fork & Remix」，展开模块并编辑提示词，再生成预览。也可以在「我的创造」上传 MCP 解析的记录。模型与 API 在「我的设置」中配置；填写模型 API Key 或配置团队网关后可生成真实预览；两者都未配置时保留模拟预览。'
      : /graph|流程|图|路径/i.test(q) ? 'Creation Graph 保存主干和失败尝试。点击节点查看细节，拖动和缩放探索依赖；Golden Path 会高亮推荐复现路径。下方按钮可以直接打开示例。'
      : /收藏|搜索|作品/i.test(q) ? '向下浏览社区作品，用分类和关键词筛选；点击心形收藏到本机，在顶部「我的收藏」中回看。点击作品封面即可进入详情。'
      : '我现在是本地规则助手，还没有接入语言模型。我可以说明 Graph、Fork、缓存和社区收藏的操作。你可以试着问「如何 Fork？」或「什么是缓存复用？」。';
    setMessages(current => [...current.slice(-18), { role: 'user', text: q }, { role: 'assistant', text: answer }]); setInput('');
  }
  return <div className={`agent-widget ${open ? 'is-open' : ''}`}>
    {open && <section id="agent-panel" className="agent-panel" role="region" aria-label="AiCA Agent 助手" onKeyDown={event => { if (event.key === 'Escape') close(); }}><div className="agent-panel-header"><span className="agent-status" /><div><strong>Orbit <span>/ AiCA Agent</span></strong><small>本地操作助手 · 未接入模型</small></div><button className="icon-button" aria-label="关闭助手" onClick={close}><X size={17} /></button></div><div className="agent-context"><GitBranch size={12} />{projectTitle ? `正在查看：${projectTitle}` : '探索社区中的下一份灵感'}</div><div className="agent-messages"><div className="agent-welcome"><span>✦</span><h3>灵感，需要一个搭子。</h3><p>我是 Orbit。想了解这个作品如何诞生，还是准备开始你的第一次 Fork？</p></div>{messages.length === 0 && <div className="agent-suggestions">{['如何 Fork 一个作品？', '什么是缓存复用？', '如何探索 Graph？'].map(text => <button key={text} onClick={() => ask(text)}>{text}<ArrowUpRight size={13} /></button>)}</div>}{messages.map((message, index) => <div key={index} className={`agent-message ${message.role}`}>{message.text}</div>)}<div ref={endRef} /></div><button className="agent-graph-link" onClick={() => { openGraph(); close(); }}><GitBranch size={14} />{projectTitle ? '回到当前作品 Graph' : '打开示例 Graph'}<ArrowUpRight size={14} /></button><form className="agent-input" onSubmit={e => { e.preventDefault(); ask(input); }}><input ref={inputRef} aria-label="向助手提问" placeholder="问问 Graph、Fork 或缓存…" value={input} maxLength={500} onChange={e => setInput(e.target.value)} /><button aria-label="发送消息" disabled={!input.trim()}><Send size={16} /></button></form><p className="agent-disclaimer">本地规则回复，不执行任务或调用模型。</p></section>}
    <button ref={buttonRef} className="pet-button" aria-label={open ? '收起 Agent 助手' : '打开 Agent 助手'} aria-expanded={open} aria-controls={open ? 'agent-panel' : undefined} onClick={() => open ? close() : setOpen(true)}><span className="pet-orbit" /><span className="pet-body"><span className="pet-antenna" /><span className="pet-ear left" /><span className="pet-ear right" /><span className="pet-face"><i /><i /><b /></span><span className="pet-foot left" /><span className="pet-foot right" /></span><span className="pet-online" /></button><span className="pet-tooltip" id="agent-tooltip"><Sparkles size={12} />和 Orbit 聊聊</span>
  </div>;
}
