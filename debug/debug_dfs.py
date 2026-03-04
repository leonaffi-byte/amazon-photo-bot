import asyncio, aiohttp, base64, json, sys, os
sys.path.insert(0, '/app')
os.chdir('/app')

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    print("Login:", login)
    print("Password:", password[:8] + "...")

    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as s:
        # 1. Account info
        async with s.get(
            "https://api.dataforseo.com/v3/appendix/user_data",
            headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            d = await r.json()
            print("\n=== Account info (status", r.status, ") ===")
            task   = (d.get("tasks") or [{}])[0]
            result = (task.get("result") or [{}])[0]
            print("  status_code:", task.get("status_code"))
            print("  status_msg: ", task.get("status_message"))
            print("  balance:    ", result.get("money", {}).get("balance"))

        # 2. Test Amazon SERP endpoint
        payload = [{
            "keyword":       "headphones",
            "location_code": 2840,
            "language_code": "en",
            "device":        "desktop",
            "depth":         3,
        }]
        async with s.post(
            "https://api.dataforseo.com/v3/serp/amazon/organic/live/advanced",
            headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            d = await r.json()
            print("\n=== Amazon SERP live/advanced (status", r.status, ") ===")
            task = (d.get("tasks") or [{}])[0]
            print("  status_code:", task.get("status_code"))
            print("  status_msg: ", task.get("status_message"))
            items = ((task.get("result") or [{}])[0]).get("items") or []
            print("  items:      ", len(items))

        # 3. Try the regular (non-live) endpoint as fallback
        async with s.post(
            "https://api.dataforseo.com/v3/serp/amazon/organic/task_post",
            headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            d = await r.json()
            print("\n=== Amazon SERP task_post (status", r.status, ") ===")
            task = (d.get("tasks") or [{}])[0]
            print("  status_code:", task.get("status_code"))
            print("  status_msg: ", task.get("status_message"))

asyncio.run(main())
