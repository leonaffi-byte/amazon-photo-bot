"""
Exhaustive test of ALL DataForSEO endpoints that could return Amazon product data.
"""
import asyncio, aiohttp, base64, json, sys, os
sys.path.insert(0, '/app'); os.chdir('/app')

async def post(s, headers, url, payload):
    try:
        async with s.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            d = await r.json()
            task = (d.get("tasks") or [{}])[0]
            code  = task.get("status_code")
            msg   = task.get("status_message","")
            items = ((task.get("result") or [{}])[0]).get("items") or []
            return code, msg, items
    except Exception as e:
        return None, str(e), []

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as s:

        # 1. Google Organic - check ALL item types for a shopping query
        print("=== Google Organic 'wireless headphones' — all item types ===")
        code, msg, items = await post(s, headers,
            "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
            [{"keyword": "wireless headphones", "location_code": 2840, "language_code": "en",
              "device": "desktop", "depth": 10, "se_domain": "google.com"}])
        print(f"Status: {code} {msg}")
        type_map = {}
        for item in items:
            t = item.get("type","")
            type_map[t] = type_map.get(t,0) + 1
        print(f"Item types: {type_map}")
        # Show shopping-type items if any
        for item in items:
            if "shop" in item.get("type","").lower() or "product" in item.get("type","").lower():
                print(f"  Shopping item: {json.dumps(item, indent=2)[:300]}")

        # 2. Google Organic Advanced - look for shopping_element, amazon items
        print("\n=== Google Organic Advanced 'buy headphones amazon' ===")
        code, msg, items = await post(s, headers,
            "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
            [{"keyword": "buy headphones amazon", "location_code": 2840, "language_code": "en",
              "device": "desktop", "depth": 10, "se_domain": "google.com"}])
        print(f"Status: {code} {msg}")
        type_map = {}
        for item in items:
            t = item.get("type","")
            type_map[t] = type_map.get(t,0) + 1
            if "shop" in t.lower():
                title = item.get("title","")[:40]
                price = item.get("price",{})
                url_  = item.get("url","")[:60]
                print(f"  [{t}] {title} | price={price} | {url_}")
        print(f"Item types: {type_map}")

        # 3. DataForSEO Labs - Amazon Related Keywords (might return product info)
        print("\n=== DFS Labs Amazon Related Keywords ===")
        code, msg, items = await post(s, headers,
            "https://api.dataforseo.com/v3/dataforseo_labs/amazon/related_keywords/live",
            [{"keyword": "wireless headphones", "location_name": "United States",
              "language_name": "English (United States)", "limit": 5}])
        print(f"Status: {code} {msg} — {len(items)} items")
        if items:
            print(f"Sample: {json.dumps(items[0], indent=2)[:300]}")

        # 4. DataForSEO Labs - Amazon Bulk Search Volume
        print("\n=== DFS Labs Amazon Bulk Search Volume ===")
        code, msg, items = await post(s, headers,
            "https://api.dataforseo.com/v3/dataforseo_labs/amazon/bulk_search_volume/live",
            [{"keywords": ["wireless headphones", "sony headphones", "bluetooth earbuds"],
              "location_name": "United States", "language_name": "English (United States)"}])
        print(f"Status: {code} {msg} — {len(items)} items")
        if items:
            print(f"Sample: {json.dumps(items[0], indent=2)[:300]}")

        # 5. Check tasks_ready for merchant API (any pending tasks?)
        print("\n=== Merchant Amazon Tasks Ready ===")
        async with s.get(
            "https://api.dataforseo.com/v3/merchant/amazon/products/tasks_ready",
            headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            d = await r.json()
            task = (d.get("tasks") or [{}])[0]
            result = (task.get("result") or [])
            print(f"Status: {task.get('status_code')} — {len(result)} ready tasks")
            for t in result[:3]:
                print(f"  Task: {t.get('id')} status={t.get('status_code')}")

asyncio.run(main())
