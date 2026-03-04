"""
Fetch completed merchant tasks + explore popular_products structure
"""
import asyncio, aiohttp, base64, json, sys, os
sys.path.insert(0, '/app'); os.chdir('/app')

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as s:

        # 1. Fetch the completed merchant tasks
        print("=== Fetching completed merchant task ===")
        task_id = "03040734-1448-0209-0000-d608db0a5d37"
        async with s.get(
            f"https://api.dataforseo.com/v3/merchant/amazon/products/task_get/advanced/{task_id}",
            headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            d = await r.json()
            task  = (d.get("tasks") or [{}])[0]
            code  = task.get("status_code")
            msg   = task.get("status_message","")
            items = ((task.get("result") or [{}])[0]).get("items") or []
            print(f"Status: {code} {msg} — {len(items)} items")
            for item in items[:3]:
                print(f"\n  title:     {item.get('title','')[:60]}")
                print(f"  asin:      {item.get('asin','')}")
                print(f"  price_from:{item.get('price_from')}")
                print(f"  price_to:  {item.get('price_to')}")
                print(f"  currency:  {item.get('currency','')}")
                rating = item.get('rating') or {}
                print(f"  rating:    {rating.get('value')} ({rating.get('votes_count')} reviews)")
                print(f"  image:     {'yes ('+item['image_url'][:40]+')' if item.get('image_url') else 'no'}")
                print(f"  url:       {(item.get('url') or '')[:60]}")
                print(f"  is_prime:  {item.get('is_amazon_choice') or item.get('is_prime')}")

        # 2. Explore popular_products structure in Google Organic
        print("\n\n=== Google Organic popular_products full structure ===")
        async with s.post(
            "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
            headers=headers,
            json=[{"keyword": "wireless headphones", "location_code": 2840, "language_code": "en",
                   "device": "desktop", "depth": 10, "se_domain": "google.com"}],
            timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            d = await r.json()
            items = ((d.get("tasks") or [{}])[0].get("result") or [{}])[0].get("items") or []
        
        for item in items:
            if item.get("type") == "popular_products":
                sub_items = item.get("items") or []
                print(f"Found popular_products block with {len(sub_items)} products")
                for p in sub_items[:3]:
                    print(f"\n  sub-type:  {p.get('type')}")
                    print(f"  title:     {p.get('title','')[:60]}")
                    print(f"  price:     {p.get('price',{})}")
                    print(f"  url:       {(p.get('url') or '')[:70]}")
                    print(f"  image:     {'yes' if p.get('image_url') else 'no'}")
                    print(f"  source:    {p.get('source','')}")
                    print(f"  rating:    {p.get('rating',{})}")
                    # Check for Amazon
                    if 'amazon' in (p.get('url') or '').lower() or 'amazon' in (p.get('source') or '').lower():
                        print(f"  *** AMAZON PRODUCT ***")
                        import re
                        m = re.search(r'/dp/([A-Z0-9]{10})', p.get('url',''))
                        if m: print(f"  ASIN: {m.group(1)}")
                break

asyncio.run(main())
