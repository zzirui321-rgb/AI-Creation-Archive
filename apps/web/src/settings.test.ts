import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { defaultSettings, readCredentials, readSettings, saveCredentials, saveSettings } from './settings';

const values = new Map<string, string>();
let previous: PropertyDescriptor | undefined;
beforeEach(() => {
  values.clear(); previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) } });
});
afterEach(() => { if (previous) Object.defineProperty(globalThis, 'localStorage', previous); else Reflect.deleteProperty(globalThis, 'localStorage'); });

describe('persistent local model credentials', () => {
  it('recovers saved credentials independently of general settings', () => {
    saveCredentials({ image: ' fixture-image-key ', video: 'fixture-video-key' });
    saveSettings(defaultSettings);
    expect(readCredentials()).toEqual({ image: 'fixture-image-key', video: 'fixture-video-key' });
    expect(JSON.stringify(readSettings())).not.toContain('fixture-image-key');
    expect(values.get('aica.settings.v1')).not.toContain('fixture-video-key');
  });
  it('supports replacement and explicit deletion', () => {
    saveCredentials({ image: 'first', video: 'video' });
    saveCredentials({ image: 'second', video: '' });
    expect(readCredentials()).toEqual({ image: 'second', video: '' });
    saveCredentials({ image: '', video: '' });
    expect(readCredentials()).toEqual({ image: '', video: '' });
    expect(values.has('aica.model-credentials.v1')).toBe(false);
  });
  it('tolerates unavailable or corrupt browser storage', () => {
    values.set('aica.model-credentials.v1', '{invalid');
    expect(readCredentials()).toEqual({ image: '', video: '' });
  });
});
