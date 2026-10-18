import { projects } from './community';
import type { Values } from './demo';
type PreviewResult = { values: Values; stage: 'draft' | 'final' };

export type LocalCreation = { sourceId: string; values: Values; result: PreviewResult | null; updatedAt: number };
const key = 'aica.local.creations.v1';
const validValues = (value: unknown): value is Values => !!value && typeof value === 'object' && ['style', 'character', 'detail'].every(k => typeof (value as Record<string, unknown>)[k] === 'string' && ((value as Record<string, string>)[k]).length <= 1000);
export function readCreations(): LocalCreation[] {
  try {
    const data: unknown = JSON.parse(localStorage.getItem(key) ?? '[]');
    if (!Array.isArray(data)) return [];
    const entries: LocalCreation[] = [];
    for (const item of data) {
      if (!item || !projects.some(p => p.id === item.sourceId) || !validValues(item.values) || !Number.isFinite(item.updatedAt) || entries.some(e => e.sourceId === item.sourceId)) continue;
      const result = item.result && ['draft', 'final'].includes(item.result.stage) && validValues(item.result.values) ? { values: item.result.values, stage: item.result.stage } : null;
      entries.push({ sourceId: item.sourceId, values: item.values, updatedAt: item.updatedAt, result });
    }
    return entries;
  } catch { return []; }
}
export function storeCreations(entries: LocalCreation[]) { try { localStorage.setItem(key, JSON.stringify(entries)); } catch { /* Drafts remain available for this session. */ } }
export function upsertCreation(entries: LocalCreation[], entry: LocalCreation) {
  const previous = entries.find(item => item.sourceId === entry.sourceId);
  if (previous && JSON.stringify(previous.values) === JSON.stringify(entry.values) && JSON.stringify(previous.result) === JSON.stringify(entry.result)) return entries;
  return [entry, ...entries.filter(item => item.sourceId !== entry.sourceId)];
}
