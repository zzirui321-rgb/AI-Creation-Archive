import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.argv[2] || 'http://127.0.0.1:5174';
const cases = [
  { id: 'ai-comic', variable: 'character', value: 'ocean transparent jellyfish', append: ['detail', 'with glass shell'], total: 7, dirty: 4 },
  { id: 'field-guide', variable: 'creature', value: 'pixel geometric robot', total: 5, dirty: 2 },
  { id: 'self-archive', variable: 'framework', value: 'React', append: ['review_note', 'human review required'], total: 4, dirty: 3 },
].filter(item => !process.argv[3] || item.id === process.argv[3]);
const output = fileURLToPath(new URL('../.c-integration/generic-runtime/', import.meta.url));
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const report = [];
try {
  for (const item of cases) {
    console.log(`${item.id}: importing actual .aica`);
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const errors = [], endpoints = new Set();
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`); if (/\/(?:runtime-api|api)\//.test(response.url())) endpoints.add(new URL(response.url()).pathname); });
    await page.addInitScript(() => localStorage.setItem('aica.settings.v1', JSON.stringify({ image: { provider: 'local_fallback', model: '', apiBase: '' } })));
    await page.goto(`${base}/#/execute`, { waitUntil: 'networkidle' });
    const fixture = fileURLToPath(new URL(`../demo/generated/generic-cases/${item.id}.aica.json`, import.meta.url));
    const input = JSON.parse(readFileSync(fixture, 'utf8'));
    await page.getByLabel('导入可执行档案', { exact: true }).setInputFiles(fixture);
    await page.getByRole('heading', { name: input.metadata.title, exact: true }).waitFor();
    const projectUrl = page.url();
    for (const key of Object.keys(input.variables)) await page.getByLabel(`${key} 变量值`, { exact: true }).waitFor();
    const vars = page.locator('.exec-variables');
    assert.equal(await page.locator('.exec-variables fieldset').count(), Object.keys(input.variables).length);
    await vars.getByRole('checkbox', { name: '允许执行工作流', exact: true }).check();
    await vars.getByRole('checkbox', { name: '记录执行和产物', exact: true }).check();
    async function execute(stage, phase) {
      console.log(`${item.id}: ${phase} ${stage}`);
      await page.getByRole('button', { name: `Stage ${stage === 'Draft' ? '1 · Draft' : '2 · Final'}`, exact: true }).click();
      const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && /\/executions$/.test(response.url()));
      await page.getByRole('button', { name: `执行 ${stage}`, exact: true }).click();
      const response = await responsePromise;
      assert.equal(response.status(), 202);
      const { job_id: jobId } = await response.json();
      await page.waitForFunction(id => document.querySelector(`[data-job-id="${id}"]`)?.getAttribute('data-job-state') === 'succeeded', jobId, { timeout: 120000 });
      await page.locator('.exec-artifacts .aica-reuse-heading').first().waitFor({ timeout: 30000 });
      const expected = phase === 'fork' ? item.total - item.dirty : phase === 'repeat' ? item.total : 0;
      await page.waitForFunction(text => document.querySelector('.exec-artifacts .aica-reuse-heading')?.textContent?.includes(text), `${expected}/${item.total} nodes reused`, { timeout: 30000 });
      assert.match(await page.locator('.exec-artifacts .aica-reuse-heading').first().innerText(), new RegExp(`${expected}/${item.total} nodes reused`));
      await page.locator('.exec-artifacts img').first().scrollIntoViewIfNeeded();
      await page.waitForFunction(() => document.querySelector('.exec-artifacts img')?.naturalWidth > 0);
      assert.equal(await page.locator('.source-live').count(), 0);
      await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({ path: `${output}/${item.id}-desktop-${phase}-${stage.toLowerCase()}.png`, fullPage: true });
      if (phase === 'fork' && stage === 'Final' && item.id !== 'self-archive') {
        await page.locator('.exec-artifacts').getByRole('button', { name: 'Individual panels', exact: true }).first().click();
        await page.locator('.exec-artifacts .aica-panel img').first().waitFor();
        await page.locator('.exec-artifacts .aica-panel-label').first().click();
        await page.locator('.exec-artifacts .aica-panel-details').waitFor();
        assert.match(await page.locator('.exec-artifacts .aica-panel-details').first().innerText(), new RegExp(item.value));
        assert.ok(await page.locator('.exec-artifacts .aica-panel .source-fallback').count() >= 2);
        await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
        await page.screenshot({ path: `${output}/${item.id}-desktop-panels.png`, fullPage: true });
      }
    }
    await execute('Draft', 'baseline');
    await execute('Final', 'baseline');
    const change = async (id, mode, value) => {
      await page.getByRole('group', { name: `${id} 修改方式`, exact: true }).getByRole('button', { name: mode, exact: true }).click();
      await page.getByLabel(`${id} 变量值`, { exact: true }).fill(value);
    };
    await change(item.variable, 'override', item.value);
    if (item.append) await change(item.append[0], 'append', item.append[1]);
    await page.getByRole('button', { name: '创建 Fork', exact: true }).click();
    await page.getByText(`Fork 完成：${item.dirty} 个脏节点`, { exact: true }).waitFor();
    await execute('Draft', 'fork');
    await execute('Final', 'fork');
    await page.getByRole('checkbox', { name: 'Original / Fork', exact: true }).check();
    await page.getByRole('table').first().waitFor();
    await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
    await page.screenshot({ path: `${output}/${item.id}-desktop-comparison.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({ path: `${output}/${item.id}-mobile-comparison.png`, fullPage: true });
    await page.getByRole('checkbox', { name: 'Original / Fork', exact: true }).uncheck();
    await execute('Final', 'repeat');
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Stage 2 · Final', exact: true }).click();
    await page.locator('.exec-artifacts img').first().scrollIntoViewIfNeeded();
    await page.waitForFunction(() => document.querySelector('.exec-artifacts img')?.naturalWidth > 0);
    if (item.id === 'self-archive') {
      await page.getByText('所有节点与试错分支', { exact: true }).click();
      assert.match(await page.locator('.exec-history').innerText(), /dead_end/);
      assert.match(await page.locator('.exec-history').innerText(), /recorded/);
      assert.match(await page.locator('.exec-history').innerText(), /extracted/);
      assert.match(await page.locator('.exec-history').innerText(), /inferred/);
    }
    await page.screenshot({ path: `${output}/${item.id}-mobile-final.png`, fullPage: true });
    assert.equal(page.url(), projectUrl);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    assert.deepEqual(errors, []);
    for (const endpoint of ['/forks', '/plan', '/executions', '/archive']) assert.ok([...endpoints].some(path => path.endsWith(endpoint)));
    assert.equal([...endpoints].some(path => path.includes('/preview/review')), false);
    report.push({ case: item.id, projectUrl, nodes: item.total, variables: Object.keys(input.variables), dirty: item.dirty, reused: item.total - item.dirty, endpoints: [...endpoints], errors });
    await page.close();
  }
} catch (error) {
  for (const page of browser.contexts().flatMap(context => context.pages())) {
    console.error((await page.locator('body').innerText()).slice(-10000));
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
  }
  throw error;
} finally { await browser.close(); }
writeFileSync(`${output}/report${process.argv[3] ? '-' + process.argv[3] : ''}.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report.map(({ endpoints, ...item }) => ({ ...item, apiRequests: endpoints.length })), null, 2));
