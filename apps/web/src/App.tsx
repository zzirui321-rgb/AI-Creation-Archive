import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUpRight, ChevronDown, Command, GitBranch, Heart, Layers3, LogIn, LogOut, Plus, Search, Settings2, Sparkles, UploadCloud, X } from 'lucide-react';
import Hero from './Hero';
import { createProject, forkProject, isRemote, listProjects, saveProject as saveRemoteProject } from './platform';
import BranchPanel from './BranchPanel';
import AgentPet from './AgentPet';
import ProjectArtwork from './ProjectArtwork';
import ProjectCard from './ProjectCard';
import { platformAuth, readSession, storeSession } from './auth';
import { categories } from './community';
import { exampleProjects, type WorkspaceProject } from './workspace';
import { readSettings, saveSettings, readCredentials, saveCredentials, type AppSettings, type ModelCredentials } from './settings';
const Studio = lazy(() => import('./Studio'));
const ShareDialog = lazy(() => import('./ShareDialog'));
const LoginPage = lazy(() => import('./LoginPage'));
const SettingsPage = lazy(() => import('./SettingsPage'));
const ImportPage = lazy(() => import('./ImportPage'));
const ExecutableWorkspace = lazy(() => import('./ExecutableWorkspace'));

function routeId() {
  const hash = window.location.hash;
  const executable = hash.match(/^#\/execute(?:\/([a-zA-Z0-9_.:-]+))?$/); if (executable) return `execute:${executable[1] || ''}`;
  const simple = hash.match(/^#\/(login|creations|saved|settings|new)$/); if (simple) return simple[1];
  const work = hash.match(/^#\/(?:works|remix)\/([^/]+)$/); if (work) return work[1];
  return hash === '#/' || !hash ? null : 'not-found';
}
function loadSaved(): string[] { try { const data = JSON.parse(localStorage.getItem('aica.community.saved') ?? '[]'); return Array.isArray(data) ? data.filter((id): id is string => typeof id === 'string') : []; } catch { return []; } }

export default function App() {
  const [route, setRoute] = useState(routeId); const [query, setQuery] = useState(''); const [category, setCategory] = useState('全部'); const [sort, setSort] = useState('精选');
  const [saved, setSaved] = useState(loadSaved); const [session, setSession] = useState(readSession); const [accountMenu, setAccountMenu] = useState(false); const [shareOpen, setShareOpen] = useState(false);
  const [localProjects, setLocalProjects] = useState<WorkspaceProject[]>([]); const localRef = useRef(localProjects);
  const sessionRef = useRef(session); sessionRef.current = session;
  const [publicProjects, setPublicProjects] = useState<WorkspaceProject[]>([]);
  const [settings, setSettings] = useState(readSettings); const [credentials, setCredentials] = useState<ModelCredentials>(readCredentials);
  useEffect(() => { try { saveCredentials(credentials); } catch { /* Settings save reports storage failures to the user. */ } }, [credentials]);
  const [creationFilter, setCreationFilter] = useState('全部创作'); const [storageError, setStorageError] = useState('');
  const scrollRef = useRef(0); const exploreRef = useRef<HTMLElement>(null); const loginReturn = useRef('#/creations');
  const isCreations = route === 'creations', savedOnly = route === 'saved', isCollection = !route || isCreations || savedOnly;
  const catalog = useMemo(() => [...exampleProjects, ...publicProjects.filter(p => !localProjects.some(own => own.id === p.id)), ...localProjects], [localProjects, publicProjects]);
  const project = (window.location.hash.startsWith('#/remix/') ? catalog.find(p => p.id === `local-legacy-${route}`) : undefined) ?? catalog.find(p => p.id === route);
  const filtered = useMemo(() => {
    const source = isCreations ? localProjects : savedOnly ? catalog.filter(p => saved.includes(p.id)) : catalog.filter(p => !p.owned || p.status === 'published');
    const term = query.trim().toLowerCase();
    return source.filter(p => (category === '全部' || p.category === category) && (!term || [p.title, p.author, p.description, ...p.tools].join(' ').toLowerCase().includes(term)) && (!isCreations || creationFilter === '全部创作' || (creationFilter === '我的原创' ? !p.parentId : creationFilter === 'Fork 创作' ? !!p.parentId : p.status === 'published'))).sort((a, b) => sort === '热门' ? b.likes - a.likes : sort === '最新' || isCreations ? (b.updatedAt ?? Date.parse(b.date)) - (a.updatedAt ?? Date.parse(a.date)) : 0);
  }, [catalog, localProjects, saved, isCreations, savedOnly, query, category, sort, creationFilter]);
  const saveProject = useCallback(async (next: WorkspaceProject) => {
    if (!sessionRef.current) throw new Error('请先登录平台账号。');
    if (!next.owned) throw new Error('请先 Fork 到自己的仓库。');
    const savedProject = isRemote(next) ? await saveRemoteProject(next) : await createProject(next);
    const updated = [savedProject, ...localRef.current.filter(p => p.id !== next.id && p.id !== savedProject.id)];
    localRef.current = updated; setLocalProjects(updated);
    return savedProject;
  }, []);
  useEffect(() => {
    let cancelled = false;
    localRef.current = []; setLocalProjects([]);
    void Promise.all([listProjects('public'), session ? listProjects('mine') : Promise.resolve([])]).then(([published, mine]) => {
      if (cancelled) return;
      setPublicProjects(published); localRef.current = mine; setLocalProjects(mine);
    }).catch(error => { if (!cancelled) setStorageError(`平台读取失败：${(error as Error).message}`); });
    return () => { cancelled = true; };
  }, [session?.id]);
  useEffect(() => {
    if (!session || route !== 'creations') return;
    let cancelled = false, pending = false;
    const refresh = async () => {
      if (pending || document.visibilityState === 'hidden') return;
      pending = true;
      try {
        const mine = await listProjects('mine');
        if (!cancelled) { localRef.current = mine; setLocalProjects(mine); }
      } catch (error) { if (!cancelled) setStorageError(`作品同步读取失败：${(error as Error).message}`); }
      finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 20000);
    window.addEventListener('focus', refresh);
    return () => { cancelled = true; window.clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, [session?.id, route]);
  useEffect(() => { const handler = () => setRoute(routeId()); window.addEventListener('hashchange', handler); return () => window.removeEventListener('hashchange', handler); }, []);
  useEffect(() => {
    const saved = (event: Event) => {
      const next = (event as CustomEvent<WorkspaceProject>).detail;
      if (!next?.owned) return;
      const updated = [next, ...localRef.current.filter(project => project.id !== next.id)];
      localRef.current = updated; setLocalProjects(updated);
    };
    window.addEventListener('aica:repository-saved', saved);
    return () => window.removeEventListener('aica:repository-saved', saved);
  }, []);
  useEffect(() => { setQuery(''); setCategory('全部'); setSort('精选'); setCreationFilter('全部创作'); setAccountMenu(false); setShareOpen(false); window.scrollTo({ top: route ? 0 : scrollRef.current, behavior: 'instant' }); }, [route]);
  useEffect(() => { document.title = project ? `${project.title} · AiCA` : `${({ login: '登录', settings: '我的设置', new: '导入创作', creations: '我的创造', saved: '我的收藏' } as Record<string, string>)[route ?? ''] ?? 'AI Creation Archive'} · AiCA`; }, [route, project?.title]);
  useEffect(() => { storeSession(session); }, [session]);
  useEffect(() => { try { localStorage.setItem('aica.community.saved', JSON.stringify(saved)); } catch { setStorageError('收藏未写入本机存储，本次页面仍可使用。'); } }, [saved]);
  useEffect(() => {
    if (!accountMenu) return;
    const pointer = (e: PointerEvent) => { if (!(e.target as Element).closest('.account-control')) setAccountMenu(false); };
    const keyboard = (e: KeyboardEvent) => { if (e.key === 'Escape') { setAccountMenu(false); document.querySelector<HTMLButtonElement>('.account-control .user-orb')?.focus(); } };
    document.addEventListener('pointerdown', pointer); document.addEventListener('keydown', keyboard); return () => { document.removeEventListener('pointerdown', pointer); document.removeEventListener('keydown', keyboard); };
  }, [accountMenu]);
  function openProject(id: string) { if (!route) scrollRef.current = window.scrollY; window.location.hash = `/works/${id}`; }
  function home() { window.location.hash = '/'; }
  function explore(onlySaved = false) { if (onlySaved) window.location.hash = '/saved'; else if (route) { home(); setTimeout(() => exploreRef.current?.scrollIntoView({ behavior: 'smooth' }), 60); } else exploreRef.current?.scrollIntoView({ behavior: 'smooth' }); }
  function toggleSaved(id: string) { setSaved(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id]); }
  function login() { loginReturn.current = route === 'login' ? '#/creations' : window.location.hash || '#/'; window.location.hash = '/login'; }
  async function fork(source: WorkspaceProject) {
    if (!sessionRef.current) { login(); return; }
    try { const next = await forkProject(source); const updated = [next, ...localRef.current.filter(p => p.id !== next.id)]; localRef.current = updated; setLocalProjects(updated); openProject(next.id); }
    catch (error) { setStorageError(`Fork 失败：${(error as Error).message}`); }
  }
  async function logout() {
    try { await platformAuth.signOut(); }
    catch (error) { setStorageError(`注销请求失败，本地登录已清除：${(error as Error).message}`); }
    finally { setSession(null); setAccountMenu(false); }
  }
  function applySettings(next: AppSettings, nextCredentials: ModelCredentials) { saveCredentials(nextCredentials); saveSettings(next); setSettings(next); setCredentials({ image: nextCredentials.image.trim(), video: nextCredentials.video.trim() }); }
  return <div className="site-shell"><a className="skip-link" href="#main-content" onClick={e => { e.preventDefault(); document.getElementById('main-content')?.focus(); }}>跳到主要内容</a>
    <header className="site-header"><a className="site-brand" href="#/" aria-label="AiCA 首页"><span className="site-logo"><GitBranch size={22} /></span>AiCA<span className="site-brand-dot">.</span></a><span className="header-separator" /><nav aria-label="主导航"><button className={!route ? 'active' : ''} onClick={() => explore()}>发现<span className="nav-dot" /></button><button className={isCreations ? 'active' : ''} onClick={() => { window.location.hash = '/creations'; }}>我的创造{localProjects.length > 0 && <span className="nav-count">{localProjects.length}</span>}<span className="nav-dot" /></button><button className={savedOnly ? 'active' : ''} onClick={() => explore(true)}>我的收藏{saved.length > 0 && <span className="nav-count">{saved.length}</span>}<span className="nav-dot" /></button></nav><div className="site-header-right"><span className="local-status"><i />LAN PLATFORM</span><a className="github-link" href="https://github.com/AiCA-Team/AiCA" target="_blank" rel="noreferrer">GitHub<ArrowUpRight size={13} /></a><button className="header-create" onClick={() => { window.location.hash = '/new'; }}><Plus size={15} /><span>开始创作</span></button><div className="account-control"><button className="user-orb" aria-label="账户菜单" aria-expanded={accountMenu} onClick={() => setAccountMenu(!accountMenu)}>{(session?.name ?? settings.displayName).slice(0, 1).toUpperCase()}</button>{accountMenu && <div className="account-menu"><strong>{session?.name ?? settings.displayName}</strong><span>{session ? `平台账号 · ${session.username}` : '访客 · 公开创作空间'}</span><button onClick={() => { window.location.hash = '/creations'; setAccountMenu(false); }}><Layers3 size={14} />我的创造</button><button onClick={() => { window.location.hash = '/settings'; setAccountMenu(false); }}><Settings2 size={14} />我的设置</button>{session ? <button onClick={() => void logout()}><LogOut size={14} />退出平台账号</button> : <button onClick={() => { setAccountMenu(false); login(); }}><LogIn size={14} />登录平台账号</button>}</div>}</div>{!session && <button className="login-entry" onClick={login}><LogIn size={15} /><span>登录</span></button>}</div></header>
    <div id="main-content" tabIndex={-1}><Suspense fallback={<section className="page-loading" role="status"><span />正在打开创作空间…</section>}>
    {route?.startsWith('execute:') ? <ExecutableWorkspace projectId={route.slice(8)} settings={settings} credentials={credentials} /> : route === 'login' ? <LoginPage onBack={home} onSuccess={next => { setSession(next); window.location.hash = loginReturn.current; }} /> : route === 'settings' ? <SettingsPage settings={settings} credentials={credentials} onSave={applySettings} /> : route === 'new' ? <ImportPage author={settings.displayName} authenticated={!!session} onLogin={login} onImport={async next => { const saved = await saveProject(next); openProject(saved.id); }} /> : isCollection ? <>
      {!isCreations && !savedOnly && <Hero explore={explore} openProject={openProject} />}
      {(isCreations || savedOnly) && <section className="collection-banner"><div className="kicker"><span />{isCreations ? 'YOUR CREATIVE SPACE' : 'YOUR INSPIRATION LIBRARY'}</div><h1>{isCreations ? '每次创造，都有下一章。' : '喜欢的灵感，留在这里。'}</h1><p>{isCreations ? '继承一份灵感，也记录自己的开始。原创、Fork 与草稿，都在这里。' : '收藏作品的过程，找到下一次创作的起点。'}</p><div className="collection-summary"><span><strong>{isCreations ? localProjects.length : saved.length}</strong>{isCreations ? '份平台创作' : '个本地收藏'}</span><span className="collection-scope">作品保存在平台 · 收藏保存在当前浏览器</span></div>{isCreations && <div className="creation-entry-grid"><a href="#/new"><span><UploadCloud size={23} /></span><div><strong>上传我的创作记录</strong><p>从 MCP 解析文件生成自己的 Graph</p></div><ArrowUpRight size={19} /></a><button onClick={() => explore()}><span><GitBranch size={23} /></span><div><strong>从别人的灵感开始</strong><p>发现作品，Fork 后继续创作</p></div><ArrowUpRight size={19} /></button></div>}</section>}
      <section className="community-section" ref={exploreRef} aria-labelledby="community-title"><div className="community-heading"><div><div className="kicker"><span />THE CREATIVE COMMONS</div><h2 id="community-title">{isCreations ? '我的创造' : savedOnly ? '我的收藏' : '灵感不止于此'}<span>Explore the possibilities.</span></h2><p>{isCreations ? '保存每一步，也保留继续改变的可能。' : savedOnly ? '留在本机的喜欢，等待下一次继续。' : '发现作品背后的过程，让别人的终点成为你的起点。'}</p></div><label className="community-search"><Search size={16} /><input aria-label="搜索社区作品" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索作品、创作者、工具…" />{query ? <button aria-label="清空搜索" onClick={() => setQuery('')}><X size={14} /></button> : <Command size={13} />}</label></div>
      {isCreations && <div className="creation-filter" aria-label="创作来源筛选">{['全部创作', '我的原创', 'Fork 创作', '已发布'].map(item => <button key={item} aria-pressed={creationFilter === item} onClick={() => setCreationFilter(item)}>{item}</button>)}</div>}
      <div className="community-controls"><div className="category-list" aria-label="作品分类">{categories.map(item => <button key={item} className={category === item ? 'active' : ''} aria-pressed={category === item} onClick={() => setCategory(item)}>{item === '全部' && <Layers3 size={13} />}{item}</button>)}</div><label className="sort-control"><span className="sr-only">作品排序</span><select aria-label="作品排序" value={sort} onChange={e => setSort(e.target.value)}><option value="精选">{isCreations ? '最近编辑' : '精选'}</option><option>最新</option><option>热门</option></select><ChevronDown size={12} /></label></div><div className="gallery-meta"><span>{isCreations ? 'YOUR CREATIONS' : savedOnly ? 'YOUR COLLECTION' : 'CURATED FOR THE CURIOUS'}<b>{filtered.length} 个作品</b></span><span className="demo-caption"><i />平台社区 · 包含示例与已发布作品</span></div>
      {filtered.length ? <div className="masonry-gallery">{filtered.map(item => <ProjectCard key={item.id} project={item} personal={isCreations} saved={saved.includes(item.id)} toggleSaved={() => toggleSaved(item.id)} onOpen={() => { scrollRef.current = window.scrollY; }} />)}</div> : <div className="gallery-empty"><Search size={30} /><h3>{isCreations ? '下一份创作，等你开始。' : savedOnly ? '还没有找到收藏的作品' : '没有找到这份灵感'}</h3><p>{isCreations ? '上传自己的创作记录，或 Fork 一份喜欢的作品。' : '换个关键词或分类，再探索一次。'}</p><button className="button" onClick={() => { setQuery(''); setCategory('全部'); setCreationFilter('全部创作'); if (isCreations) window.location.hash = '/new'; else if (savedOnly) home(); }}>{isCreations ? '上传创作记录' : '浏览全部作品'}<ArrowRight size={14} /></button></div>}
      <div className="gallery-bottom"><span>你已抵达这片灵感的边界，下一份创作，或许来自你。<Sparkles size={13} /></span></div></section><section className="closing-banner"><div><span className="kicker">FROM INSPIRATION TO INHERITANCE</span><h2>不只是看见，更是继续创造。</h2><p>See how it was made. Fork it. Make it yours.</p></div><button onClick={() => { window.location.hash = '/new'; }}>开始记录你的创作<ArrowUpRight size={20} /></button></section>
    </> : project ? <><div className="detail-nav"><button onClick={() => { window.location.hash = project.owned ? '/creations' : '/'; }}><ArrowLeft size={15} />{project.owned ? '返回我的创造' : '返回发现'}</button><span>{project.owned ? 'MY ARCHIVE' : 'CREATION ARCHIVE'}</span><div><button aria-label={saved.includes(project.id) ? '取消收藏作品' : '收藏作品'} aria-pressed={saved.includes(project.id)} onClick={() => toggleSaved(project.id)}><Heart size={15} fill={saved.includes(project.id) ? 'currentColor' : 'none'} />{saved.includes(project.id) ? '已收藏' : '收藏'}</button><button onClick={() => setShareOpen(true)}><ArrowUpRight size={14} />分享作品</button></div></div><section className="detail-overview"><div className="detail-overview-art"><ProjectArtwork project={project} values={project.values} /><span>{project.coverArtifact ? 'MODEL OUTPUT / 确认生成结果' : project.workflow.source === 'imported' ? 'IMPORTED ARCHIVE / 示意封面' : 'LOCAL VISUAL STUDY / 示意作品'}</span></div><div className="detail-overview-copy"><span className="kicker">{project.category} / EXECUTABLE ARCHIVE</span><h2>{project.title}</h2><p>{project.description}</p><div className="overview-author"><span style={{ color: project.accent }}>{project.avatar}</span><div><strong>{project.author}</strong><small>{project.date} · {project.owned ? project.status === 'published' ? '平台已发布' : '平台私有草稿' : '社区示例'}</small></div></div><div className="overview-tools">{project.tools.map(tool => <span key={tool}>{tool}</span>)}</div><button className="button primary" onClick={() => document.querySelector('.module-studio')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}><GitBranch size={14} />查看创作图<ArrowDown size={14} /></button><small className="overview-note">{project.workflow.source === 'imported' ? `${project.workflow.modules.length} 个模块 · 文件提供的过程记录` : '示例工作流模板 · 非真实采集'}</small></div></section><BranchPanel project={project} onChange={next => { const updated = [next, ...localRef.current.filter(p => p.id !== next.id)]; localRef.current = updated; setLocalProjects(updated); }} /><Studio key={`${project.id}:${project.branch}`} project={project} settings={settings} credentials={credentials} onSave={saveProject} onFork={fork} /></> : <section className="not-found"><span>404 / ARCHIVE NOT FOUND</span><h1>这份灵感，暂未归档。</h1><button className="button primary" onClick={home}>返回发现<ArrowRight size={15} /></button></section>}
    </Suspense></div>
    {shareOpen && project && <Suspense fallback={null}><ShareDialog project={project} link={window.location.href} localRemix={!isRemote(project) && !!project.owned} onClose={() => setShareOpen(false)} /></Suspense>}
    {storageError && <p className="inline-error storage-feedback" role="alert">{storageError}<button className="icon-button" aria-label="关闭存储提示" onClick={() => setStorageError('')}><X size={14} /></button></p>}
    <footer className="site-footer"><a className="site-brand" href="#/">AiCA<span className="site-brand-dot">.</span></a><p>Archive the process, not just the result.</p><span>局域网平台 · 模型调用需配置服务商</span><a href="https://github.com/AiCA-Team/AiCA" target="_blank" rel="noreferrer">BUILT FOR THE NEXT IDEA<ArrowUpRight size={12} /></a></footer>
    <AgentPet projectTitle={project?.title} openGraph={() => project ? document.querySelector('.module-studio')?.scrollIntoView({ behavior: 'smooth' }) : openProject('neon-samurai')} />
  </div>;
}
