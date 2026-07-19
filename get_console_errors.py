import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        # Capture console messages
        page.on("console", lambda msg: print(f"Console {msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: print(f"Page Error: {err}"))
        
        await page.goto("http://127.0.0.1:5000", wait_until="networkidle")
        
        try:
            await page.click("#openBidBtn")
            print("Clicked #openBidBtn successfully.")
            await page.wait_for_timeout(1000)
        except Exception as e:
            print("Click error:", e)
            
        await browser.close()

asyncio.run(main())
