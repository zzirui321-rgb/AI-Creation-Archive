import { useEffect, useLayoutEffect, useRef } from 'react';

function writeText(root: HTMLElement, value: string) {
  const fragment = document.createDocumentFragment();
  const Segmenter = (Intl as any).Segmenter;
  const words: { segment: string; isWordLike: boolean }[] = Segmenter ? [...new Segmenter('zh', { granularity: 'word' }).segment(value)] : (value.match(/[\p{L}\p{N}_]+|[^\p{L}\p{N}_]+/gu) ?? []).map(segment => ({ segment, isWordLike: /[\p{L}\p{N}]/u.test(segment) }));
  for (const word of words) {
    if (!word.isWordLike) { fragment.append(document.createTextNode(word.segment)); continue; }
    const span = document.createElement('span'); span.dataset.word = word.segment; span.textContent = word.segment; fragment.append(span);
  }
  root.replaceChildren(fragment);
}

export default function PromptEditor({ value, disabled, highlight, activeWord, onWord, onChange, label = '模块提示词', placeholder = '直接在这里填写或修改创作描述…' }: { value: string; disabled: boolean; highlight: boolean; activeWord: string; onWord: (word: string) => void; onChange: (value: string) => void; label?: string; placeholder?: string }) {
  const root = useRef<HTMLDivElement>(null);
  const composing = useRef(false);
  useLayoutEffect(() => {
    if (root.current && !composing.current && root.current.innerText !== value) writeText(root.current, value);
  }, [value]);
  useEffect(() => {
    root.current?.querySelectorAll<HTMLElement>('[data-word]').forEach(word => word.classList.toggle('word-active', highlight && !!activeWord && word.textContent?.toLocaleLowerCase() === activeWord.toLocaleLowerCase()));
  }, [activeWord, highlight, value]);
  function commit() { if (root.current && !disabled && !composing.current) onChange(root.current.innerText.replace(/\r\n/g, '\n')); }
  return <div ref={root} className={`prompt-words prompt-inline-editor ${disabled ? 'is-readonly' : ''}`} role="textbox" aria-label={label} aria-multiline="true" aria-readonly={disabled} tabIndex={0} contentEditable={disabled ? false : 'plaintext-only'} suppressContentEditableWarning data-placeholder={placeholder} spellCheck={false}
    onInput={commit} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; commit(); }}
    onPointerMove={event => { const span = (event.target as HTMLElement).closest<HTMLElement>('[data-word]'); if (highlight && span && root.current?.contains(span)) onWord(span.textContent ?? ''); }}
    onPointerLeave={() => onWord('')} onMouseUp={() => { const selection = window.getSelection(); if (highlight && selection?.anchorNode && root.current?.contains(selection.anchorNode) && selection.toString().trim()) onWord(selection.toString().trim()); }}
    onBlur={() => { if (root.current && !composing.current) writeText(root.current, root.current.innerText); onWord(''); }} />;
}
