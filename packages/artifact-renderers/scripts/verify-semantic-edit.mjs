import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { chromium } from 'playwright';

const origin = process.argv[2] || 'http://127.0.0.1:5177';
const python = process.argv[3];
const live = process.argv.includes('--live');
const directory = path.resolve('.c-integration/semantic-edit');
await mkdir(directory, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(String(error)));
const report = {};
try {
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    try { ready = (await fetch('http://127.0.0.1:8101/health')).ok; } catch { ready = false; }
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  assert.equal(ready, true, 'Segmentation service did not become ready');
  await page.goto(origin + '/semantic-edit.html');
  await page.getByAltText('Artifact', { exact: false }).waitFor({ timeout: 20000 });
  await page.getByLabel('Target object', { exact: true }).fill('fox');
  await page.getByLabel('Edit instruction', { exact: true }).fill('Replace the fox with a small silver mechanical robot, watercolor illustration, keep the composition.');
  const promise = page.waitForResponse(response => response.url().endsWith('/edit-api/proposals') && response.request().method() === 'POST', { timeout: 180000 });
  await page.getByRole('button', { name: 'Locate targets' }).click();
  const proposal = await (await promise).json();
  assert.equal(proposal.status, 'ready');
  assert.equal(proposal.candidates.length, 1);
  assert.equal(proposal.candidates[0].provenance.source, 'semantic_segmentation');
  assert.equal(proposal.candidates[0].provenance.segmenter, 'facebook/sam2.1-hiera-tiny');
  await page.getByRole('button', { name: 'Confirm edit' }).waitFor();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent.includes('Confirm edit')).disabled);
  await page.screenshot({ path: path.join(directory, 'target-desktop.png'), fullPage: true });
  report.proposal = proposal;
  if (live) {
    await page.locator('summary').click();
    await page.getByLabel('API key', { exact: true }).fill('local-semantic-test');
    const run = page.waitForResponse(response => response.url().endsWith('/edit-api/execute'), { timeout: 240000 });
    await page.getByRole('button', { name: 'Confirm edit' }).click();
    const response = await run;
    assert.equal(response.status(), 200, await response.text());
    const result = await response.json();
    assert.equal(result.metadata.source, 'live_api');
    if (process.argv.includes('--expect-cache')) assert.equal(result.metadata.provider_cache.decision, 'CACHE_HIT');
    assert.equal(result.metadata.edit_proposal.id, proposal.id);
    assert.equal(result.metadata.edit_target.mask_sha256, proposal.candidates[0].mask_artifact.sha256);
    const source = await fetch(origin + '/demo/generated/semantic-edit/input.png');
    await writeFile(path.join(directory, 'source.png'), Buffer.from(await source.arrayBuffer()));
    for (const [name, uri] of [['mask.png', proposal.candidates[0].mask_artifact.uri], ['result.png', result.artifact.uri]]) {
      const image = await fetch(origin + uri); assert.equal(image.status, 200);
      await writeFile(path.join(directory, name), Buffer.from(await image.arrayBuffer()));
    }
    report.result = result;
    await page.getByText('Continue from result', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(directory, 'result-desktop.png'), fullPage: true });
    if (python) {
      const result = spawnSync(python, ['../../apps/gateway/src/preview/scripts/verify_semantic_pixels.py', directory], { encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr);
      report.pixels = JSON.parse(result.stdout);
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  if (await page.locator('details').getAttribute('open') !== null) await page.locator('summary').click();
  await page.screenshot({ path: path.join(directory, 'target-mobile.png'), fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
  const ambiguousFile = path.resolve('.c-integration/semantic-background/ambiguous.png');
  if (existsSync(ambiguousFile)) {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const upload = page.waitForResponse(response => response.url().endsWith('/edit-api/images'));
    await page.locator('input[type=file]').setInputFiles(ambiguousFile);
    const source = (await (await upload).json()).artifact;
    await page.waitForFunction(sha => document.querySelector('.semantic-source')?.textContent.includes(sha), source.sha256);
    const detection = page.waitForResponse(response => response.url().endsWith('/edit-api/proposals'), { timeout: 180000 });
    await page.getByRole('button', { name: 'Locate targets' }).click();
    const ambiguous = await (await detection).json();
    assert.equal(ambiguous.status, 'needs_confirmation');
    assert.equal(ambiguous.candidates.length, 2);
    await page.getByRole('button', { name: 'Confirm edit' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Confirm edit' }).isDisabled(), true);
    await page.getByRole('button', { name: /Select fox 2/ }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent.includes('Confirm edit') && !button.disabled));
    await page.screenshot({ path: path.join(directory, 'ambiguous-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(directory, 'ambiguous-mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    report.ambiguous = { status: ambiguous.status, candidates: 2, userSelection: ambiguous.candidates[1].id, modelExecuted: false };
  }
  report.pageErrors = errors;
  assert.deepEqual(errors, []);
  await writeFile(path.join(directory, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: proposal.status, candidates: proposal.candidates.length, model: proposal.candidates[0].provenance.segmenter, live, pixels: report.pixels, pageErrors: errors }));
} finally { await browser.close(); }
