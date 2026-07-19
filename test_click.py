import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:5000")
        
        # Get button bounding box
        btn = await page.locator("#openBidBtn").bounding_box()
        if btn:
            x = btn['x'] + btn['width'] / 2
            y = btn['y'] + btn['height'] / 2
            
            # Find what is at that point
            element_info = await page.evaluate(f"""
                () => {{
                    const el = document.elementFromPoint({x}, {y});
                    return el ? el.tagName + '#' + el.id + '.' + el.className : 'none';
                }}
            """)
            print(f"Element at button center ({x}, {y}):", element_info)
        else:
            print("Button not found")
            
        await browser.close()

asyncio.run(main())
