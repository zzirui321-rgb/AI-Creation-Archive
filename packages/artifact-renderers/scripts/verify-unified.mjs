import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.argv[2] || 'http://127.0.0.1:5176';
const output = fileURLToPath(new URL('../.c-integration/unified/', import.meta.url));
mkdirSync(output, { recursive: true });
const projects = JSON.parse(readFileSync(new URL('../.c-integration/generic-runtime/report.json', import.meta.url), 'utf8'));
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const report = [];
try {
  for (const item of projects) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const errors = [], modelSubmissions = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`); });
    page.on('request', request => { if (request.method() === 'POST' && /\/semantic-parses$/.test(request.url())) modelSubmissions.push(request.url()); });
    await page.goto(item.projectUrl, { waitUntil: 'networkidle' });
    const section = page.getByRole('region', { name: '语义图解析与证据' });
    await section.getByRole('button', { name: '载入当前档案快照', exact: true }).click();
    await section.getByText(/Source import:/).waitFor({ timeout: 60000 });
    await section.getByRole('checkbox', { name: '全部原始节点', exact: true }).uncheck();
    const scopeNode = { 'ai-comic': 'story', 'field-guide': 'biome_notes', 'self-archive': 'build_configuration' }[item.case];
    await section.getByRole('checkbox', { name: `解析节点 ${scopeNode}`, exact: true }).check();
    await section.getByRole('button', { name: '准备解析输入', exact: true }).click();
    await section.getByText(/待发送输入：/).waitFor({ timeout: 60000 });
    assert.equal(await section.getByRole('checkbox', { name: '允许发送已审核事实到解析模型' }).isChecked(), false);
    assert.equal(await section.getByRole('button', { name: '提交语义解析', exact: true }).isDisabled(), true);
    const prepared = JSON.parse(await section.locator('details pre').first().innerText());
    assert.equal(prepared.input_sha256.length, 64);
    assert.ok(prepared.facts.length > 0);
    assert.deepEqual(prepared.source_node_ids, [scopeNode]);
    await section.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/${item.case}-parser-desktop.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await section.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/${item.case}-parser-mobile.png` });
    await page.reload({ waitUntil: 'networkidle' });
    await section.getByText(/Source import:/).waitFor();
    assert.equal(await section.getByRole('button', { name: '准备解析输入', exact: true }).isVisible(), true);
    assert.deepEqual(modelSubmissions, []);
    assert.deepEqual(errors, []);
    report.push({ case: item.case, projectUrl: item.projectUrl, facts: prepared.facts.length, sourceNodes: prepared.source_node_ids.length, importId: prepared.source.import_id, noExternalCalls: true, persistedImport: true, errors });
    await page.close();
  }
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(`${base}/#/works/neon-samurai`, { waitUntil: 'networkidle' });
  const link = page.getByRole('link', { name: '全流程 Fork / Plan / Replay', exact: true });
  await link.waitFor();
  await link.click();
  await page.waitForURL('**/#/execute');
  await page.getByRole('heading', { name: '可执行创作档案', exact: true }).waitFor();
  await page.goto(`${base}/#/`, { waitUntil: 'networkidle' });
  await page.screenshot({ path: `${output}/community-desktop.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${output}/community-mobile.png` });
  await page.close();
} catch (error) {
  for (const page of browser.contexts().flatMap(context => context.pages())) {
    console.error((await page.locator('body').innerText()).slice(-10000));
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
  }
  throw error;
} finally { await browser.close(); }
writeFileSync(`${output}/report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
