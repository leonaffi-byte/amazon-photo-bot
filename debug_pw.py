import asyncio, sys, os
sys.path.insert(0, '/app')
os.chdir('/app')

async def main():
    # 1. Check proxy URL
    from israel_scraper import _get_proxy_url
    proxy_url = await _get_proxy_url()
    print(f"Proxy URL: {proxy_url[:30] if proxy_url else 'None'}")

    # 2. Try plain Playwright without proxy first
    from playwright.async_api import async_playwright
    try:
        from playwright_stealth import stealth_async
        has_stealth = True
    except ImportError:
        has_stealth = False
    print(f"Has stealth: {has_stealth}")

    from price_history import _build_proxy_cfg
    proxy_cfg = _build_proxy_cfg(proxy_url)
    print(f"Proxy cfg: {proxy_cfg}")

    async with async_playwright() as pw:
        print("Launching browser...")
        browser = await pw.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(locale="en-US", viewport={"width":1280,"height":800})
        page = await ctx.new_page()
        if has_stealth:
            await stealth_async(page)

        print("Navigating to CCC...")
        try:
            resp = await page.goto(
                "https://camelcamelcamel.com/product/B08N5WRWNW",
                wait_until="domcontentloaded",
                timeout=25000
            )
            print(f"Response status: {resp.status if resp else 'None'}")
            html = await page.content()
            print(f"HTML length: {len(html)}")
            # Check for Cloudflare challenge
            if "Enable JavaScript" in html or "cf-browser-verification" in html:
                print(">>> CLOUDFLARE BLOCK detected")
            elif "camelcamelcamel" in html.lower():
                print(">>> Page loaded OK")
                # Look for prices
                import re
                prices = re.findall(r'\$[\d,]+\.\d{2}', html)
                print(f"Prices: {prices[:10]}")
            else:
                print(f">>> Unknown response, title: {html[:200]}")
        except Exception as e:
            print(f"Navigation error: {e}")
            try:
                html = await page.content()
                print(f"Got {len(html)} chars anyway")
            except Exception:
                pass
        finally:
            await browser.close()
    print("Done.")

asyncio.run(main())
