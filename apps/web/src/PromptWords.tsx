import { useMemo } from 'react';

export default function PromptWords({ text, activeWord, onWord, enabled }: { text: string; activeWord: string; onWord: (word: string) => void; enabled: boolean }) {
  const segments = useMemo(() => {
    const Segmenter = (Intl as any).Segmenter;
    if (Segmenter) return Array.from(new Segmenter('zh', { granularity: 'word' }).segment(text)) as { segment: string; isWordLike: boolean }[];
    return (text.match(/[\p{L}\p{N}_]+|[^\p{L}\p{N}_]+/gu) ?? []).map(segment => ({ segment, isWordLike: /[\p{L}\p{N}]/u.test(segment) }));
  }, [text]);
  return <div className="prompt-words" aria-label="提示词逐词阅读" onPointerLeave={() => onWord('')} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) onWord(''); }}>{segments.length ? segments.map((s, i) => enabled && s.isWordLike ? <span key={i} tabIndex={0} role="button" aria-label={`高亮词语：${s.segment}`} className={s.segment.toLocaleLowerCase() === activeWord.toLocaleLowerCase() ? 'word-active' : ''} onPointerEnter={() => onWord(s.segment)} onFocus={() => onWord(s.segment)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onWord(s.segment); } }}>{s.segment}</span> : <span key={i}>{s.segment}</span>) : <span className="muted">文件未提供此模块的提示词。可以在自己的副本中补充。</span>}</div>;
}
