import { describe, expect, it } from 'vitest';
import { filterProjects, projects } from './community';
import { analyze, initialAnswers, resolveAnswers } from './demo';

describe('community discovery and project isolation', () => {
  it('combines category, search and saved filters', () => {
    expect(filterProjects('mira', '3D作品', '精选', true, ['orbital-dreams']).map(p => p.id)).toEqual(['orbital-dreams']);
    expect(filterProjects('mira', 'AI漫画', '精选', false, [])).toEqual([]);
    expect(filterProjects('', '全部', '精选', true, [])).toEqual([]);
    expect(filterProjects('  不存在的作品  ', '全部', '精选', false, [])).toEqual([]);
  });
  it('sorts by popularity and date without changing the source array', () => {
    expect(filterProjects('', '全部', '热门', false, [])[0].id).toBe('little-companion');
    expect(filterProjects('', '全部', '最新', false, [])[0].id).toBe('orbital-dreams');
    expect(projects[0].id).toBe('orbital-dreams');
  });
  it('uses each project defaults as the baseline for Keep and cache decisions', () => {
    for (const project of projects) {
      const values = resolveAnswers(initialAnswers(), project.values);
      expect(values).toEqual(project.values);
      expect(Object.values(analyze(values, project.values))).toEqual(Array(7).fill('CACHE HIT'));
      const changed = analyze({ ...values, character: '新的主体' }, project.values);
      expect(changed.character).toBe('RECOMPUTE');
      expect(changed.style).toBe('CACHE HIT');
      expect(changed.layout).toBe('REBUILD');
    }
  });
});
