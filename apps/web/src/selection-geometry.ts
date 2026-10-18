import type { LocalEdit } from './workspace';
export type Point = { x: number; y: number };
export type Selection = { shape: NonNullable<LocalEdit['shape']>; rect?: LocalEdit['rect']; rotation: number; points: Point[] };
export function bounds(points: Point[]) {
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, width: Math.max(...xs) - x, height: Math.max(...ys) - y };
}
export function selectionPoints(selection: Selection, width: number, height: number): Point[] {
  if (selection.shape === 'freehand') return selection.points;
  const rect = selection.rect;
  if (!rect || !width || !height) return [];
  const cx = (rect.x + rect.width / 2) * width, cy = (rect.y + rect.height / 2) * height;
  const rx = rect.width * width / 2, ry = rect.height * height / 2;
  const angle = selection.rotation * Math.PI / 180;
  const raw = selection.shape === 'rectangle' ? [[-rx, -ry], [rx, -ry], [rx, ry], [-rx, ry]] : Array.from({ length: 96 }, (_, i) => [rx * Math.cos(i * Math.PI / 48), ry * Math.sin(i * Math.PI / 48)]);
  const offsets = raw.map(([x, y]) => ({ x: x * Math.cos(angle) - y * Math.sin(angle), y: x * Math.sin(angle) + y * Math.cos(angle) }));
  let scale = 1;
  for (const p of offsets) {
    if (p.x > 0) scale = Math.min(scale, (width - cx) / p.x); else if (p.x < 0) scale = Math.min(scale, -cx / p.x);
    if (p.y > 0) scale = Math.min(scale, (height - cy) / p.y); else if (p.y < 0) scale = Math.min(scale, -cy / p.y);
  }
  return offsets.map(p => ({ x: Math.max(0, Math.min(1, (cx + p.x * scale) / width)), y: Math.max(0, Math.min(1, (cy + p.y * scale) / height)) }));
}
export function polygonArea(points: Point[]) {
  return Math.abs(points.reduce((sum, p, i) => { const next = points[(i + 1) % points.length]; return sum + p.x * next.y - p.y * next.x; }, 0)) / 2;
}
