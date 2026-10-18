import { useRef, useState } from 'react';
import { Crop, RotateCcw, X } from 'lucide-react';
import type { LocalEdit } from './workspace';
import { bounds, polygonArea, selectionPoints, type Point, type Selection } from './selection-geometry';

const clamp = (value: number) => Math.max(0, Math.min(1, value));
export default function LocalEditPanel({ sourceUri, model, onClose, onSubmit }: { sourceUri: string; model?: string; onClose: () => void; onSubmit: (edit: LocalEdit) => void }) {
  const image = useRef<HTMLImageElement>(null);
  const start = useRef<{ point: Point; previous: Selection; pointerId: number } | null>(null);
  const [selection, setSelection] = useState<Selection>({ shape: 'rectangle', rotation: 0, points: [] });
  const [undo, setUndo] = useState<Selection[]>([]);
  const [instruction, setInstruction] = useState(''); const [feather, setFeather] = useState(4); const [size, setSize] = useState({ width: 0, height: 0 }); const [error, setError] = useState('');
  const ready = size.width > 0; const { rect, shape, rotation } = selection;
  const points = selectionPoints(selection, size.width, size.height);
  const valid = points.length >= 3 && polygonArea(points) * size.width * size.height >= 4;
  function point(clientX: number, clientY: number) { const box = image.current!.getBoundingClientRect(); return { x: clamp((clientX - box.left) / box.width), y: clamp((clientY - box.top) / box.height) }; }
  function change(next: Selection) { setUndo(current => [...current, selection]); setSelection(next); setError(''); }
  function submit() {
    if (!valid || !ready || !instruction.trim()) { setError('请选择有效区域并填写修改要求。'); return; }
    onSubmit({ sourceUri, rect: bounds(points), shape, rotation, points, instruction: instruction.trim(), featherPx: feather });
  }
  function adjust(key: keyof NonNullable<Selection['rect']>, raw: string) {
    const next = { ...(rect ?? { x: 0, y: 0, width: .5, height: .5 }), [key]: clamp(Number(raw) / 100) };
    next.width = Math.min(next.width, 1 - next.x); next.height = Math.min(next.height, 1 - next.y);
    if (shape === 'circle') { const diameter = Math.min(next.width * size.width, next.height * size.height); next.width = diameter / size.width; next.height = diameter / size.height; }
    change({ ...selection, rect: next });
  }
  const path = points.length ? points.map((p, i) => `${i ? 'L' : 'M'}${p.x * size.width},${p.y * size.height}`).join(' ') + 'Z' : '';
  return <div className="local-edit-panel" aria-label="图片局部编辑"><header><strong><Crop size={16} />选择局部修改区域</strong><button type="button" className="icon-button" aria-label="关闭局部编辑" onClick={onClose}><X size={17} /></button></header><p>在原图上拖动选择区域；手绘沿轮廓描绘，松开后自动闭合。选区外保留原图。</p>
    <div className="local-edit-shapes" role="group" aria-label="选区形状">{([['rectangle', '矩形'], ['circle', '圆形'], ['ellipse', '椭圆'], ['freehand', '手绘']] as const).map(([id, label]) => <button type="button" className="button" key={id} aria-pressed={shape === id} onClick={() => change({ shape: id, rotation: 0, points: [] })}>{label}</button>)}</div>
    <div className="local-edit-stage" onPointerDown={event => { if (!ready || event.button !== 0) return; event.preventDefault(); const p = point(event.clientX, event.clientY); start.current = { point: p, previous: selection, pointerId: event.pointerId }; event.currentTarget.setPointerCapture(event.pointerId); setSelection({ ...selection, rect: { ...p, width: 0, height: 0 }, points: [p] }); setError(''); }}
      onPointerMove={event => { const first = start.current; if (!first || first.pointerId !== event.pointerId) return; const p = point(event.clientX, event.clientY); if (shape === 'freehand') { setSelection(current => { const last = current.points.at(-1)!; if (Math.hypot((last.x - p.x) * size.width, (last.y - p.y) * size.height) < 2) return current; const next = [...current.points, p]; return { ...current, points: next, rect: bounds(next) }; }); return; } let w = Math.abs(p.x - first.point.x), h = Math.abs(p.y - first.point.y); if (shape === 'circle') { const d = Math.min(w * size.width, h * size.height); w = d / size.width; h = d / size.height; } setSelection(current => ({ ...current, rect: { x: p.x < first.point.x ? first.point.x - w : first.point.x, y: p.y < first.point.y ? first.point.y - h : first.point.y, width: w, height: h } })); }}
      onPointerUp={event => { if (!start.current || start.current.pointerId !== event.pointerId) return; const previous = start.current.previous; setUndo(current => [...current, previous]); start.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onPointerCancel={() => { if (start.current) setSelection(start.current.previous); start.current = null; }}>
      <img ref={image} src={sourceUri} alt="框选需要修改的原图区域" draggable={false} onLoad={event => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} onError={() => { setSize({ width: 0, height: 0 }); setError('原图无法加载，请检查文件是否仍可访问。'); }} />
      {!!path && <svg className="local-edit-selection-overlay" viewBox={`0 0 ${size.width} ${size.height}`} preserveAspectRatio="none" aria-hidden="true"><path d={`M0,0H${size.width}V${size.height}H0Z ${path}`} fill="#03121c8a" fillRule="evenodd" /><path d={path} fill="#b7e9f320" stroke="#c8eef4" strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg>}
    </div>
    {shape !== 'freehand' && <><div className="local-edit-rect-fields" role="group" aria-label="选区百分比坐标">{(['x', 'y', 'width', 'height'] as const).map((key, index) => <label key={key}>{['左侧 %', '顶部 %', '宽度 %', '高度 %'][index]}<input type="number" min="0" max="100" step="0.1" aria-label={['选区左侧百分比', '选区顶部百分比', '选区宽度百分比', '选区高度百分比'][index]} value={rect ? Math.round(rect[key] * 1000) / 10 : ''} onChange={event => adjust(key, event.target.value)} /></label>)}</div>{shape !== 'circle' && <label className="local-edit-rotation">旋转角度<input type="range" aria-label="选区旋转角度" min="-180" max="180" value={rotation} onChange={event => change({ ...selection, rotation: Number(event.target.value) })} /><input type="number" aria-label="选区旋转度数" min="-180" max="180" value={rotation} onChange={event => change({ ...selection, rotation: Math.max(-180, Math.min(180, Number(event.target.value))) })} />°</label>}</>}
    <div className="local-edit-tools"><button type="button" className="button" disabled={!undo.length} onClick={() => { setSelection(undo.at(-1)!); setUndo(current => current.slice(0, -1)); }}><RotateCcw size={13} />撤销框选</button><button type="button" className="button" disabled={!rect} onClick={() => change({ ...selection, rect: undefined, points: [] })}>清除选区</button><label>边缘过渡 {feather}px<input aria-label="局部编辑边缘过渡" type="range" min="0" max="12" value={feather} onChange={event => setFeather(Number(event.target.value))} /></label></div>
    <label className="form-field">局部修改要求<textarea aria-label="局部修改要求" rows={3} value={instruction} placeholder="例如：把选中人物的衣服改成白色，保留姿态与材质" onChange={event => { setInstruction(event.target.value); setError(''); }} /></label>
    <p className="local-edit-model">使用设置中的千问模型{model ? ` · ${model}` : ''}。旋转选区接近边缘时自动缩小以保持在原图内。先预览，确认后新增版本节点。</p>
    {error && <p className="inline-error" role="alert">{error}</p>}<button type="button" className="button primary" disabled={!ready || !valid || !instruction.trim()} onClick={submit}>生成局部修改预览</button>
  </div>;
}
