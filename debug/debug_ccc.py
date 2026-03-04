import asyncio, sys, os
sys.path.insert(0, '/app')
os.chdir('/app')

async def main():
    from price_history import _fetch_rendered_html, _parse_ccc_html
    asin = "B0CK3TG3DS"
    url = f"https://camelcamelcamel.com/product/{asin}"
    print(f"Fetching {url} ...")
    html = await _fetch_rendered_html(url, timeout_ms=20000)
    if not html:
        print("Got no HTML at all")
        return
    print(f"HTML length: {len(html)}")
    # Show page title and first price-like content
    from bs4 import BeautifulSoup
    import re
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string if soup.title else "no title"
    print(f"Page title: {title}")
    # Look for any dollar amounts
    prices = re.findall(r'\$[\d,]+\.\d{2}', html)
    print(f"Dollar amounts in page: {prices[:15]}")
    # Look for keywords
    for kw in ['amazon', 'lowest', 'current', 'average', 'stats', 'camel']:
        count = html.lower().count(kw)
        if count:
            print(f"  '{kw}' appears {count} times")
    # Show a snippet around 'lowest' if found
    idx = html.lower().find('lowest')
    if idx > 0:
        print(f"\nContext around 'lowest': ...{html[max(0,idx-50):idx+100]}...")
    # Try parsing
    result = _parse_ccc_html(asin, html)
    print(f"\nParse result: {result}")

asyncio.run(main())
