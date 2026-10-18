import { projects, type CommunityProject } from './community';
import { readCreations } from './creations';
import { demoWorkflow, redact, type Workflow } from './workflow';
import { initialAnswers, type Answers, type Values } from './demo';

export type LocalEdit = { sourceUri: string; instruction: string; rect: { x: number; y: number; width: number; height: number }; featherPx: number; shape?: 'rectangle' | 'circle' | 'ellipse' | 'freehand'; rotation?: number; points?: { x: number; y: number }[]; sourceModel?: string; sourceSha256?: string; sourceWidth?: number; sourceHeight?: number };
export type ModulePreview = { id?: string; nodeId: string; prompt: string; model: string; stage: 'draft' | 'final'; simulated: boolean; source?: string; artifact?: { uri: string; mime: string; sha256?: string }; localEdit?: LocalEdit; createdAt: number };
export type WorkspaceProject = CommunityProject & { workflow: Workflow; commitId?: string; archiveCommit?: string; branch?: string; owned?: boolean; parentId?: string; status?: 'draft' | 'published'; updatedAt?: number; preview?: ModulePreview; previewHistory?: ModulePreview[]; coverArtifact?: { uri: string; mime: string }; baselineArtifact?: { uri: string; mime: string }; basePrompts?: Record<string, string>; baseModels?: Record<string, string>; remix?: { baseValues: Values; answers: Answers } };
const key = 'aica.workspace.v1';
export const exampleProjects: WorkspaceProject[] = projects.map(project => ({ ...project, workflow: demoWorkflow(project) }));
export function readWorkspace(): WorkspaceProject[] {
  try {
    const raw = localStorage.getItem(key);
    if (raw) { const data = JSON.parse(raw); return Array.isArray(data) ? data.filter(p => p.owned && typeof p.id === 'string' && typeof p.title === 'string' && Array.isArray(p.workflow?.modules) && Array.isArray(p.workflow?.edges)) : []; }
    return readCreations().flatMap(c => { const p = exampleProjects.find(p => p.id === c.sourceId); if (!p) return []; const workflow = demoWorkflow({ ...p, values: c.values }); return [{ ...p, id: `local-legacy-${c.sourceId}`, title: `${p.title} · 我的 Remix`, values: c.values, workflow, basePrompts: Object.fromEntries(p.workflow.modules.map(n => [n.id, n.prompt])), owned: true, parentId: p.id, status: 'draft' as const, updatedAt: c.updatedAt, preview: c.result ? { nodeId: 'character', prompt: workflow.modules.find(n => n.id === 'character')!.prompt, model: '', stage: c.result.stage, simulated: true, createdAt: c.updatedAt } : undefined }]; });
  } catch { return []; }
}
export function saveWorkspace(projects: WorkspaceProject[]) { localStorage.setItem(key, JSON.stringify(redact(projects))); }
export function newId() { return `local-${Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('')}`; }
export function forkProject(source: WorkspaceProject, name: string): WorkspaceProject {
  return { ...structuredClone(source), id: newId(), title: `${source.title.replace(/ · 我的 Remix$/, '')} · 我的 Remix`, author: name, avatar: name.slice(0, 1) || 'C', owned: true, parentId: source.id, status: 'draft', likes: 0, forks: 0, baselineArtifact: source.coverArtifact ?? (!source.preview?.simulated ? source.preview?.artifact : undefined) ?? source.baselineArtifact, preview: undefined, previewHistory: [], updatedAt: Date.now(), basePrompts: Object.fromEntries(source.workflow.modules.map(n => [n.id, n.prompt])), baseModels: Object.fromEntries(source.workflow.modules.map(n => [n.id, n.model])), remix: source.workflow.source === 'demo' ? { baseValues: { ...source.values }, answers: initialAnswers() } : undefined };
}
