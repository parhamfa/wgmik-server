const { test } = require('@playwright/test');

test('dashboard debug', async ({ page }) => {
  const consoleMessages = [];
  const failures = [];
  const apiEvents = [];

  page.on('console', msg => {
    consoleMessages.push(`[${msg.type()}] ${msg.text()}`);
  });
  page.on('pageerror', err => {
    consoleMessages.push(`[pageerror] ${err.stack || err.message}`);
  });
  page.on('response', async response => {
    const url = response.url();
    if (!url.includes('/api/')) return;
    apiEvents.push(`${response.status()} ${url}`);
    if (response.status() >= 400) {
      let body = '';
      try { body = await response.text(); } catch {}
      failures.push(`${response.status()} ${url} ${body}`);
    }
  });

  await page.goto('http://localhost:5173', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"], input[type="text"]', 'admin');
  await page.fill('input[name="password"], input[type="password"]', 'password');
  await page.click('button[type="submit"], button:has-text("Login")');
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(12000);

  const bodyText = await page.locator('body').innerText();
  const checkingCount = (bodyText.match(/Checking\.\.\./g) || []).length;
  const screenshotPath = 'output/playwright/dashboard-debug.png';
  await page.screenshot({ path: screenshotPath, fullPage: true });

  console.log('BODY_START');
  console.log(bodyText.slice(0, 4000));
  console.log('BODY_END');
  console.log('CHECKING_COUNT', checkingCount);
  console.log('API_EVENTS_START');
  for (const line of apiEvents) console.log(line);
  console.log('API_EVENTS_END');
  console.log('FAILURES_START');
  for (const line of failures) console.log(line);
  console.log('FAILURES_END');
  console.log('CONSOLE_START');
  for (const line of consoleMessages) console.log(line);
  console.log('CONSOLE_END');
  console.log('SCREENSHOT', screenshotPath);
});
