'use strict';
const nonce = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
const login = document.querySelector('#login');
const project = document.querySelector('#project');
const cancel = document.querySelector('#cancel');
const error = document.querySelector('#error');
let busy = false;

async function api(action, values = {}) {
  const response = await fetch('/connect/' + action, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({nonce, ...values}), cache: 'no-store', credentials: 'omit'
  });
  if (!response.ok) throw new Error('本机连接请求失败，请重新运行 aica connect。');
  const result = await response.json();
  const messages = {
    platform_login_failed: '登录失败，请检查用户名和密码。',
    platform_request_failed: '平台请求未完成，请检查项目权限和平台状态。',
    platform_unavailable: '无法连接平台，请检查地址、网络和 HTTPS 证书。',
    connection_expired: '连接页面已过期，请重新运行 aica connect。',
    project_not_authorized: '请勾选项目授权后再连接。'
  };
  if (result.status !== 'success') throw new Error(messages[result.code] || result.message || '操作未完成，请重试。');
  return result;
}

async function act(action) {
  if (busy) return;
  busy = true;
  error.hidden = true;
  document.querySelectorAll('button,input,select').forEach(control => control.disabled = true);
  try { await action(); }
  catch (reason) { error.textContent = reason.message; error.hidden = false; }
  finally {
    busy = false;
    document.querySelectorAll('button,input,select').forEach(control => control.disabled = false);
  }
}

async function showProjects() {
  const result = await api('projects');
  const select = project.elements.project_id;
  select.replaceChildren(new Option('新建项目', ''));
  result.projects.forEach(item => select.add(new Option(item.title, item.id)));
  login.hidden = true;
  project.hidden = false;
  const repositories = await api('repositories');
  project.elements.repo_id.replaceChildren(new Option('新建同步作品', ''));
  repositories.repositories.forEach(item => project.elements.repo_id.add(new Option(item.title, item.id)));
  document.querySelector('#step-login').classList.remove('current');
  document.querySelector('#step-project').classList.add('current');
  select.focus();
}

login.addEventListener('submit', event => {
  event.preventDefault();
  void act(async () => {
    try { await api('login', {username: login.elements.username.value, password: login.elements.password.value}); }
    finally { login.elements.password.value = ''; }
    await showProjects();
  });
});

project.elements.project_id.addEventListener('change', () => {
  const creating = !project.elements.project_id.value;
  document.querySelector('#title-field').hidden = !creating;
  project.elements.title.required = creating;
});
project.elements.sync_enabled.addEventListener('change', () => {
  document.querySelector('#repo-field').hidden = !project.elements.sync_enabled.checked;
});

project.addEventListener('submit', event => {
  event.preventDefault();
  void act(async () => {
    const result = await api('bind', {
      project_id: project.elements.project_id.value, title: project.elements.title.value,
      confirm_project: project.elements.confirm_project.checked,
      sync_enabled: project.elements.sync_enabled.checked, repo_id: project.elements.repo_id.value
    });
    if (result.connected) {
      project.hidden = true;
      cancel.hidden = true;
      document.querySelector('#success').hidden = false;
    }
  });
});

cancel.addEventListener('click', () => void act(async () => {
  await api('cancel');
  login.hidden = true;
  project.hidden = true;
  cancel.hidden = true;
  document.querySelector('.steps').textContent = '已取消，原有设置保持不变。';
}));

void act(async () => {
  const result = await api('view');
  document.querySelector('#workspace').textContent = result.project;
  document.querySelector('#server').textContent = result.server_url;
  document.querySelector('#archive').textContent = `现有档案：${result.node_count} 个节点 · ${result.artifact_count} 个已登记附件`;
  project.elements.title.value = result.project;
  cancel.hidden = false;
  if (result.logged_in) await showProjects();
  else { login.hidden = false; login.elements.username.focus(); }
});
