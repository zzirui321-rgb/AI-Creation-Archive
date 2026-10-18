export type Mode = 'keep' | 'override' | 'append';
export type VariableKey = 'style' | 'character' | 'detail';
export type Answer = { mode: Mode; value: string };
export type Answers = Record<VariableKey, Answer>;
export type Values = Record<VariableKey, string>;
export type Decision = 'CACHE HIT' | 'RECOMPUTE' | 'REBUILD';
export type Progress = 'idle' | 'queued' | 'running' | 'succeeded' | 'cancelled';
export type Step = { id: string; title: string; subtitle: string; kind: string; prompt: string; x: number; y: number; dead?: boolean };
export const defaults: Values = { style: '霓虹漫画 · Neon manga', character: '银发赛博朋克武士', detail: '' };
export const initialAnswers = (): Answers => ({ style: { mode: 'keep', value: '' }, character: { mode: 'keep', value: '' }, detail: { mode: 'keep', value: '' } });
export const variableInfo: { key: VariableKey; title: string; description: string; placeholder: string }[] = [
  { key: 'style', title: '视觉风格', description: '绑定 Style → Panels', placeholder: '例如：复古水彩漫画' },
  { key: 'character', title: '故事主角', description: '绑定 Character → Panels', placeholder: '戴宇航员头盔的橘猫' },
  { key: 'detail', title: '附加描述', description: '绑定 Character → Panels', placeholder: '背后漂浮着汉堡包' },
];
export const steps: Step[] = [
  { id: 'story', title: 'Story', subtitle: '故事与分镜脚本', kind: 'TEXT', prompt: '一位孤独的旅人穿过霓虹都市，发现一枚来自未来的信标。以相遇、追逐、启程三个镜头讲述故事。', x: 20, y: 160 },
  { id: 'style', title: 'Style', subtitle: '统一视觉语言', kind: 'STYLE', prompt: '统一色板、光影和漫画笔触。风格：{{style}}。', x: 255, y: 30 },
  { id: 'character', title: 'Character', subtitle: '角色设定与一致性', kind: 'IMAGE', prompt: '角色：{{character}}。附加描述：{{detail}}。生成统一的角色参考。', x: 255, y: 260 },
  { id: 'trial', title: 'Character · v0', subtitle: '已放弃：角色一致性不足', kind: 'TRIAL', prompt: '早期尝试：写实人物在多镜头间外观不一致，未纳入推荐复现路径。', x: 255, y: 440, dead: true },
  { id: 'panel1', title: 'Panel 01', subtitle: '镜头一 · 霓虹相遇', kind: 'IMAGE', prompt: '结合故事、画风和角色设定，绘制都市相遇镜头。', x: 510, y: 30 },
  { id: 'panel2', title: 'Panel 02', subtitle: '镜头二 · 穿越街巷', kind: 'IMAGE', prompt: '结合故事、画风和角色设定，绘制街巷追逐镜头。', x: 510, y: 260 },
  { id: 'music', title: 'Music', subtitle: '独立的合成器氛围', kind: 'AUDIO', prompt: '为霓虹都市创作合成器背景氛围，不依赖人物和画风。', x: 510, y: 440 },
  { id: 'layout', title: 'Layout', subtitle: '合成最终漫画', kind: 'COMPOSE', prompt: '将面板按故事顺序排版，组合现有音乐引用。', x: 770, y: 160 },
];
export const links = [ ['story', 'style'], ['story', 'character'], ['story', 'trial'], ['story', 'music'], ['style', 'panel1'], ['style', 'panel2'], ['character', 'panel1'], ['character', 'panel2'], ['panel1', 'layout'], ['panel2', 'layout'], ['music', 'layout'] ];
export const activeSteps = steps.filter(step => !step.dead);

export function resolveAnswers(answers: Answers, base: Values = defaults): Values {
  return Object.fromEntries(variableInfo.map(({ key }) => {
    const answer = answers[key];
    if (answer.mode !== 'keep' && !answer.value.trim()) throw new Error(`请填写「${variableInfo.find(v => v.key === key)!.title}」的修改内容。`);
    return [key, answer.mode === 'keep' ? base[key] : answer.mode === 'override' ? answer.value.trim() : [base[key], answer.value.trim()].filter(Boolean).join('，')];
  })) as Values;
}

export function analyze(values: Values, base: Values = defaults): Record<string, Decision> {
  const dirty = new Set<string>();
  if (values.style !== base.style) dirty.add('style');
  if (values.character !== base.character || values.detail !== base.detail) dirty.add('character');
  let expanded = true;
  while (expanded) {
    expanded = false;
    for (const [source, target] of links) {
      if (dirty.has(source) && target !== 'trial' && !dirty.has(target)) { dirty.add(target); expanded = true; }
    }
  }
  return Object.fromEntries(activeSteps.map(step => [step.id, !dirty.has(step.id) ? 'CACHE HIT' : step.id === 'layout' ? 'REBUILD' : 'RECOMPUTE']));
}

export type RunEvent = { nodeId: string; progress: Progress };
export interface ReplayService {
  run(values: Values, signal: AbortSignal, onEvent: (event: RunEvent) => void, base?: Values): Promise<void>;
}
const pause = (signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (signal.aborted) return reject(new DOMException('Cancelled', 'AbortError'));
  const abort = () => { clearTimeout(timer); reject(new DOMException('Cancelled', 'AbortError')); };
  const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 450);
  signal.addEventListener('abort', abort, { once: true });
});
export const demoService: ReplayService = {
  async run(values, signal, onEvent, base = defaults) {
    const plan = analyze(values, base);
    for (const step of activeSteps) onEvent({ nodeId: step.id, progress: plan[step.id] === 'CACHE HIT' ? 'succeeded' : 'queued' });
    for (const step of activeSteps) {
      if (plan[step.id] === 'CACHE HIT') continue;
      onEvent({ nodeId: step.id, progress: 'running' });
      await pause(signal);
      onEvent({ nodeId: step.id, progress: 'succeeded' });
    }
    await pause(signal);
  },
};
