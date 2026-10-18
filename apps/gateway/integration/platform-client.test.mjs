import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PlatformClient, PlatformError } from './platform-client.mjs';

test('C client authenticates, commits with expected version, and sends no provider credentials', async () => {
  const calls = [];
  const client = new PlatformClient('http://localhost:8000', async (url, options) => {
    calls.push({ url, options });
    const data = url.endsWith('/login') ? { access_token: 'session-token', user: { username: 'alice' } } : { status: 'success' };
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  assert.deepEqual(await client.login('alice', 'a-long-password'), { username: 'alice' });
  await client.commit('repo', { modules: [{ id: 'one' }] }, { expectedHead: null });
  assert.equal(calls[1].options.headers.Authorization, 'Bearer session-token');
  assert.equal(JSON.parse(calls[1].options.body).expected_head, null);
  assert.equal(calls[1].options.redirect, 'error');
  assert.throws(() => client.commit('repo', {}), /expectedHead/);
  await client.createBranch('repo', 'experiment/cat');
  assert.equal(JSON.parse(calls[2].options.body).name, 'experiment/cat');
  await client.graph('repo', { branch: 'experiment/cat' });
  assert.equal(new URL(calls[3].url).searchParams.get('branch'), 'experiment/cat');
  await client.logout();
  await client.repositories();
  assert.equal(calls.at(-1).options.headers.Authorization, undefined);
});

test('C client preserves 409 conflict and invalidates an expired session', async () => {
  const calls = [];
  const client = new PlatformClient('http://localhost:8000', async (url, options) => {
    calls.push(options);
    if (url.endsWith('/login')) return Response.json({ access_token: 'expired', user: {} });
    const status = url.includes('/commits') ? 409 : 401;
    return Response.json({ code: 'id_conflict', message: 'Reload before committing' }, { status });
  });
  await client.login('alice', 'password');
  await assert.rejects(client.commit('repo', {}, { expectedHead: 'old' }), error => error instanceof PlatformError && error.status === 409);
  await assert.rejects(client.me(), error => error.status === 401);
  await assert.rejects(client.me());
  assert.equal(calls.at(-1).headers.Authorization, undefined);
});

test('bundle uploads use the existing A hash contract and artifacts use authorization headers', async () => {
  const calls = [];
  const client = new PlatformClient('http://localhost:8000', async (url, options) => {
    calls.push({ url, options });
    if (url.includes('/artifacts/')) return new Response(new Uint8Array([1, 2]), { headers: { 'Content-Type': 'image/png' } });
    return Response.json({ status: 'success' });
  });
  await client.uploadBundle('project', new Uint8Array([1, 2, 3]));
  const hash = calls[0].options.headers['X-AiCA-Bundle-SHA256'];
  assert.equal(hash, '039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81');
  assert.equal(calls[0].options.headers['Idempotency-Key'], `sha256:${hash}`);
  const blob = await client.artifactBlob('repo', 'commit', 'image');
  assert.equal(blob.type, 'image/png');
  assert.equal(new URL(calls[1].url).searchParams.get('commit_id'), 'commit');
  assert.equal(calls[1].url.includes('token'), false);
});
