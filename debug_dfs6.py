import asyncio, aiohttp, base64, json, sys, os, time
sys.path.insert(0, '/app'); os.chdir('/app')

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    # Correct field names: location_name + language_name (not location_code/language_code)
    payload_correct = [{
        "keyword":       "headphones",
        "location_name": "United States",
        "language_name": "English (United States)",
        "priority":      2,
    }]

    async with aiohttp.ClientSession() as s:
        # Test 1: Merchant Products task_post with CORRECT fields
        print("=== Merchant Products task_post (correct fields) ===")
        async with s.post(
            "https://api.dataforseo.com/v3/merchant/amazon/products/task_post",
            headers=headers, json=payload_correct, timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            d = await r.json()
            task = (d.get("tasks") or [{}])[0]
            print(f"Status: {task.get('status_code')} — {task.get('status_message')}")
            task_id = (task.get("id") or "")
            print(f"Task ID: {task_id}")

        # Test 2: Merchant Products live/advanced (if it exists)
        print("\n=== Merchant Products live/advanced ===")
        async with s.post(
            "https://api.dataforseo.com/v3/merchant/amazon/products/live/advanced",
            headers=headers, json=payload_correct, timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            d = await r.json()
            task = (d.get("tasks") or [{}])[0]
            code = task.get("status_code")
            msg  = task.get("status_message","")
            items = len(((task.get("result") or [{}])[0]).get("items") or [])
            print(f"Status: {code} — {msg}  items={items}")
            if items > 0:
                item = ((task.get("result") or [{}])[0]).get("items")[0]
                print(f"Sample item: {json.dumps(item, indent=2)[:300]}")

        # Test 3: If task_post worked, poll for result
        if task_id:
            print(f"\n=== Polling for task {task_id} ===")
            await asyncio.sleep(5)
            async with s.get(
                f"https://api.dataforseo.com/v3/merchant/amazon/products/task_get/advanced/{task_id}",
                headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                d = await r.json()
                task = (d.get("tasks") or [{}])[0]
                code = task.get("status_code")
                msg  = task.get("status_message","")
                items = len(((task.get("result") or [{}])[0]).get("items") or [])
                print(f"Status: {code} — {msg}  items={items}")
                if items > 0:
                    item = ((task.get("result") or [{}])[0]).get("items")[0]
                    print(f"Sample: {json.dumps(item, indent=2)[:400]}")

asyncio.run(main())
