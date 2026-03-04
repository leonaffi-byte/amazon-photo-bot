import asyncio, aiohttp, base64, sys, os
sys.path.insert(0, '/app'); os.chdir('/app')

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}
    payload = [{"keyword": "headphones", "location_code": 2840, "language_code": "en",
                "device": "desktop", "depth": 3}]

    endpoints = [
        # Amazon variations
        ("Amazon live regular",    "POST", "https://api.dataforseo.com/v3/serp/amazon/organic/live/regular", payload),
        ("Amazon products",         "POST", "https://api.dataforseo.com/v3/merchant/amazon/products/task_post",
          [{"keyword": "headphones", "location_code": 2840, "language_code": "en", "priority": 1}]),
        # Sandbox (free test)
        ("Amazon sandbox",         "POST", "https://sandbox.dataforseo.com/v3/serp/amazon/organic/live/advanced", payload),
        # Google (to confirm SERP works at all)
        ("Google live regular",    "POST", "https://api.dataforseo.com/v3/serp/google/organic/live/regular",
          [{"keyword": "headphones", "location_code": 2840, "language_code": "en",
            "device": "desktop", "depth": 3, "se_domain": "google.com"}]),
    ]

    async with aiohttp.ClientSession() as s:
        for name, method, url, data in endpoints:
            try:
                if method == "POST":
                    r = await s.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=12))
                else:
                    r = await s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12))
                d  = await r.json()
                task = (d.get("tasks") or [{}])[0]
                code = task.get("status_code")
                msg  = task.get("status_message", "")
                items = len(((task.get("result") or [{}])[0]).get("items") or [])
                print(f"{name:30s} → {code} {msg[:40]}  items={items}")
            except Exception as e:
                print(f"{name:30s} → ERROR: {e}")

asyncio.run(main())
