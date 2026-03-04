import asyncio, aiohttp, base64, sys, os
sys.path.insert(0, '/app'); os.chdir('/app')

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    tests = [
        # Merchant live endpoints (no language_code)
        ("Merchant live advanced",  "https://api.dataforseo.com/v3/merchant/amazon/products/live/advanced",
         [{"keyword": "headphones", "location_code": 2840, "language_name": "English", "priority": 1}]),
        # Async task_post without language
        ("Merchant task_post",      "https://api.dataforseo.com/v3/merchant/amazon/products/task_post",
         [{"keyword": "headphones", "location_code": 2840, "language_name": "English", "priority": 1}]),
        # Amazon ASIN lookup
        ("ASIN lookup",             "https://api.dataforseo.com/v3/merchant/amazon/asin/task_post",
         [{"asin": "B0863TXGM3", "location_code": 2840, "language_name": "English", "priority": 1}]),
        # Check available endpoints/products
        ("Available endpoints GET", "https://api.dataforseo.com/v3/appendix/user_data", None),
    ]

    async with aiohttp.ClientSession() as s:
        for name, url, data in tests:
            try:
                if data is None:
                    r = await s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10))
                else:
                    r = await s.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10))
                d    = await r.json()
                task = (d.get("tasks") or [{}])[0]
                code = task.get("status_code")
                msg  = task.get("status_message", "")
                # For user_data, show available services
                if "user_data" in url:
                    result = (task.get("result") or [{}])[0]
                    services = list((result.get("api", {}) or {}).keys())
                    print(f"Available API groups: {services[:10]}")
                else:
                    items = len(((task.get("result") or [{}])[0]).get("items") or [])
                    print(f"{name:30s} → {code} {msg[:50]}  items={items}")
            except Exception as e:
                print(f"{name:30s} → ERROR: {e}")

asyncio.run(main())
