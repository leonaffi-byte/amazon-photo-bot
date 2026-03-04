import asyncio, aiohttp, base64, json, sys, os
sys.path.insert(0, '/app'); os.chdir('/app')

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}"}

    async with aiohttp.ClientSession() as s:
        r = await s.get("https://api.dataforseo.com/v3/appendix/user_data",
                        headers=headers, timeout=aiohttp.ClientTimeout(total=10))
        d = await r.json()
    
    task   = (d.get("tasks") or [{}])[0]
    result = (task.get("result") or [{}])[0]
    print("Full account data:")
    print(json.dumps(result, indent=2))

asyncio.run(main())
