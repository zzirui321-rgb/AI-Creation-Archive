import { describe, expect, it, vi } from 'vitest';
import { analyze, defaults, demoService, initialAnswers, resolveAnswers } from './demo';

describe('local demo adapter', () => {
  it('keeps defaults and excludes abandoned branches from replay', () => {
    expect(resolveAnswers(initialAnswers())).toEqual(defaults);
    expect(Object.values(analyze(defaults))).toEqual(Array(7).fill('CACHE HIT'));
    expect(analyze(defaults)).not.toHaveProperty('trial');
  });
  it('recomputes character descendants while retaining independent story, style and music', () => {
    expect(analyze({ ...defaults, character: '宇航员橘猫' })).toEqual({ story: 'CACHE HIT', style: 'CACHE HIT', character: 'RECOMPUTE', panel1: 'RECOMPUTE', panel2: 'RECOMPUTE', music: 'CACHE HIT', layout: 'REBUILD' });
  });
  it('invalidates panels for style changes without invalidating character', () => {
    const plan = analyze({ ...defaults, style: '水彩' });
    expect(plan.style).toBe('RECOMPUTE');
    expect(plan.character).toBe('CACHE HIT');
    expect(plan.panel1).toBe('RECOMPUTE');
    expect(plan.layout).toBe('REBUILD');
  });
  it('supports override plus a separate append variable and rejects empty edits', () => {
    const answers = initialAnswers();
    answers.character = { mode: 'override', value: '橘猫' };
    answers.detail = { mode: 'append', value: '漂浮汉堡包' };
    expect(resolveAnswers(answers)).toEqual({ ...defaults, character: '橘猫', detail: '漂浮汉堡包' });
    answers.character = { mode: 'append', value: '戴着头盔' };
    expect(resolveAnswers(answers).character).toBe(`${defaults.character}，戴着头盔`);
    answers.character.value = '   ';
    expect(() => resolveAnswers(answers)).toThrow('故事主角');
  });
  it('stops emitting progress after cancellation', async () => {
    vi.useFakeTimers();
    try {
      const controller = new AbortController();
      const events = vi.fn();
      const run = demoService.run({ ...defaults, character: '橘猫' }, controller.signal, events);
      const rejected = expect(run).rejects.toMatchObject({ name: 'AbortError' });
      controller.abort();
      await rejected;
      const count = events.mock.calls.length;
      await vi.runAllTimersAsync();
      expect(events).toHaveBeenCalledTimes(count);
      expect(events.mock.calls.some(([event]) => event.nodeId === 'layout' && event.progress === 'succeeded')).toBe(false);
    } finally { vi.useRealTimers(); }
  });
});
