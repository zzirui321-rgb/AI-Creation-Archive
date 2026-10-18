import { describe, expect, it } from 'vitest';
import { demoCredentials, localAuth } from './auth';
import { upsertCreation } from './creations';
import { defaults } from './demo';

describe('local account adapter', () => {
  it('rejects arbitrary credentials and returns only a demo identity for the demo account', async () => {
    await expect(localAuth.signIn('not-a-user', 'wrong')).rejects.toThrow('不正确');
    const result = await localAuth.signIn(demoCredentials.account, demoCredentials.password);
    expect(result).toEqual({ id: 'local-creator', name: 'Creator', mode: 'demo' });
    expect(JSON.stringify(result)).not.toContain(demoCredentials.password);
  });
  it('does not manufacture a successful OAuth session', async () => {
    await expect(localAuth.authorize('github')).rejects.toThrow('尚未连接');
    await expect(localAuth.authorize('google')).rejects.toThrow('尚未连接');
  });
});
describe('local creation archive', () => {
  it('updates one work copy per source and preserves confirmed results', () => {
    const initial = { sourceId: 'neon-samurai', values: defaults, result: null, updatedAt: 1 };
    const first = upsertCreation([], initial);
    expect(upsertCreation(first, { ...initial, updatedAt: 2 })).toBe(first);
    const result = { values: { ...defaults, character: '橘猫' }, stage: 'final' as const };
    const updated = upsertCreation(first, { ...initial, values: result.values, result, updatedAt: 3 });
    expect(updated).toHaveLength(1);
    expect(updated[0].result).toEqual(result);
    expect(upsertCreation(updated, { ...initial, updatedAt: 4 })[0].result).toBeNull();
  });
});
