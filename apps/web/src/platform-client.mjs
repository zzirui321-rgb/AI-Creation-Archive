export class PlatformError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export class PlatformClient {
  #token = null;
  #base;
  #fetch;

  constructor(baseUrl, fetcher = globalThis.fetch) {
    const url = new URL(baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('Use an HTTP(S) platform base URL without credentials or query parameters');
    }
    this.#base = url.href.replace(/\/$/, '');
    this.#fetch = fetcher.bind(globalThis);
  }

  authorizedFetch = (input, options = {}) => {
    const target = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, this.#base);
    if (target.origin !== new URL(this.#base).origin) throw new Error('Authenticated requests must stay on the platform origin');
    return this.#fetch(target.href, {
      ...options, credentials: 'omit', redirect: 'error', cache: 'no-store',
      headers: { ...Object.fromEntries(new Headers(options.headers).entries()), ...(this.#token ? { Authorization: `Bearer ${this.#token}` } : {}) },
    });
  };

  async #request(path, options = {}) {
    const response = await this.#fetch(this.#base + path, {
      ...options,
      credentials: 'omit',
      cache: 'no-store',
      redirect: 'error',
      headers: {
        ...(options.body && typeof options.body === 'string' ? { 'Content-Type': 'application/json' } : {}),
        ...(this.#token ? { Authorization: `Bearer ${this.#token}` } : {}),
        ...options.headers,
      },
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401) this.#token = null;
      throw new PlatformError(response.status, data.code, data.message ?? 'Platform request failed');
    }
    return data;
  }

  #post(path, body) {
    return this.#request(path, { method: 'POST', body: JSON.stringify(body) });
  }

  register({ username, password, name }) { return this.#post('/api/auth/register', { username, password, name }); }

  async login(username, password) {
    const session = await this.#post('/api/auth/login', { username, password });
    this.#token = session.access_token;
    return session.user;
  }

  me() { return this.#request('/api/auth/me'); }

  async logout() {
    try { await this.#post('/api/auth/logout', {}); }
    finally { this.#token = null; }
  }

  repositories({ scope = 'public', q = '', offset = 0, limit = 50 } = {}) {
    return this.#request('/api/repos?' + new URLSearchParams({ scope, q, offset, limit }));
  }

  createRepository({ name, title, description = '', visibility = 'private' }) {
    return this.#post('/api/repos', { name, title, description, visibility });
  }

  repository(id) { return this.#request(`/api/repos/${encodeURIComponent(id)}`); }
  branches(id) { return this.#request(`/api/repos/${encodeURIComponent(id)}/branches`); }

  createBranch(id, name, fromBranch = 'main', fromCommit = null) {
    return this.#post(`/api/repos/${encodeURIComponent(id)}/branches`, {
      name, from_branch: fromBranch, from_commit: fromCommit,
    });
  }

  graph(id, { branch = 'main', commitId } = {}) {
    const query = new URLSearchParams({ branch });
    if (commitId) query.set('commit_id', commitId);
    return this.#request(`/api/repos/${encodeURIComponent(id)}/graph?${query}`);
  }

  commits(id, { branch = 'main', offset = 0, limit = 50 } = {}) {
    return this.#request(`/api/repos/${encodeURIComponent(id)}/commits?` + new URLSearchParams({ branch, offset, limit }));
  }

  commit(id, document, { expectedHead, branch = 'main', message = 'Update Graph' } = {}) {
    if (expectedHead === undefined) throw new Error('Provide expectedHead from the loaded branch; use null for an empty branch');
    return this.#post(`/api/repos/${encodeURIComponent(id)}/commits`, { document, expected_head: expectedHead, branch, message });
  }

  fork(id, { name, title, description = '', visibility = 'private', branch = 'main' }) {
    return this.#post(`/api/repos/${encodeURIComponent(id)}/forks`, { name, title, description, visibility, branch });
  }

  setVisibility(id, visibility) {
    return this.#request(`/api/repos/${encodeURIComponent(id)}/visibility`, {
      method: 'PATCH', body: JSON.stringify({ visibility }),
    });
  }

  createProject(title) { return this.#post('/api/projects', { title }); }

  async uploadBundle(projectId, bytes) {
    if (!globalThis.crypto?.subtle) return this.#request(`/api/projects/${encodeURIComponent(projectId)}/browser-imports`, {
      method: 'POST', body: bytes, headers: { 'Content-Type': 'application/vnd.aica.bundle+zip' },
    });
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const hash = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
    return this.#request(`/api/projects/${encodeURIComponent(projectId)}/imports`, {
      method: 'POST', body: bytes,
      headers: { 'Content-Type': 'application/vnd.aica.bundle+zip', 'X-AiCA-Bundle-SHA256': hash, 'Idempotency-Key': `sha256:${hash}` },
    });
  }

  archiveCommit(id, { projectId, importId, expectedHead, branch = 'main', message = 'Import archive' }) {
    if (expectedHead === undefined) throw new Error('Provide expectedHead');
    return this.#post(`/api/repos/${encodeURIComponent(id)}/archive-commits`, {
      project_id: projectId, import_id: importId, expected_head: expectedHead, branch, message,
    });
  }

  parserInfo() { return this.#request('/api/graph-parser'); }
  uploadArchive(projectId, text) {
    return this.#request(`/api/projects/${encodeURIComponent(projectId)}/archive-json`, {
      method: 'POST', body: text, headers: { 'Content-Type': 'application/json' },
    });
  }
  prepareGraph(projectId, importId) {
    return this.#post(`/api/projects/${encodeURIComponent(projectId)}/semantic-parse-input`, { import_id: importId });
  }
  parseGraph(projectId, importId, inputSha256, requestId) {
    return this.#post(`/api/projects/${encodeURIComponent(projectId)}/semantic-parses`, {
      import_id: importId, input_sha256: inputSha256, request_id: requestId, allow_external_processing: true,
    });
  }
  parseJob(projectId, parseId) { return this.#request(`/api/projects/${encodeURIComponent(projectId)}/semantic-parses/${encodeURIComponent(parseId)}`); }
  semanticCommit(id, { projectId, importId, graphId, revisionId, expectedHead }) {
    return this.#post(`/api/repos/${encodeURIComponent(id)}/semantic-commits`, {
      project_id: projectId, import_id: importId, graph_id: graphId, revision_id: revisionId,
      expected_head: expectedHead, message: '保存提炼后的 Graph',
    });
  }

  async artifactBlob(id, commitId, artifactId) {
    const response = await this.#fetch(this.#base + `/api/repos/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(artifactId)}?`
      + new URLSearchParams({ commit_id: commitId }), {
      credentials: 'omit', cache: 'no-store', redirect: 'error',
      headers: this.#token ? { Authorization: `Bearer ${this.#token}` } : {},
    });
    if (!response.ok) {
      if (response.status === 401) this.#token = null;
      const error = await response.json();
      throw new PlatformError(response.status, error.code, error.message);
    }
    return response.blob();
  }
}
