import asyncio, aiohttp, base64, json, sys, os
sys.path.insert(0, '/app'); os.chdir('/app')

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}
    query   = "headphones"

    tests = [
        ("Google Shopping live",
         "https://api.dataforseo.com/v3/serp/google/shopping/live/advanced",
         [{"keyword": query, "location_code": 2840, "language_code": "en",
           "device": "desktop", "depth": 10, "se_domain": "google.com"}]),
        ("Google Organic site:amazon",
         "https://api.dataforseo.com/v3/serp/google/organic/live/regular",
         [{"keyword": f"{query} site:amazon.com", "location_code": 2840,
           "language_code": "en", "device": "desktop", "depth": 10, "se_domain": "google.com"}]),
    ]

    async with aiohttp.ClientSession() as s:
        for name, url, data in tests:
            r = await s.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15))
            d = await r.json()
            task   = (d.get("tasks") or [{}])[0]
            code   = task.get("status_code")
            msg    = task.get("status_message", "")
            items  = ((task.get("result") or [{}])[0]).get("items") or []
            print(f"\n{'='*50}")
            print(f"{name}: {code} {msg} — {len(items)} items")
            for item in items[:3]:
                itype = item.get("type","")
                title = item.get("title","")[:50]
                price = item.get("price", {})
                url_  = item.get("url","")[:60]
                src   = item.get("source","")
                rating = item.get("rating", {})
                image  = item.get("image_url","")[:40] if item.get("image_url") else ""
                asin   = ""
                import re
                m = re.search(r'/dp/([A-Z0-9]{10})', url_)
                if m: asin = m.group(1)
                print(f"  [{itype}] {title}")
                print(f"  price={price}  source={src}  asin={asin}")
                print(f"  rating={rating}  img={'yes' if image else 'no'}")

asyncio.run(main())
