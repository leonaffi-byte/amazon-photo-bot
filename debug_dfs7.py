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
        # Post a fresh task with high priority
        payload = [{"keyword": "wireless headphones", "location_name": "United States",
                    "language_name": "English (United States)", "priority": 2}]
        async with s.post(
            "https://api.dataforseo.com/v3/merchant/amazon/products/task_post",
            headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            d = await r.json()
            task = (d.get("tasks") or [{}])[0]
            task_id = task.get("id","")
            print(f"Task created: {task_id}")

        # Poll until done (max 60s)
        for attempt in range(12):
            await asyncio.sleep(5)
            print(f"Polling attempt {attempt+1}...")
            async with s.get(
                f"https://api.dataforseo.com/v3/merchant/amazon/products/task_get/advanced/{task_id}",
                headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                d = await r.json()
                task = (d.get("tasks") or [{}])[0]
                code  = task.get("status_code")
                msg   = task.get("status_message","")
                items = ((task.get("result") or [{}])[0]).get("items") or []
                print(f"  Status: {code} {msg} — {len(items)} items")

                if code == 20000 and items:
                    print("\n=== SUCCESS! Sample items: ===")
                    for item in items[:3]:
                        print(f"  title:  {item.get('title','')[:60]}")
                        print(f"  price:  {item.get('price_from')} - {item.get('price_to')}")
                        print(f"  asin:   {item.get('asin','')}")
                        print(f"  rating: {item.get('rating',{})}")
                        print(f"  image:  {'yes' if item.get('image_url') else 'no'}")
                        print()
                    break
                elif code not in (20100, 40601, 40602):
                    print(f"  Terminal status — stopping")
                    break

asyncio.run(main())
