import { afterEach, describe, expect, it, vi } from 'vitest';
import { readSharedCatalog, saveSharedCatalog } from './local-sharing';
import { exampleProjects } from './workspace';

afterEach(() => vi.unstubAllGlobals());
describe('shared archives in local collections', () => {
  it('retains read-only snapshots so bookmarks can resolve after navigation and reload', () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) });
    const shared = { ...exampleProjects[0], id: 'shared-' + 'a'.repeat(32), owned: false };
    saveSharedCatalog([shared]);
    expect(readSharedCatalog()[0].id).toBe(shared.id);
    expect(readSharedCatalog()[0].workflow.modules).toEqual(shared.workflow.modules);
  });
  it('ignores malformed snapshots and owned drafts in the share catalog', () => {
    vi.stubGlobal('localStorage', { getItem: () => JSON.stringify([{ id: 'shared-' + 'a'.repeat(32), owned: true }, { id: 'bad' }, null]) });
    expect(readSharedCatalog()).toEqual([]);
  });
});
