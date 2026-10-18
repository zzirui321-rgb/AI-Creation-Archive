import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('../.c-integration/generic-runtime/', import.meta.url));
const report = JSON.parse(readFileSync(`${root}/report.json`, 'utf8'));
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  for (const item of report) {
    for (const [name, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
      const page = await browser.newPage({ viewport, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(item.projectUrl, { waitUntil: 'networkidle' });
      await page.getByRole('button', { name: 'Stage 2 · Final', exact: true }).click();
      await page.locator('.exec-artifacts img').first().scrollIntoViewIfNeeded();
      await page.waitForFunction(() => document.querySelector('.exec-artifacts img')?.naturalWidth > 0);
      if (item.case !== 'self-archive') {
        await page.locator('.exec-artifacts').getByRole('button', { name: 'Individual panels', exact: true }).first().click();
        await page.locator('.exec-artifacts .aica-panel-label').first().click();
        await page.waitForFunction(() => [...document.querySelectorAll('.exec-artifacts .aica-panel img')].every(image => image.complete && image.naturalWidth > 0));
      } else {
        await page.getByText('所有节点与试错分支', { exact: true }).click();
        await page.getByRole('button', { name: '显示试错', exact: true }).click();
        await page.locator('.react-flow__node').filter({ hasText: 'Rejected Experiment' }).waitFor();
      }
      await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({ path: `${root}/${item.case}-${name}-review.png`, fullPage: true });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      assert.deepEqual(errors, []);
      console.log(`${item.case} ${name}: images, source/regions, node graph and layout passed`);
      await page.close();
    }
  }
} finally { await browser.close(); }
