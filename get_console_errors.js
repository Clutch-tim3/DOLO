const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.new_page();
  
  page.on('console', msg => {
    if (msg.type() === 'error') {
      console.log(`Console Error: ${msg.text()}`);
    }
  });

  page.on('pageerror', error => {
    console.log(`Page Error: ${error.message}`);
  });

  await page.goto('http://127.0.0.1:5000', { waitUntil: 'networkidle' });
  
  // Try to click the bid button
  await page.click('#openBidBtn').catch(e => console.log("Click error:", e.message));
  
  await browser.close();
})();
