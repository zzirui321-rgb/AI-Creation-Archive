import { describe, expect, it } from 'vitest';
import { cacheDecision, defaultUriResolver, generationSource, latestRuns, primaryArtifacts, replayNodes, reuseStatistics, safeExternalUri, safeMediaUri, variableChanges } from '../src';
import { artifact, fixture, run } from './fixtures';

describe('archive view models', () => {
  it('counts actual latest runs and excludes executions from old revisions', () => {
    const archive = fixture();
    archive.runs = [run('source', 'CACHE_HIT'), run('image', 'RECOMPUTE'), run('layout', 'REBUILD'),
      { ...run('source', 'RECOMPUTE'), revision_id: 'old' }];
    expect(reuseStatistics(archive)).toEqual({ reused: 1, recomputed: 1, rebuilt: 1, unknown: 0 });
    expect(cacheDecision(latestRuns(archive).get('source'))).toBe('CACHE_HIT');
  });
  it('selects a requested stage without confusing draft with final', () => {
    const archive = fixture();
    archive.runs = [run('image', 'RECOMPUTE'), { ...run('image', 'CACHE_HIT'), id: 'hq', preview_stage: 'final' }];
    expect(latestRuns(archive, 'draft').get('image')?.id).toBe('run-image');
    expect(latestRuns(archive, 'high_quality').get('image')?.id).toBe('hq');
  });
  it('keeps failed work out of successful reuse counts', () => {
    const archive = fixture();
    archive.runs = [{ ...run('image', 'CACHE_HIT'), state: 'failed' }];
    expect(reuseStatistics(archive).reused).toBe(0);
    expect(reuseStatistics(archive).unknown).toBe(3);
  });
  it('shows terminal outputs instead of every intermediate image', () => {
    const archive = fixture();
    archive.artifacts = [artifact('intermediate'), artifact('final')];
    archive.runs = [run('image', 'RECOMPUTE', ['intermediate']), run('layout', 'REBUILD', ['final'])];
    expect(primaryArtifacts(archive).map(item => item.id)).toEqual(['final']);
  });
  it('retains ordered variable operations and unchanged Keep', () => {
    const original = fixture(), fork = fixture();
    fork.variable_operations = [{ variable_id: 'theme', mode: 'override', value: 'space' },
      { variable_id: 'theme', mode: 'append', value: 'station' }];
    fork.variables.theme.value = 'space station';
    expect(variableChanges(original, fork)[0].operations).toEqual(['override', 'append']);
    fork.variable_operations = [{ variable_id: 'theme', mode: 'keep' }];
    fork.variables.theme.value = 'city';
    expect(variableChanges(original, fork)[0].operations).toEqual(['keep']);
  });
  it('rejects executable schemes and local filesystem URLs', () => {
    expect(safeMediaUri('javascript:alert(1)')).toBeUndefined();
    expect(safeMediaUri('file:private.png')).toBeUndefined();
    expect(safeMediaUri('//untrusted.example/a')).toBeUndefined();
    expect(safeMediaUri('/artifacts/image.png')).toBe('/artifacts/image.png');
    expect(safeExternalUri('data:text/html,unsafe')).toBeUndefined();
    expect(safeExternalUri('https://username:password@example.com')).toBeUndefined();
  });
  it('excludes dead-end branches and their descendants from reuse counts', () => {
    const archive = fixture();
    archive.graph.branches.push({ id: 'trial', parent_id: 'main', state: 'dead_end', reason: 'Rejected test' });
    archive.graph.branches.push({ id: 'child', parent_id: 'trial', state: 'active' });
    const inactive = { ...archive.graph.nodes[0], id: 'trial-node', branch_id: 'child' };
    archive.graph.nodes.push(inactive);
    archive.runs = [run('trial-node', 'CACHE_HIT')];
    expect(replayNodes(archive).length).toBe(3);
    expect(reuseStatistics(archive)).toEqual({ reused: 0, recomputed: 0, rebuilt: 0, unknown: 3 });
  });
  it('never describes a known local fallback as Live API', () => {
    expect(generationSource({ source: 'live_api', is_substitute: true }).label).toBe('Local Fallback');
    expect(generationSource({ source: 'prewarmed_cache', origin_source: 'local_fallback' }).label).toBe('Local Fallback');
    expect(generationSource({}).label).toBe('Source unavailable');
  });
  it('uses remote locations without changing A artifact identity or original URI', () => {
    const item = { ...artifact(), uri: 'file:another-machine/.aica/output.png',
      locations: ['file:private/output.png', 'https://platform.example/artifacts/output.png'] };
    const original = JSON.stringify(item);
    expect(defaultUriResolver(item)).toBe('https://platform.example/artifacts/output.png');
    expect(JSON.stringify(item)).toBe(original);
  });
});
