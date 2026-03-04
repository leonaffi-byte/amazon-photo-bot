"""
Test DataForSEO Merchant timing + full item structure + ASIN extraction
"""
import asyncio, aiohttp, base64, json, sys, os, time
sys.path.insert(0, '/app'); os.chdir('/app')

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as s:
        # 1. Full item structure from completed task
        print("=== Full item structure (first item) ===")
        task_id = "03040734-1448-0209-0000-d608db0a5d37"
        async with s.get(
            f"https://api.dataforseo.com/v3/merchant/amazon/products/task_get/advanced/{task_id}",
            headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            d = await r.json()
            items = ((d.get("tasks") or [{}])[0].get("result") or [{}])[0].get("items") or []
            print(f"Total items: {len(items)}")
            # Show all fields of first item
            for item in items[:5]:
                url = item.get("url","")
                import re
                m = re.search(r'/dp/([A-Z0-9]{10})', url)
                asin = m.group(1) if m else item.get("asin","") or item.get("data_asin","") or "NO_ASIN"
                if asin != "NO_ASIN":
                    print(f"\n✅ Item with ASIN:")
                    print(f"   title:      {item.get('title','')[:60]}")
                    print(f"   asin:       {asin}")
                    print(f"   price:      ${item.get('price_from')}")
                    print(f"   rating:     {(item.get('rating') or {}).get('value')} ({(item.get('rating') or {}).get('votes_count')} reviews)")
                    print(f"   image:      {'yes' if item.get('image_url') else 'no'}")
                    print(f"   is_prime:   {item.get('is_prime')}")
                    print(f"   is_amazon_choice: {item.get('is_amazon_choice')}")
                    print(f"   all keys:   {list(item.keys())}")
                    break
            
            # Count items WITH vs WITHOUT extractable ASIN
            with_asin = 0
            for item in items:
                url = item.get("url","")
                m = re.search(r'/dp/([A-Z0-9]{10})', url)
                asin = m.group(1) if m else item.get("asin","") or item.get("data_asin","")
                if asin:
                    with_asin += 1
            print(f"\nItems with extractable ASIN: {with_asin}/{len(items)}")

        # 2. Time a fresh high-priority task
        print("\n=== Timing high-priority task ===")
        t0 = time.time()
        payload = [{"keyword": "sony headphones", "location_name": "United States",
                    "language_name": "English (United States)", "priority": 2}]
        async with s.post(
            "https://api.dataforseo.com/v3/merchant/amazon/products/task_post",
            headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            d = await r.json()
            task = (d.get("tasks") or [{}])[0]
            task_id2 = task.get("id","")
            print(f"Task created in {time.time()-t0:.1f}s: {task_id2}")

        # Poll until done
        for attempt in range(18):  # up to 90 seconds
            await asyncio.sleep(5)
            elapsed = time.time() - t0
            async with s.get(
                f"https://api.dataforseo.com/v3/merchant/amazon/products/task_get/advanced/{task_id2}",
                headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                d = await r.json()
                task = (d.get("tasks") or [{}])[0]
                code  = task.get("status_code")
                items = ((task.get("result") or [{}])[0]).get("items") or []
                print(f"  {elapsed:.0f}s → {code} — {len(items)} items")
                if code == 20000 and items:
                    print(f"✅ Completed in {elapsed:.1f} seconds!")
                    break

asyncio.run(main())
