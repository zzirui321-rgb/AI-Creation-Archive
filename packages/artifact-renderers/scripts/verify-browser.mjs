import assert from 'node:assert/strict';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.argv[2] || 'http://127.0.0.1:5173';
const output = fileURLToPath(new URL('../playwright-output/', import.meta.url));
mkdirSync(output, { recursive: true });
const executable = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const options = executable ? { executablePath: executable }
  : existsSync(chromium.executablePath()) ? {} : { channel: 'msedge' };
const browser = await chromium.launch({ ...options, headless: true });
const report = [];
try {
  for (const [name, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`); });
    await page.goto(base, { waitUntil: 'networkidle' });
    const data = await (await page.request.get(`${base}/demo/generated/archives.json`)).json();
    assert.equal(data.validation.original.valid, true);
    assert.equal(data.validation.fork.valid, true);
    const widths = [];
    for (const stage of ['draft', 'final']) {
      const button = stage === 'draft' ? 'Stage 1: Draft Preview' : 'Stage 2: High-quality Replay (Final)';
      await page.getByRole('button', { name: button, exact: true }).click();
      await page.getByRole('heading', { name: 'Composite artifact', exact: true }).waitFor();
      const layoutRun = data.fork.runs.find(run => run.revision_id === 'review-fork' && run.node_id === 'layout' && run.preview_stage === stage);
      const layout = data.fork.artifacts.find(artifact => layoutRun.artifact_ids.includes(artifact.id));
      await page.waitForFunction(uri => document.querySelector('img')?.getAttribute('src') === uri, `/demo/generated/${layout.uri}`);
      await page.getByRole('heading', { name: 'Creation notes', exact: true }).waitFor();
      assert.equal(await page.locator('audio').evaluate(audio => audio.readyState >= 1), true);
      assert.match(await page.locator('.aica-reuse-heading').first().textContent(), /3\/7 nodes reused/);
      assert.equal(await page.getByRole('progressbar').getAttribute('aria-valuenow'), '3');
      assert.equal(await page.getByRole('progressbar').getAttribute('aria-valuemax'), '7');
      assert.equal(await page.locator('aside .aica-links a').count(), 4);
      assert.equal(await page.locator('aside .aica-provenance-inferred').count(), 1);
      await page.getByText('Contains Local Fallback', { exact: true }).waitFor();
      await checkImages(page);
      widths.push(await page.locator('img').first().evaluate(image => image.naturalWidth));
      await snapshot(page, `${name}-${stage}-artifacts`);
      await page.getByRole('button', { name: 'Individual panels', exact: true }).click();
      await page.locator('.aica-panel-label').first().click();
      assert.equal(await page.locator('.aica-panel .state-RECOMPUTE').count(), 2);
      assert.equal(await page.locator('.aica-panel .source-fallback').count(), 2);
      await checkImages(page);
      await snapshot(page, `${name}-${stage}-panels`);
      await page.getByRole('button', { name: 'Original / Fork', exact: true }).click();
      await page.getByRole('table').first().waitFor();
      assert.match(await page.locator('.aica-statistics').textContent(), /3 reused/);
      assert.match(await page.locator('.aica-statistics').textContent(), /3 recomputed/);
      assert.match(await page.locator('.aica-statistics').textContent(), /1 rebuilt/);
      await page.getByText('keep', { exact: true }).waitFor();
      await page.getByText('override → append', { exact: true }).waitFor();
      for (const [node, color] of [['story', 'rgb(213, 240, 223)'], ['character', 'rgb(255, 240, 189)'], ['layout', 'rgb(223, 237, 255)']]) {
        assert.equal(await page.locator(`tr[data-node-id="${node}"] .aica-state`).evaluate(element => getComputedStyle(element).backgroundColor), color);
      }
      assert.equal(await page.locator('.aica-source').filter({ hasText: /^Live API$/ }).count(), 0);
      await checkImages(page);
      await snapshot(page, `${name}-${stage}-diff`);
      await page.getByRole('button', { name: 'Execution', exact: true }).click();
      await page.getByRole('heading', { name: 'Creation Graph', exact: true }).waitFor();
      assert.equal(await page.locator('tr[data-node-id="rejected_direction"] .aica-state').textContent(), 'DEAD END');
      assert.match(await page.locator('tr[data-node-id="rejected_direction"]').textContent(), /invalid_dimensions/);
      assert.match(await page.locator('.review-golden-path').textContent(), /Recommended from successful history/);
      await snapshot(page, `${name}-${stage}-execution`);
      await page.getByRole('button', { name: 'Explore', exact: true }).click();
      await page.locator('.aica-explore-card').first().waitFor();
      await checkImages(page);
      await page.getByRole('button', { name: 'Fork Creation study / Fork', exact: true }).click();
      await page.getByRole('status').waitFor();
      await snapshot(page, `${name}-${stage}-explore`);
      await page.getByRole('button', { name: 'Dismiss notification', exact: true }).click();
      await page.getByRole('button', { name: 'Open Creation study / Original', exact: true }).click();
      await page.getByText('review-original', { exact: true }).waitFor();
      await page.getByRole('button', { name: 'Explore', exact: true }).click();
      await page.getByRole('button', { name: 'Open Creation study / Fork', exact: true }).click();
      await page.getByText('review-fork', { exact: true }).waitFor();
    }
    assert.ok(widths[1] > widths[0], 'Final must load a distinct higher-resolution composite');
    assert.deepEqual(errors, []);
    report.push({ viewport: name, stages: ['draft', 'final'], passed: true, browserErrors: 0, compositeWidths: widths });
    await page.close();
  }
} finally { await browser.close(); }
writeFileSync(`${output}/report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));

async function checkImages(page) {
  for (const image of await page.locator('img').all()) {
    await image.scrollIntoViewIfNeeded();
    await image.evaluate(element => element.complete ? undefined : new Promise(resolve => {
      element.addEventListener('load', resolve, { once: true }); element.addEventListener('error', resolve, { once: true });
    }));
  }
  const states = await page.locator('img').evaluateAll(images => images.map(image => ({ source: image.src, width: image.naturalWidth, height: image.naturalHeight })));
  assert.ok(states.length > 0);
  assert.ok(states.every(image => image.width > 0 && image.height > 0), JSON.stringify(states));
}

async function snapshot(page, name) {
  await page.evaluate(() => window.scrollTo(0, 0));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1), false, 'No page-width overflow');
  await page.screenshot({ path: `${output}/${name}.png`, fullPage: true });
}
