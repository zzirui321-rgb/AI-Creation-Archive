import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.argv[2] || 'http://127.0.0.1:5176';
const endpoint = process.argv[3];
const model = process.argv[4];
if (!endpoint || !model) throw new Error('Usage: verify-c-live-preview.mjs <site-origin> <image-endpoint> <model-id>');
const output = fileURLToPath(new URL('../.c-integration/live-provider/', import.meta.url));
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const report = { endpoint, model, errors: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  page.on('pageerror', error => report.errors.push(error.message));
  await page.goto(`${base}/#/settings`, { waitUntil: 'networkidle' });
  await page.getByLabel('image 服务商', { exact: true }).selectOption('sdcpp');
  await page.getByLabel('image 模型 ID', { exact: true }).fill(model);
  await page.getByLabel('image API URL', { exact: true }).fill(endpoint);
  await page.getByLabel('image API Key', { exact: true }).fill('sk-local');
  await page.getByLabel('API 等待时限', { exact: true }).fill('180');
  await page.getByLabel('允许 Local Fallback', { exact: true }).uncheck();
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await page.goto(`${base}/#/login`, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: '没有账号，创建账号', exact: true }).click();
  await page.getByLabel('用户名', { exact: true }).fill(`b-test-${Date.now()}`);
  await page.getByLabel('显示名称', { exact: true }).fill('B Local Acceptance');
  await page.getByLabel('密码', { exact: true }).fill('local-acceptance-account-only');
  await page.getByRole('button', { name: '注册并登录', exact: true }).click();
  await page.waitForURL('**/#/creations');
  await page.getByRole('button', { name: '开始创作', exact: true }).click();
  const fixture = { format: 'aica-ui-module-record', title: 'Live API single-node acceptance', description: 'Synthetic test record; single-node preview only.', steps: [{ id: 'test-image', title: 'Watercolor blue bird', kind: 'image', prompt: 'A blue bird on a branch, expressive watercolor on white paper', model: '', params: { seed: 81 }, provenance: 'inferred' }], edges: [] };
  await page.getByLabel('上传 MCP 解析文件', { exact: true }).setInputFiles({ name: 'module-record.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(fixture)) });
  await page.getByRole('button', { name: '创建我的创作档案', exact: true }).click();
  await page.getByRole('heading', { name: '你的创作工作台', exact: true }).waitFor();
  await page.getByRole('region', { name: '创作图工作台', exact: true }).getByText('Watercolor blue bird', { exact: true }).click();
  await page.getByRole('dialog', { name: '创作细节', exact: true }).waitFor();
  const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/runtime-api/previews'), { timeout: 180000 });
  await page.getByRole('button', { name: '用当前提示词生成预览', exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 200, await response.text());
  const body = await response.json();
  assert.equal(body.metadata.source, 'live_api');
  assert.equal(body.metadata.api_style, 'sdcpp');
  await page.locator('.module-preview-dock').getByText('DRAFT · Live API', { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('.module-preview-dock img')?.naturalWidth > 0);
  assert.equal(await page.getByText('生成预览（模拟）', { exact: true }).count(), 0);
  await page.screenshot({ path: `${output}/c-module-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '显示完整创作图', exact: true }).click();
  await page.waitForFunction(() => {
    const node = document.querySelector('.module-canvas .react-flow__node')?.getBoundingClientRect();
    const canvas = document.querySelector('.module-canvas')?.getBoundingClientRect();
    return node && canvas && node.left >= canvas.left - 1 && node.right <= canvas.right + 1;
  });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await page.screenshot({ path: `${output}/c-module-mobile.png`, fullPage: true });
  report.source = body.metadata.source; report.hash = body.artifact.sha256; report.projectUrl = page.url();
  assert.deepEqual(report.errors, []);
  writeFileSync(`${output}/c-module-report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  for (const page of browser.contexts().flatMap(context => context.pages())) {
    console.error((await page.locator('body').innerText()).slice(-10000));
    await page.screenshot({ path: `${output}/c-failure.png`, fullPage: true });
  }
  throw error;
} finally { await browser.close(); }
