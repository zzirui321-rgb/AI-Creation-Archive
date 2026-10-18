import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Repository } from './platform-client.mjs';

const client = vi.hoisted(() => ({
  graph: vi.fn(), me: vi.fn(), commit: vi.fn(), setVisibility: vi.fn(), login: vi.fn(),
  logout: vi.fn(), register: vi.fn(), repository: vi.fn(), authorizedFetch: vi.fn(),
}));
vi.mock('./platform-client.mjs', () => ({ PlatformClient: vi.fn(() => client) }));
import { exportAgentWorkflow, loadProject, saveProject, setPlatformIdentity } from './platform';
import { platformAuth } from './auth';

const repo: Repository = { id: 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa', owner: 'user:alice', owner_username: 'alice', owner_name: 'Alice',
  name: 'test', title: 'Graph', description: 'Fixture', visibility: 'private', created_at: '2026-10-17T00:00:00Z',
  parent_repo: null, parent_commit: null, default_branch: 'main', forks: 0 };
const document = { title: 'Graph', modules: [{ id: 'one', prompt: 'cat' }], edges: [] };

beforeEach(() => {
  vi.resetAllMocks(); setPlatformIdentity('alice');
  client.authorizedFetch.mockResolvedValue(new Response(JSON.stringify({ previews: [] })));
  client.graph.mockResolvedValue({ commit: { id: 'initial', created_at: '2026-10-17T00:00:00Z' }, document });
  client.me.mockResolvedValue({ user: { id: 'alice' } });
  client.setVisibility.mockResolvedValue({ status: 'success' });
});

describe('platform page adapter', () => {
  it('exports the exact newly saved version even after a later save moves the branch head', async () => {
    const project = await loadProject(repo);
    client.commit.mockResolvedValueOnce({ commit: { id: 'first-saved', has_archive: true } });
    const saved = await saveProject(project);
    expect(saved.commitId).toBe('first-saved');
    expect(saved.archiveCommit).toBe('first-saved');
    client.commit.mockResolvedValueOnce({ commit: { id: 'later-saved', has_archive: true } });
    await saveProject({ ...saved, title: 'Later save' });
    client.authorizedFetch.mockResolvedValueOnce(new Response('portable-aica', { headers: { 'Content-Type': 'application/json' } }));
    const result = await exportAgentWorkflow(saved, 'aica');
    expect(result.commitId).toBe('first-saved');
    expect(await result.blob.text()).toBe('portable-aica');
    expect(client.authorizedFetch).toHaveBeenLastCalledWith(`/api/repos/${repo.id}/handoff?commit_id=first-saved&format=aica`);
  });
  it('serializes overlapping saves and uses the committed head for the next save', async () => {
    const project = await loadProject(repo);
    let finish: (value: unknown) => void = () => {};
    client.commit.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    client.commit.mockResolvedValueOnce({ commit: { id: 'second' } });
    const first = saveProject(project);
    const second = saveProject({ ...project, title: 'Second version' });
    await vi.waitFor(() => expect(client.commit).toHaveBeenCalledTimes(1));
    expect(client.commit.mock.calls[0][2].expectedHead).toBe('initial');
    finish({ commit: { id: 'first' } });
    await Promise.all([first, second]);
    expect(client.commit.mock.calls[1][2].expectedHead).toBe('first');
  });

  it('does not publish when saving fails and preserves the error for the UI', async () => {
    const project = await loadProject(repo);
    client.commit.mockRejectedValueOnce(new Error('Branch head changed'));
    await expect(saveProject({ ...project, status: 'published' })).rejects.toThrow('Branch head changed');
    expect(client.setVisibility).not.toHaveBeenCalledWith(repo.id, 'public');
  });

  it('returns the real account identity and requires the server to confirm login', async () => {
    client.login.mockResolvedValueOnce({ id: 'alice', name: 'Alice', username: 'alice' });
    expect(await platformAuth.signIn('alice', 'test-password-only')).toEqual({ id: 'alice', name: 'Alice', username: 'alice', mode: 'platform' });
    client.login.mockRejectedValueOnce(new Error('Invalid username or password'));
    await expect(platformAuth.signIn('alice', 'wrong')).rejects.toThrow('Invalid username or password');
  });
});
