import { platform, setPlatformIdentity } from './platform';
export type LocalSession = { id: string; name: string; username?: string; mode: 'demo' | 'platform' };
export type AuthProvider = 'github' | 'google';
export const demoCredentials = { account: 'creator@aica.demo', password: 'aica2026' };
let session: LocalSession | null = null;

export interface AuthAdapter {
  signIn(account: string, password: string): Promise<LocalSession>;
  authorize(provider: AuthProvider): Promise<LocalSession>;
}
export const localAuth: AuthAdapter = {
  async signIn(account, password) {
    if (account.trim().toLowerCase() !== demoCredentials.account || password !== demoCredentials.password) throw new Error('演示账号或密码不正确，请使用下方提供的体验账号。');
    return { id: 'local-creator', name: 'Creator', mode: 'demo' };
  },
  async authorize(provider) {
    throw new Error(`${provider === 'github' ? 'GitHub' : 'Google'} 授权尚未连接。当前可使用演示账号体验，不会跳转或读取你的真实账号。`);
  },
};
export function readSession(): LocalSession | null {
  return session;
}
export function storeSession(next: LocalSession | null) { session = next; }
export const platformAuth = {
  async signIn(username: string, password: string): Promise<LocalSession> {
    const user = await platform.login(username, password);
    setPlatformIdentity(user.id);
    return { id: user.id, name: user.name, username: user.username, mode: 'platform' };
  },
  async signUp(username: string, password: string, name: string): Promise<LocalSession> {
    await platform.register({ username, password, name });
    return this.signIn(username, password);
  },
  async signOut() {
    try { await platform.logout(); }
    finally { setPlatformIdentity(null); session = null; }
  },
};
