import { useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Eye, EyeOff, Github, GitBranch, KeyRound, Layers3, LockKeyhole, Mail, ShieldCheck, Sparkles } from 'lucide-react';
import SegmentedControl from './SegmentedControl';
import ParticleField from './ParticleField';
import { platformAuth, localAuth, type AuthProvider, type LocalSession } from './auth';

export default function LoginPage({ onSuccess, onBack }: { onSuccess: (session: LocalSession) => void; onBack: () => void }) {
  const [method, setMethod] = useState('password');
  const [registering, setRegistering] = useState(false);
  const [account, setAccount] = useState('');
  const [password, setPassword] = useState('');
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [touched, setTouched] = useState(false);
  async function signIn() {
    if (busy) return;
    setTouched(true); setError('');
    if (!account.trim() || !password) return;
    setBusy(true);
    try { const session = await (registering ? platformAuth.signUp(account.trim(), password, account.trim()) : platformAuth.signIn(account.trim(), password)); setPassword(''); onSuccess(session); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function authorize(provider: AuthProvider) {
    setError('');
    try { onSuccess(await localAuth.authorize(provider)); }
    catch (err) { setError((err as Error).message); }
  }
  return <main className="login-page"><div className="login-ambient"><ParticleField /><div className="hero-mouse-glow" /></div><button className="login-back" onClick={onBack}><ArrowLeft size={15} />返回发现</button><div className="login-layout"><section className="login-story"><div className="kicker"><span />CREATE. CONNECT. CONTINUE.</div><h1>每一个灵感，<br />都值得<span>继续。</span></h1><p>登录 AiCA，找到属于你的创作路径。<br />从看见灵感，到亲手创造下一个版本。</p><div className="login-orbit" aria-hidden="true"><div className="login-orbit-ring" /><span className="login-orbit-node node-a"><GitBranch size={24} /></span><span className="login-orbit-node node-b"><Layers3 size={24} /></span><span className="login-orbit-node node-c"><Sparkles size={22} /></span><i /><i /><i /></div><div className="login-promises"><span><Check size={14} />保存创作过程</span><span><Check size={14} />继续每一次探索</span><span><Check size={14} />连接下一份灵感</span></div></section><section className="login-card" aria-label="登录 AiCA"><span className="login-card-mark"><GitBranch size={25} /></span><div className="login-card-intro"><span>WELCOME TO YOUR NEXT IDEA</span><h2>欢迎回来。</h2><p>你的下一次创作，从这里开始。</p></div><SegmentedControl label="登录方式" value={method} onChange={value => { setMethod(value); setError(''); }} options={[{ id: 'password', label: '账号密码', icon: <KeyRound size={14} /> }, { id: 'authorize', label: '授权登录', icon: <ShieldCheck size={14} /> }]} />
    {method === 'password' ? <form className="login-form" onSubmit={e => { e.preventDefault(); signIn(); }} noValidate><label htmlFor="login-account">账号<span>Account</span></label><div className={`login-input ${touched && !account.trim() ? 'invalid' : ''}`}><Mail size={17} /><input id="login-account" name="username" autoComplete="username" placeholder="输入平台用户名" value={account} onChange={e => { setAccount(e.target.value); setError(''); }} onBlur={() => setTouched(true)} aria-invalid={touched && !account.trim()} aria-describedby="account-error" /></div>{touched && !account.trim() && <p id="account-error" className="field-error">请填写平台用户名。</p>}<label htmlFor="login-password">密码<span>Password</span></label><div className={`login-input ${touched && !password ? 'invalid' : ''}`}><LockKeyhole size={17} /><input id="login-password" name="password" autoComplete="current-password" type={visible ? 'text' : 'password'} placeholder="输入平台密码" value={password} onChange={e => { setPassword(e.target.value); setError(''); }} aria-invalid={touched && !password} aria-describedby="password-error" /><button type="button" aria-label={visible ? '隐藏密码' : '显示密码'} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>{touched && !password && <p id="password-error" className="field-error">请填写平台密码。</p>}{error && <p className="login-error" role="alert">{error}</p>}<button className="button primary login-submit" disabled={busy}>{busy ? '正在进入…' : registering ? '注册平台账号' : '登录平台账号'}<ArrowRight size={16} /></button></form> : <div className="oauth-options"><p>选择你熟悉的账号，连接创作世界。</p><button onClick={() => authorize('github')}><Github size={19} />使用 GitHub 授权<span>待接入</span></button><button onClick={() => authorize('google')}><span className="google-mark">G</span>使用 Google 授权<span>待接入</span></button><div className="oauth-note"><ShieldCheck size={18} /><p>授权服务尚未配置。当前点击入口只展示说明，不会跳转、读取账号或模拟授权成功。</p></div>{error && <p className="login-error" role="alert">{error}</p>}<button className="login-use-demo" onClick={() => { setMethod('password'); setAccount(''); setPassword(''); setError(''); }}>返回账号密码登录<ArrowRight size={14} /></button></div>}
    <button className="login-use-demo" onClick={() => { setRegistering(!registering); setMethod('password'); setError(''); }}>{registering ? '已有账号？返回登录' : '创建平台账号'}</button><div className="login-local-note"><span /><p>连接本机平台账号<br /><small>登录到当前平台；密码不写入作品或浏览器存储。</small></p></div></section></div></main>;
}
