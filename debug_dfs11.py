"""
Deep exploration of On-Page Instant Pages (Amazon scraper!) + Product Competitors
"""
import asyncio, aiohttp, base64, json, sys, os
sys.path.insert(0, '/app'); os.chdir('/app')

async def post(s, headers, url, payload, timeout=20):
    async with s.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        return await r.json()

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    ASIN = "B0CHX3QBCH"   # Sony WH-1000XM5 headphones

    async with aiohttp.ClientSession() as s:

        # =============================================
        # 1. On-Page Instant Pages — full meta dump
        # =============================================
        print("="*60)
        print("ON-PAGE / INSTANT PAGES — Amazon product page scrape")
        print("="*60)
        d = await post(s, headers, "https://api.dataforseo.com/v3/on_page/instant_pages",
            [{"url": f"https://www.amazon.com/dp/{ASIN}",
              "load_resources": False,
              "enable_javascript": True,
              "custom_js": "document.querySelectorAll('#productTitle, #priceblock_ourprice, .a-price .a-offscreen, #acrCustomerReviewText, #averageCustomerReviews .a-color-secondary').forEach(e=>e.style.display='block');",
              }])
        task = (d.get("tasks") or [{}])[0]
        print(f"Status: {task.get('status_code')} {task.get('status_message')}")
        result = ((task.get("result") or [{}])[0])
        items  = result.get("items") or []
        if items:
            item = items[0]
            print(f"\nPage: {item.get('url')}")
            print(f"Status code: {item.get('status_code')}")
            print(f"OnPage score: {item.get('onpage_score')}")
            
            meta = item.get("meta") or {}
            print(f"\n--- META ---")
            print(f"title:       {meta.get('title','')[:80]}")
            print(f"description: {meta.get('description','')[:120]}")
            print(f"h1s:         {(meta.get('htags') or {}).get('h1','')}")
            print(f"canonical:   {meta.get('canonical','')}")
            print(f"charset:     {meta.get('charset','')}")
            print(f"meta keys:   {list(meta.keys())}")
            
            # Check for structured data (JSON-LD with product schema)
            content = item.get("page_title","") or ""
            print(f"\npage_title:  {content[:100]}")
            
            # Check for extended fields
            for k,v in item.items():
                if k not in ('meta','xpath','resource_type','status_code','location','url','page_timing','onpage_score','total_dom_size'):
                    print(f"  {k}: {str(v)[:100]}")

        # =============================================
        # 2. On-Page with custom_js to extract product data
        # =============================================
        print("\n" + "="*60)
        print("ON-PAGE / INSTANT PAGES — with structured_data enabled")
        print("="*60)
        d = await post(s, headers, "https://api.dataforseo.com/v3/on_page/instant_pages",
            [{"url": f"https://www.amazon.com/dp/{ASIN}",
              "load_resources": False,
              "enable_javascript": True,
              "enable_browser_rendering": True,
              "store_raw_html": False,
              }])
        task = (d.get("tasks") or [{}])[0]
        print(f"Status: {task.get('status_code')} {task.get('status_message')}")
        items = ((task.get("result") or [{}])[0]).get("items") or []
        if items:
            item = items[0]
            meta = item.get("meta") or {}
            print(f"title:       {meta.get('title','')[:80]}")
            print(f"description: {meta.get('description','')[:120]}")
            # Look for any price/product data
            for k,v in item.items():
                if isinstance(v, (dict, list)) and k not in ('meta', 'page_timing'):
                    print(f"  {k}: {json.dumps(v)[:150]}")

        # =============================================
        # 3. Product Competitors — full structure
        # =============================================
        print("\n" + "="*60)
        print("DFS LABS / AMAZON PRODUCT COMPETITORS")
        print("="*60)
        d = await post(s, headers, "https://api.dataforseo.com/v3/dataforseo_labs/amazon/product_competitors/live",
            [{"asin": ASIN, "location_code": 2840, "language_code": "en", "limit": 10}])
        task = (d.get("tasks") or [{}])[0]
        print(f"Status: {task.get('status_code')} {task.get('status_message')}")
        result = ((task.get("result") or [{}])[0])
        items  = result.get("items") or []
        print(f"Total competitors: {result.get('total_count')} | returned: {len(items)}")
        if items:
            print(f"Keys: {list(items[0].keys())}")
            for item in items[:5]:
                print(f"\n  asin:          {item.get('asin','')}")
                print(f"  title:         {item.get('title','')[:60]}")
                print(f"  avg_position:  {item.get('avg_position')}")
                print(f"  intersections: {item.get('intersections')}")
                # Check for price/image in competitor data
                for k in ['price','image','image_url','price_from','rating','url']:
                    if item.get(k):
                        print(f"  {k}: {item[k]}")

        # =============================================
        # 4. DFS Labs Ranked Keywords — full structure
        # =============================================
        print("\n" + "="*60)
        print("DFS LABS / AMAZON RANKED KEYWORDS — what does it return?")
        print("="*60)
        d = await post(s, headers, "https://api.dataforseo.com/v3/dataforseo_labs/amazon/ranked_keywords/live",
            [{"asin": ASIN, "location_code": 2840, "language_code": "en", "limit": 5}])
        task = (d.get("tasks") or [{}])[0]
        result = ((task.get("result") or [{}])[0])
        items  = result.get("items") or []
        print(f"Status: {task.get('status_code')} | {len(items)} items")
        if items:
            print(f"Keys: {list(items[0].keys())}")
            sample = items[0]
            kd = sample.get("keyword_data") or {}
            ms = (kd.get("keyword_info") or {})
            print(f"  keyword:   {kd.get('keyword','')}")
            print(f"  search_vol:{ms.get('search_volume')}")
            ranked = sample.get("ranked_serp_element") or {}
            serp   = (ranked.get("serp_item") or {})
            print(f"  rank:      {serp.get('rank_absolute')}")
            print(f"  asin:      {serp.get('asin')}")
            print(f"  title:     {str(serp.get('title',''))[:60]}")
            print(f"  price:     {serp.get('price_from')} {serp.get('currency')}")
            print(f"  image:     {'yes' if serp.get('image_url') else 'no'}")
            print(f"  rating:    {(serp.get('rating') or {}).get('value')}")
            print(f"  serp keys: {list(serp.keys())}")

asyncio.run(main())
