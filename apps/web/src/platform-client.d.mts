export interface User { id: string; username: string; name: string; created_at: string }
export interface Branch { name: string; head: string | null; created_at?: string }
export interface Repository {
  id: string; owner: string; owner_username: string; owner_name: string;
  name: string; title: string; description: string; visibility: 'private' | 'public';
  default_branch: 'main'; created_at: string; parent_repo: string | null; parent_commit: string | null;
  branches?: Branch[]; head?: string; forks: number;
}
export interface Commit {
  id: string; repo_id: string; parent: string | null; author: string; message: string;
  has_archive?: boolean; created_at: string; document_hash: string; format: 'aica' | 'ui-module-record' | 'aica-semantic-graph/0.1'; source_commit: string | null;
}
export interface NewRepository { name: string; title: string; description?: string; visibility?: 'private' | 'public' }
type Success<T> = T & { status: 'success' };
export class PlatformError extends Error { status: number; code: string }
export class PlatformClient {
  constructor(baseUrl: string, fetcher?: typeof fetch);
  authorizedFetch: typeof fetch;
  register(input: { username: string; name: string; password: string }): Promise<Success<{ user: User }>>;
  login(username: string, password: string): Promise<User>;
  me(): Promise<Success<{ user: User }>>;
  logout(): Promise<void>;
  repositories(options?: { scope?: 'public' | 'mine'; q?: string; offset?: number; limit?: number }): Promise<Success<{ repositories: Repository[] }>>;
  createRepository(input: NewRepository): Promise<Success<{ repository: Repository }>>;
  repository(id: string): Promise<Success<{ repository: Repository }>>;
  branches(id: string): Promise<Success<{ branches: Branch[] }>>;
  createBranch(id: string, name: string, fromBranch?: string, fromCommit?: string | null): Promise<Success<{ branch: Branch }>>;
  graph(id: string, options?: { branch?: string; commitId?: string }): Promise<Success<{ commit: Commit; document: Record<string, unknown>; process_graph?: import('./workflow').RecordedProcessView }>>;
  commits(id: string, options?: { branch?: string; offset?: number; limit?: number }): Promise<Success<{ commits: Commit[] }>>;
  commit(id: string, document: object, options: { expectedHead: string | null; branch?: string; message?: string }): Promise<Success<{ commit: Commit }>>;
  fork(id: string, input: NewRepository & { branch?: string }): Promise<Success<{ repository: Repository }>>;
  setVisibility(id: string, visibility: 'private' | 'public'): Promise<Success<{}>>;
  createProject(title: string): Promise<Success<{ project: { id: string; title: string } }>>;
  uploadBundle(projectId: string, bytes: ArrayBuffer | Uint8Array): Promise<Success<{ import_id: string; project_id: string; bundle_sha256: string }>>;
  archiveCommit(id: string, input: { projectId: string; importId: string; expectedHead: string | null; branch?: string; message?: string }): Promise<Success<{ commit: Commit }>>;
  artifactBlob(id: string, commitId: string, artifactId: string): Promise<Blob>;
  parserInfo(): Promise<{ configured: boolean; parser_version: string; prompt_version: string; model?: string }>;
  uploadArchive(projectId: string, text: string): Promise<{ import_id: string; project_id: string; archive_sha256: string; missing_artifact_ids: string[] }>;
  prepareGraph(projectId: string, importId: string): Promise<{ input: Record<string, unknown> & { input_sha256: string } }>;
  parseGraph(projectId: string, importId: string, inputSha256: string, requestId: string): Promise<{ parse: ParseJob }>;
  parseJob(projectId: string, parseId: string): Promise<{ parse: ParseJob }>;
  semanticCommit(id: string, input: { projectId: string; importId: string; graphId: string; revisionId: string; expectedHead: string | null }): Promise<Success<{ commit: Commit }>>;
}
export interface ParseJob { id: string; parse_id: string; state: string; error_code?: string | null; error_detail?: { stage?: string; finish_reason?: string; max_tokens?: number; errors?: unknown[] } | null; graph_id?: string | null; revision_id?: string | null }
