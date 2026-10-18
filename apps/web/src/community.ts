import type { Values } from './demo';

export type ArtTheme = 'neon' | 'orbit' | 'jellyfish' | 'architecture' | 'botanical' | 'robot' | 'terrain' | 'type';
export type CommunityProject = {
  id: string;
  title: string;
  english: string;
  description: string;
  author: string;
  avatar: string;
  category: string;
  theme: ArtTheme;
  accent: string;
  height: number;
  likes: number;
  forks: number;
  date: string;
  tools: string[];
  values: Values;
};

export const projects: CommunityProject[] = [
  { id: 'orbital-dreams', title: '在土星的光环里，收集一场日落', english: 'Orbital Dreams', description: '以土星环为灵感，将流体金属、宇宙尘埃和暖色日落组合成一组超现实太空视觉。探索构图与光照的两种可能。', author: 'Mira', avatar: 'M', category: '3D作品', theme: 'orbit', accent: '#f3b88a', height: 350, likes: 286, forks: 42, date: '2026-10-16', tools: ['Concept Agent', 'Image workflow', 'Compositor'], values: { style: '超现实主义 · 流体金属', character: '悬浮在太空中的环状星体', detail: '' } },
  { id: 'neon-samurai', title: '霓虹武士：城市尽头的下一章', english: 'Neon Samurai', description: '银发武士穿过未来都市，发现一枚来自未来的信标。将灵感、试错和成功镜头归档，再让新的角色进入同一个故事。', author: 'AiCA Studio', avatar: 'A', category: 'AI漫画', theme: 'neon', accent: '#c89bf3', height: 285, likes: 192, forks: 38, date: '2026-10-15', tools: ['Story Agent', 'Image workflow', 'Comic layout'], values: { style: '霓虹漫画 · Neon manga', character: '银发赛博朋克武士', detail: '' } },
  { id: 'deep-blue', title: '深海来信 / 一只会发光的梦', english: 'Letters from the Deep', description: '把深海的静谧化为一只发光水母。半透明触须、细微的生物荧光与深蓝色水域，共同组成一段关于未知的视觉叙事。', author: 'Nori', avatar: 'N', category: '图片', theme: 'jellyfish', accent: '#80deee', height: 405, likes: 354, forks: 57, date: '2026-10-14', tools: ['Concept Agent', 'Image workflow', 'Compositor'], values: { style: '生物荧光 · 深海摄影', character: '漂浮的透明水母', detail: '' } },
  { id: 'quiet-space', title: '如果安静也有形状', english: 'A Space for Silence', description: '用几何拱门、细腻砂岩和一束自然光，构建安静的建筑空间。记录从空间草图到最终构图的探索路径。', author: 'ARCH / 07', avatar: '7', category: '3D作品', theme: 'architecture', accent: '#d8c2a1', height: 310, likes: 128, forks: 19, date: '2026-10-13', tools: ['Spatial Agent', 'Image workflow', 'Compositor'], values: { style: '极简建筑 · 砂岩质感', character: '面向天空的几何拱门', detail: '' } },
  { id: 'synthetic-garden', title: '一株来自 2099 年的植物标本', english: 'Synthetic Garden', description: '当植物生长在数字世界：用精密结构与流动叶脉构建未来植物标本，保留从自然形态到机械结构的创作过程。', author: 'Leaf.exe', avatar: 'L', category: '图片', theme: 'botanical', accent: '#b4df95', height: 380, likes: 241, forks: 31, date: '2026-10-12', tools: ['Botanic Agent', 'Image workflow', 'Compositor'], values: { style: '未来植物学 · 微距插画', character: '有发光叶脉的合成植物', detail: '' } },
  { id: 'little-companion', title: '给宇宙里孤独的人，一个搭子', english: 'Hello, Little One', description: '一个有柔和眼神的迷你机器人。通过轮廓、材质与表情的迭代，探索让科技也能传递温度的角色设计。', author: 'Pixelmono', avatar: 'P', category: '3D作品', theme: 'robot', accent: '#a4b8ff', height: 330, likes: 423, forks: 68, date: '2026-10-11', tools: ['Character Agent', 'Image workflow', 'Compositor'], values: { style: '柔和 3D · 陶瓷与金属', character: '圆润的迷你陪伴机器人', detail: '' } },
  { id: 'mars-memory', title: '火星记忆：不存在的旅行照片', english: 'Memories of Mars', description: '在没有到达过的星球上留下旅行记忆。以层叠的地形、微小的天体和矿物色彩构成遥远风景。', author: 'Yuan / 远', avatar: 'Y', category: '图片', theme: 'terrain', accent: '#e7a88a', height: 290, likes: 167, forks: 24, date: '2026-10-10', tools: ['Landscape Agent', 'Image workflow', 'Compositor'], values: { style: '科幻风景 · 矿物色彩', character: '日落下层叠的火星山脉', detail: '' } },
  { id: 'beyond-type', title: 'BEYOND：让文字逃离纸面', english: 'Beyond the Ordinary', description: '一组以 BEYOND 为主题的实验性视觉：排版、轨道、网格与光的组合。把海报设计过程保存为可继续探索的档案。', author: 'TypeLab', avatar: 'T', category: '其他', theme: 'type', accent: '#b9f475', height: 375, likes: 205, forks: 27, date: '2026-10-09', tools: ['Design Agent', 'Image workflow', 'Compositor'], values: { style: '瑞士排版 · 酸性设计', character: '悬浮于轨道中的 BEYOND 字体', detail: '' } },
  { id: 'orbital-motion', title: '环形宇宙：一段无尽的航行', english: 'An Endless Orbit', description: '视频概念项目的静态封面示例。以环状星体与轨道摄影为灵感，探索分镜、光照与运动方向。目前只提供本地封面插画，没有可播放视频。', author: 'Frame / 24', avatar: 'F', category: '视频', theme: 'orbit', accent: '#91c8df', height: 315, likes: 146, forks: 22, date: '2026-10-08', tools: ['Storyboard Agent', 'Video workflow', 'Compositor'], values: { style: '电影感 · 太空光影', character: '沿星环缓慢航行的飞船', detail: '' } },
];
export const categories = ['全部', 'AI漫画', '图片', '3D作品', '视频', '其他'];

export function filterProjects(query: string, category: string, sort: string, savedOnly: boolean, saved: string[]) {
  const search = query.trim().toLocaleLowerCase();
  return projects.filter(project => (!savedOnly || saved.includes(project.id)) && (category === '全部' || project.category === category) && (!search || [project.title, project.english, project.author, ...project.tools].join(' ').toLocaleLowerCase().includes(search)))
    .sort((a, b) => sort === '热门' ? b.likes - a.likes : sort === '最新' ? b.date.localeCompare(a.date) : projects.indexOf(a) - projects.indexOf(b));
}
