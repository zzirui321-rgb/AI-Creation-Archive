import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.argv[2], endpoint = process.argv[3], model = process.argv[4], python = process.argv[5];
if (!base || !endpoint || !model || !python) throw new Error('Usage: verify-layered-runtime.mjs <site> <image-url> <model> <python>');
const output = fileURLToPath(new URL('../.c-integration/layered/', import.meta.url));
mkdirSync(output, { recursive: true });
const source = JSON.parse(readFileSync(new URL('../demo/generated/layered/layered-scene.aica.json', import.meta.url), 'utf8'));
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const username = `layers-${Date.now()}`, password = 'synthetic-layered-acceptance-only';
let token, project;
const report = { source: 'actual local image API, independent layers', phases: [], errors: [] };
const config = { provider: 'sdcpp', api_base: endpoint, api_key: 'sk-local', model, timeout_seconds: 180, allow_fallback: false };
async function api(path, body) {
  const response = await fetch(base + path, { method: body === undefined ? 'GET' : 'POST', headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  assert.ok(response.ok, JSON.stringify(result)); return result;
}
const snapshot = () => api(`/runtime-api/projects/${project}/archive`);
async function blob(id) {
  const response = await fetch(`${base}/runtime-api/projects/${project}/artifacts/${id}`, { headers: { Authorization: `Bearer ${token}` } });
  assert.ok(response.ok); return Buffer.from(await response.arrayBuffer());
}
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
  page.on('pageerror', error => report.errors.push(error.message));
  await page.goto(`${base}/#/login`, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: '没有账号，创建账号', exact: true }).click();
  await page.getByLabel('用户名', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByLabel('显示名称', { exact: true }).fill('Layered scene acceptance');
  const login = page.waitForResponse(response => response.url().endsWith('/api/auth/login') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '注册并登录', exact: true }).click();
  token = (await (await login).json()).access_token;
  await page.waitForURL('**/#/creations');
  await page.goto(`${base}/#/settings`, { waitUntil: 'networkidle' });
  for (const [label, value] of [['image 模型 ID', model], ['image API URL', endpoint], ['image API Key', 'sk-local'], ['API 等待时限', '180']]) await page.getByLabel(label, { exact: true }).fill(value);
  await page.getByLabel('image 服务商', { exact: true }).selectOption('sdcpp');
  await page.getByLabel('允许 Local Fallback', { exact: true }).uncheck();
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await page.getByRole('link', { name: '返回可执行档案', exact: true }).click();
  await page.getByLabel('导入可执行档案', { exact: true }).setInputFiles({ name: 'layered-scene.aica.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(source)) });
  await page.getByRole('heading', { name: source.metadata.title, exact: true }).waitFor();
  project = page.url().split('/').at(-1); report.projectUrl = page.url();
  async function execute(phase, stage, expected) {
    console.log(`${phase} / ${stage}`);
    const before = await snapshot();
    const accepted = await api(`/runtime-api/projects/${project}/executions`, { request_id: crypto.randomUUID(), archive_id: before.archive_id, expected_revision: before.archive.graph.revision_id, stage, record: true, execute: true, image_config: config });
    let job;
    const deadline = Date.now() + 360000;
    while (Date.now() < deadline) {
      job = (await api(`/runtime-api/projects/${project}/executions/${accepted.job_id}`)).execution;
      if (!['queued', 'running'].includes(job.state)) break;
      await new Promise(resolve => setTimeout(resolve, 700));
    }
    assert.equal(job.state, 'succeeded', JSON.stringify(job.result));
    const after = await snapshot();
    const latest = new Map(after.archive.runs.filter(run => run.revision_id === after.archive.graph.revision_id && run.preview_stage === stage).map(run => [run.node_id, run]));
    const decisions = Object.fromEntries([...latest].map(([id, run]) => [id, run.cache.decision]));
    assert.deepEqual(decisions, expected);
    const metadata = {};
    for (const [id, run] of latest) {
      const outputId = run.outputs.out;
      writeFileSync(`${output}/${phase}-${stage}-${id}.png`, await blob(outputId));
      const sidecar = after.archive.artifacts.find(item => run.artifact_ids.includes(item.id) && item.mime_type === 'application/vnd.aica.preview+json');
      metadata[id] = JSON.parse((await blob(sidecar.id)).toString()).metadata;
      if (id !== 'scene' && !metadata[id].empty_layer) { assert.equal(metadata[id].source, 'live_api'); assert.equal(metadata[id].is_substitute, false); }
    }
    assert.equal(metadata.scene.composition_mode, 'layer_stack');
    report.phases.push({ phase, stage, decisions, metadata });
    writeFileSync(`${output}/${phase}-${stage}-archive.json`, JSON.stringify(after.archive, null, 2));
    if (stage === 'final') {
      await page.goto(`${base}/#/execute`, { waitUntil: 'networkidle' });
      await page.goto(report.projectUrl, { waitUntil: 'networkidle' });
      await page.getByRole('button', { name: 'Stage 2 · Final', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.exec-artifacts img')?.naturalWidth > 0, undefined, { timeout: 90000 });
      await page.screenshot({ path: `${output}/${phase}-desktop.png`, fullPage: true });
    }
  }
  async function fork(variable, mode, value) {
    const before = await snapshot();
    await api(`/runtime-api/projects/${project}/forks`, { request_id: crypto.randomUUID(), archive_id: before.archive_id, expected_revision: before.archive.graph.revision_id,
      operations: Object.keys(source.variables).map(id => id === variable ? { variable_id: id, mode, value } : { variable_id: id, mode: 'keep' }) });
  }
  const states = (dirty, detailEmpty = false) => Object.fromEntries(['environment_plate', 'subject_layer', 'detail_layer', 'scene'].map(id => [id, dirty.includes(id) ? id === 'scene' ? 'REBUILD' : 'RECOMPUTE' : 'CACHE_HIT']));
  for (const stage of ['draft', 'final']) await execute('original', stage, states(['environment_plate', 'subject_layer', 'detail_layer', 'scene']));
  await fork('subject', 'override', 'a cute orange tabby cat wearing a transparent astronaut helmet, full body, no scenery');
  for (const stage of ['draft', 'final']) await execute('subject', stage, states(['subject_layer', 'scene']));
  await fork('detail', 'append', 'a floating hamburger, a single small prop, no scenery');
  for (const stage of ['draft', 'final']) await execute('detail', stage, states(['detail_layer', 'scene']));
  await fork('environment', 'override', 'watercolor snowy mountain valley under a pale blue sky, empty environment');
  for (const stage of ['draft', 'final']) await execute('background', stage, states(['environment_plate', 'scene']));
  await execute('repeat', 'final', states([]));
  const pixelScript = fileURLToPath(new URL('../../../apps/gateway/src/preview/scripts/verify_scene_pixels.py', import.meta.url));
  report.pixels = JSON.parse(execFileSync(python, [pixelScript, '--directory', output], { encoding: 'utf8' }));
  for (const phase of ['original', 'subject', 'detail', 'background']) {
    const [draft, final] = ['draft', 'final'].map(stage => report.phases.find(item => item.phase === phase && item.stage === stage));
    for (const id of ['environment_plate', 'subject_layer', 'detail_layer']) assert.equal(draft.metadata[id].seed, final.metadata[id].seed);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth+1), false);
  await page.screenshot({ path: `${output}/final-mobile.png`, fullPage: true });
  assert.deepEqual(report.errors, []);
  writeFileSync(`${output}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ projectUrl: report.projectUrl, pixels: report.pixels, phases: report.phases.map(({metadata, ...item}) => item) }, null, 2));
} catch (error) {
  for (const page of browser.contexts().flatMap(context => context.pages())) { console.error((await page.locator('body').innerText()).slice(-5000)); await page.screenshot({path:`${output}/failure.png`,fullPage:true}); }
  throw error;
} finally { await browser.close(); }
