import { describe, expect, it } from 'vitest';
import { bounds, polygonArea, selectionPoints, type Selection } from './selection-geometry';
const base: Selection = { shape: 'rectangle', rect: { x: .2, y: .2, width: .4, height: .3 }, rotation: 0, points: [] };
describe('image selection geometry', () => {
  it('rotates using original image pixels rather than stretched screen coordinates', () => {
    const p = selectionPoints({ ...base, rect: { ...base.rect!, y: .35 }, rotation: 90 }, 800, 400);
    const box = bounds(p);
    expect(box.width * 800).toBeCloseTo(120);
    expect(box.height * 400).toBeCloseTo(320);
    expect(polygonArea(p)).toBeCloseTo(.12);
  });
  it('fits rotated selections at image edges without clipping their shape', () => {
    const p = selectionPoints({ ...base, rect: { x: 0, y: 0, width: .5, height: .5 }, rotation: 45 }, 800, 400);
    expect(p.every(point => point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1)).toBe(true);
    expect(polygonArea(p)).toBeGreaterThan(0);
  });
  it('keeps a circle circular for non-square images and preserves freehand outlines', () => {
    const p = selectionPoints({ ...base, shape: 'circle', rect: { x: .2, y: .2, width: .2, height: .4 } }, 800, 400);
    const box = bounds(p); expect(box.width * 800).toBeCloseTo(box.height * 400);
    const freehand = [{ x: .1, y: .1 }, { x: .8, y: .2 }, { x: .3, y: .8 }];
    expect(selectionPoints({ ...base, shape: 'freehand', points: freehand }, 800, 400)).toEqual(freehand);
  });
});
