import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.argv[2] || 'http://127.0.0.1:5176';
const endpoint = process.argv[3];
const model = process.argv[4];
if (!endpoint || !model) throw new Error('Usage: verify-live-provider.mjs <site-origin> <image-endpoint> <model-id>');
const output = fileURLToPath(new URL('../.c-integration/live-provider/', import.meta.url));
const fixture = fileURLToPath(new URL('../demo/generated/generic-cases/field-guide.aica.json', import.meta.url));
mkdirSync(output, { recursive: true });
const key = 'local-test-key-not-for-storage';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const report = { endpoint, model, stages: [], errors: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  let platformToken;
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('response', response => { if (response.status() >= 400) report.errors.push(`${response.status()} ${response.url()}`); });
  await page.goto(`${base}/#/login`, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: '没有账号，创建账号', exact: true }).click();
  await page.getByLabel('用户名', { exact: true }).fill(`live-${Date.now()}`);
  await page.getByLabel('显示名称', { exact: true }).fill('Local model acceptance');
  await page.getByLabel('密码', { exact: true }).fill('synthetic-local-acceptance-only');
  const loginResponse = page.waitForResponse(response => response.url().endsWith('/api/auth/login') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '注册并登录', exact: true }).click();
  platformToken = (await (await loginResponse).json()).access_token;
  assert.ok(platformToken);
  await page.waitForURL('**/#/creations');
  await page.goto(`${base}/#/settings`, { waitUntil: 'networkidle' });
  await page.getByLabel('image 服务商', { exact: true }).selectOption('sdcpp');
  await page.getByLabel('image 模型 ID', { exact: true }).fill(model);
  await page.getByLabel('image API URL', { exact: true }).fill(endpoint);
  await page.getByLabel('image API Key', { exact: true }).fill(key);
  await page.getByLabel('API 等待时限', { exact: true }).fill('180');
  await page.getByLabel('允许 Local Fallback', { exact: true }).uncheck();
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '设置已保存' }).waitFor();
  assert.equal(await page.evaluate(secret => JSON.stringify(localStorage).includes(secret), key), false);
  await page.getByRole('link', { name: '返回可执行档案', exact: true }).click();
  await page.getByLabel('导入可执行档案', { exact: true }).setInputFiles(fixture);
  const input = JSON.parse(readFileSync(fixture, 'utf8'));
  await page.getByRole('heading', { name: input.metadata.title, exact: true }).waitFor();
  const projectUrl = page.url(), project = projectUrl.split('/').at(-1);
  report.projectUrl = projectUrl;
  const variables = page.locator('.exec-variables');
  await variables.getByRole('checkbox', { name: '允许执行工作流', exact: true }).check();
  await variables.getByRole('checkbox', { name: '记录执行和产物', exact: true }).check();
  const snapshots = {};
  async function execute(stage, phase, expectedHits) {
    console.log(`${phase}: ${stage}`);
    await page.getByRole('button', { name: stage === 'draft' ? 'Stage 1 · Draft' : 'Stage 2 · Final', exact: true }).click();
    await page.getByRole('button', { name: `执行 ${stage === 'draft' ? 'Draft' : 'Final'}`, exact: true }).waitFor();
    const accepted = page.waitForResponse(response => response.request().method() === 'POST' && /\/executions$/.test(response.url()));
    const start = Date.now();
    await page.getByRole('button', { name: `执行 ${stage === 'draft' ? 'Draft' : 'Final'}`, exact: true }).click();
    const response = await accepted;
    assert.equal(response.status(), 202, await response.text());
    const { job_id: id } = await response.json();
    await page.waitForFunction(job => { const state = document.querySelector(`[data-job-id="${job}"]`)?.getAttribute('data-job-state'); return state && !['queued', 'running'].includes(state); }, id, { timeout: 360000 });
    assert.equal(await page.locator(`[data-job-id="${id}"]`).getAttribute('data-job-state'), 'succeeded', await page.locator('body').innerText());
    const data = await page.evaluate(async ({ project, token }) => (await fetch(`/runtime-api/projects/${project}/archive`, { headers: { Authorization: `Bearer ${token}` } })).json(), { project, token: platformToken });
    assert.equal(JSON.stringify(data).includes(key), false);
    const current = data.archive.runs.filter(run => run.revision_id === data.archive.graph.revision_id && run.preview_stage === stage);
    const latest = new Map(current.map(run => [run.node_id, run]));
    const hits = [...latest.values()].filter(run => run.cache.decision === 'CACHE_HIT').length;
    assert.equal(hits, expectedHits);
    const receipts = await page.evaluate(async ({ project, archive, latestIds, token }) => {
      const values = [];
      for (const run of archive.runs.filter(run => latestIds.includes(run.id))) {
        for (const artifact of archive.artifacts.filter(item => run.artifact_ids.includes(item.id) && item.mime_type === 'application/vnd.aica.preview+json')) {
          values.push({ node: run.node_id, metadata: await (await fetch(`/runtime-api/projects/${project}/artifacts/${artifact.id}`, { headers: { Authorization: `Bearer ${token}` } })).json() });
        }
      }
      return values;
    }, { project, archive: data.archive, latestIds: [...latest.values()].map(run => run.id), token: platformToken });
    for (const receipt of receipts.filter(value => ['wildlife_plate', 'habitat_plate'].includes(value.node))) {
      assert.equal(receipt.metadata.metadata.source, 'live_api');
      assert.equal(receipt.metadata.metadata.api_style, 'sdcpp');
      assert.equal(receipt.metadata.metadata.is_substitute, false);
      assert.equal(receipt.metadata.metadata.actual_width, receipt.metadata.metadata.width);
      assert.equal(receipt.metadata.metadata.actual_height, receipt.metadata.metadata.height);
    }
    await page.locator('.exec-artifacts').getByRole('button', { name: 'Individual panels', exact: true }).first().click();
    await page.locator('.exec-artifacts .source-live').first().waitFor();
    assert.equal(await page.locator('.exec-artifacts .source-fallback').count(), 0);
    await page.waitForFunction(() => [...document.querySelectorAll('.exec-artifacts img')].some(image => image.naturalWidth > 0));
    await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
    await page.screenshot({ path: `${output}/${phase}-${stage}-desktop.png`, fullPage: true });
    await page.locator('.exec-artifacts').getByRole('button', { name: 'Composite image', exact: true }).first().click();
    report.stages.push({ phase, stage, reused: hits, total: latest.size, elapsedSeconds: (Date.now() - start) / 1000, receipts });
    snapshots[`${phase}-${stage}`] = data;
  }
  await execute('draft', 'original', 0);
  await execute('final', 'original', 0);
  const edit = page.getByRole('group', { name: 'creature 修改方式', exact: true });
  await edit.getByRole('button', { name: 'override', exact: true }).click();
  await page.getByLabel('creature 变量值', { exact: true }).fill('a golden mechanical owl with blue eyes');
  await page.getByRole('button', { name: '创建 Fork', exact: true }).click();
  await page.getByText('Fork 完成：2 个脏节点', { exact: true }).waitFor();
  await execute('draft', 'fork', 3);
  await execute('final', 'fork', 3);
  await execute('final', 'repeat', 5);
  for (const phase of ['original', 'fork']) {
    const stages = report.stages.filter(value => value.phase === phase);
    for (const node of ['wildlife_plate', 'habitat_plate']) {
      const [draft, final] = stages.map(stage => stage.receipts.find(item => item.node === node).metadata.metadata);
      assert.equal(draft.seed, final.seed);
      assert.equal(draft.width / draft.height, final.width / final.height);
      assert.ok(final.width > draft.width);
      assert.equal(draft.effective_params.steps, 4);
      assert.equal(final.effective_params.steps, 28);
    }
  }
  const primaryHash = (phase, node) => report.stages.find(item => item.phase === phase && item.stage === 'final').receipts.find(item => item.node === node).metadata.metadata.primary_sha256;
  assert.notEqual(primaryHash('original', 'wildlife_plate'), primaryHash('fork', 'wildlife_plate'));
  assert.equal(primaryHash('original', 'habitat_plate'), primaryHash('fork', 'habitat_plate'));
  await page.getByRole('checkbox', { name: 'Original / Fork', exact: true }).check();
  await page.getByRole('table').first().waitFor();
  await page.screenshot({ path: `${output}/comparison-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await page.screenshot({ path: `${output}/comparison-mobile.png`, fullPage: true });
  await page.goto(`${base}/#/settings`, { waitUntil: 'networkidle' });
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.getByLabel('image API Key', { exact: true }).inputValue(), '');
  assert.equal(await page.evaluate(secret => JSON.stringify(localStorage).includes(secret), key), false);
  assert.deepEqual(report.errors, []);
  writeFileSync(`${output}/report.json`, JSON.stringify(report, null, 2));
  writeFileSync(`${output}/final-archive.json`, JSON.stringify(snapshots['repeat-final'].archive, null, 2));
  console.log(JSON.stringify(report.stages.map(({ receipts, ...value }) => value), null, 2));
} catch (error) {
  for (const page of browser.contexts().flatMap(context => context.pages())) {
    console.error((await page.locator('body').innerText()).slice(-12000));
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
  }
  throw error;
} finally { await browser.close(); }
