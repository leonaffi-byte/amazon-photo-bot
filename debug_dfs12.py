"""
Test with valid ASINs + Related Keywords full structure + On-Page price extraction
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

    # Known valid ASINs
    SONY_XM5 = "B09XS7JWHH"     # Sony WH-1000XM5
    AIRPODS   = "B0CHWVKPT8"     # Apple AirPods Pro 2

    async with aiohttp.ClientSession() as s:

        # ---- 1. Ranked Keywords with valid ASIN ----
        print("="*55)
        print("RANKED KEYWORDS — Sony WH-1000XM5")
        print("="*55)
        d = await post(s, headers, "https://api.dataforseo.com/v3/dataforseo_labs/amazon/ranked_keywords/live",
            [{"asin": SONY_XM5, "location_code": 2840, "language_code": "en", "limit": 5}])
        task   = (d.get("tasks") or [{}])[0]
        result = ((task.get("result") or [{}])[0])
        items  = result.get("items") or []
        print(f"Status: {task.get('status_code')} | total_count={result.get('total_count')} | returned={len(items)}")
        for item in items[:3]:
            kd   = item.get("keyword_data") or {}
            ki   = kd.get("keyword_info") or {}
            serp = (item.get("ranked_serp_element") or {}).get("serp_item") or {}
            print(f"\n  keyword:    {kd.get('keyword','')}")
            print(f"  search_vol: {ki.get('search_volume')}")
            print(f"  rank:       {serp.get('rank_absolute')}")
            print(f"  title:      {str(serp.get('title',''))[:55]}")
            print(f"  price:      ${serp.get('price_from')} {serp.get('currency','')}")
            print(f"  image:      {'✅' if serp.get('image_url') else '❌'}")
            print(f"  rating:     {(serp.get('rating') or {}).get('value')}")
            print(f"  asin:       {serp.get('asin')}")
            print(f"  is_prime:   {serp.get('is_prime')}")

        # ---- 2. Related Keywords ----
        print("\n" + "="*55)
        print("RELATED KEYWORDS — 'wireless headphones'")
        print("="*55)
        d = await post(s, headers, "https://api.dataforseo.com/v3/dataforseo_labs/amazon/related_keywords/live",
            [{"keyword": "wireless headphones", "location_code": 2840, "language_code": "en", "limit": 10, "depth": 1}])
        task  = (d.get("tasks") or [{}])[0]
        items = ((task.get("result") or [{}])[0]).get("items") or []
        print(f"Status: {task.get('status_code')} | {len(items)} items")
        for item in items[:5]:
            kd = item.get("keyword_data") or {}
            ki = (kd.get("keyword_info") or {})
            print(f"  '{kd.get('keyword','')}'  vol={ki.get('search_volume')}  cpc=${ki.get('cpc')}")

        # ---- 3. Product Competitors with valid ASIN ----
        print("\n" + "="*55)
        print("PRODUCT COMPETITORS — Sony WH-1000XM5")
        print("="*55)
        d = await post(s, headers, "https://api.dataforseo.com/v3/dataforseo_labs/amazon/product_competitors/live",
            [{"asin": SONY_XM5, "location_code": 2840, "language_code": "en", "limit": 8}])
        task   = (d.get("tasks") or [{}])[0]
        result = ((task.get("result") or [{}])[0])
        items  = result.get("items") or []
        print(f"Status: {task.get('status_code')} | total={result.get('total_count')} | returned={len(items)}")
        if items:
            print(f"Item keys: {list(items[0].keys())}")
        for item in items[:5]:
            print(f"  asin={item.get('asin')}  avg_pos={item.get('avg_position')}  intersections={item.get('intersections')}  title='{str(item.get('title',''))[:40]}'")

        # ---- 4. On-Page on valid Amazon ASIN — what's in meta.content? ----
        print("\n" + "="*55)
        print("ON-PAGE — Sony WH-1000XM5 (check meta.content for price)")
        print("="*55)
        d = await post(s, headers, "https://api.dataforseo.com/v3/on_page/instant_pages",
            [{"url": f"https://www.amazon.com/dp/{SONY_XM5}",
              "load_resources": False, "enable_javascript": True}])
        task  = (d.get("tasks") or [{}])[0]
        items = ((task.get("result") or [{}])[0]).get("items") or []
        if items:
            item = items[0]
            meta = item.get("meta") or {}
            canonical = meta.get("canonical","")
            import re
            asin_match = re.search(r'/dp/([A-Z0-9]{10})', canonical)
            print(f"URL fetched:  {item.get('url')}")
            print(f"Canonical:    {canonical}")
            print(f"ASIN in URL:  {asin_match.group(1) if asin_match else 'none'}")
            print(f"Title:        {meta.get('title','')[:80]}")
            print(f"Description:  {meta.get('description','')[:100]}")
            print(f"H1:           {(meta.get('htags') or {}).get('h1',[''])[0][:60]}")
            content = meta.get("content") or {}
            print(f"content keys: {list(content.keys()) if isinstance(content, dict) else type(content)}")
            social = meta.get("social_media_tags") or {}
            print(f"social keys:  {list(social.keys())}")
            # OG tags often have price
            for k,v in social.items():
                print(f"  og:{k} = {str(v)[:80]}")

asyncio.run(main())
