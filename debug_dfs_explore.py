"""
Exhaustive exploration of all DataForSEO endpoints available on this account.
Test every endpoint that might return useful Amazon product data.
"""
import asyncio, aiohttp, base64, json, sys, os
sys.path.insert(0, '/app'); os.chdir('/app')

async def post(s, headers, url, payload, timeout=12):
    try:
        async with s.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            d = await r.json()
            task = (d.get("tasks") or [{}])[0]
            return task.get("status_code"), task.get("status_message",""), task
    except Exception as e:
        return None, str(e)[:60], {}

async def get(s, headers, url, timeout=10):
    try:
        async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            d = await r.json()
            task = (d.get("tasks") or [{}])[0]
            return task.get("status_code"), task.get("status_message",""), task
    except Exception as e:
        return None, str(e)[:60], {}

async def main():
    import sqlite3
    db = sqlite3.connect('/app/data/bot_data.db')
    login    = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_login'").fetchone()[0]
    password = db.execute("SELECT key_value FROM api_keys WHERE key_name='dataforseo_password'").fetchone()[0]
    creds   = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    ASIN = "B0CHX3QBCH"   # Sony WH-1000XM5
    KW   = "wireless headphones"

    async with aiohttp.ClientSession() as s:

        tests = [
            # --- DFS Labs Amazon (keyword analytics) ---
            ("DFS Labs / Amazon Ranked Keywords",
             "POST", "https://api.dataforseo.com/v3/dataforseo_labs/amazon/ranked_keywords/live",
             [{"asin": ASIN, "location_code": 2840, "language_code": "en", "limit": 5}]),

            ("DFS Labs / Amazon Related Keywords",
             "POST", "https://api.dataforseo.com/v3/dataforseo_labs/amazon/related_keywords/live",
             [{"keyword": KW, "location_code": 2840, "language_code": "en", "limit": 5}]),

            ("DFS Labs / Amazon Bulk Search Volume",
             "POST", "https://api.dataforseo.com/v3/dataforseo_labs/amazon/bulk_search_volume/live",
             [{"keywords": [KW, "sony headphones", "noise cancelling"], "location_code": 2840, "language_code": "en"}]),

            ("DFS Labs / Amazon Product Rank Overview",
             "POST", "https://api.dataforseo.com/v3/dataforseo_labs/amazon/product_rank_overview/live",
             [{"asin": ASIN, "location_code": 2840, "language_code": "en"}]),

            ("DFS Labs / Amazon Product Competitors",
             "POST", "https://api.dataforseo.com/v3/dataforseo_labs/amazon/product_competitors/live",
             [{"asin": ASIN, "location_code": 2840, "language_code": "en", "limit": 5}]),

            ("DFS Labs / Amazon Product Keyword Intersections",
             "POST", "https://api.dataforseo.com/v3/dataforseo_labs/amazon/product_keyword_intersections/live",
             [{"asins": [ASIN, "B09XS7JWHH"], "location_code": 2840, "language_code": "en", "limit": 5}]),

            # --- Merchant Amazon (async tasks) ---
            ("Merchant / Amazon ASIN page",
             "POST", "https://api.dataforseo.com/v3/merchant/amazon/asin/task_post",
             [{"asin": ASIN, "location_name": "United States", "language_name": "English (United States)", "priority": 2}]),

            # --- Content Analysis ---
            ("Content Analysis / Search",
             "POST", "https://api.dataforseo.com/v3/content_analysis/search/live",
             [{"keyword": KW, "limit": 3}]),

            # --- On-Page API ---
            ("On-Page / Instant Pages",
             "POST", "https://api.dataforseo.com/v3/on_page/instant_pages",
             [{"url": f"https://www.amazon.com/dp/{ASIN}", "load_resources": False, "enable_javascript": True}]),

            # --- Domain Analytics ---
            ("Domain Analytics / Whois Overview",
             "POST", "https://api.dataforseo.com/v3/domain_analytics/whois/overview/live",
             [{"keywords": ["amazon.com"], "limit": 1}]),

            # --- Keywords Data: Amazon ---
            ("Keywords Data / Amazon Search Volume",
             "POST", "https://api.dataforseo.com/v3/keywords_data/amazon/search_volume/live",
             [{"keywords": [KW, "sony headphones"], "location_code": 2840, "language_code": "en"}]),

            ("Keywords Data / Amazon Keywords for Keywords",
             "POST", "https://api.dataforseo.com/v3/keywords_data/amazon/keywords_for_keywords/live",
             [{"keywords": [KW], "location_code": 2840, "language_code": "en", "limit": 5}]),

            # --- SERP: Google Shopping (alternative to Amazon SERP) ---
            ("SERP / Google Shopping live regular",
             "POST", "https://api.dataforseo.com/v3/serp/google/shopping/live/regular",
             [{"keyword": KW, "location_code": 2840, "language_code": "en", "device": "desktop"}]),

            ("SERP / Google Shopping live advanced",
             "POST", "https://api.dataforseo.com/v3/serp/google/shopping/live/advanced",
             [{"keyword": KW, "location_code": 2840, "language_code": "en", "device": "desktop"}]),

            # --- Merchant Google Products ---
            ("Merchant / Google Products live",
             "POST", "https://api.dataforseo.com/v3/merchant/google/products/live/advanced",
             [{"keyword": KW, "location_code": 2840, "language_code": "en", "se_domain": "google.com"}]),
        ]

        for name, method, url, payload in tests:
            code, msg, task = await post(s, headers, url, payload)
            result_list = (task.get("result") or [{}])
            items = (result_list[0] if result_list else {}).get("items") or []
            # Also check top-level result for non-items structures
            result_data = result_list[0] if result_list else {}
            has_data = len(items) > 0 or (result_data and result_data != {})
            
            status_icon = "✅" if code == 20000 else ("⚠️" if code and code < 40000 else "❌")
            print(f"{status_icon} [{code}] {name}")
            if code == 20000 and items:
                sample = items[0]
                keys = list(sample.keys())
                print(f"   {len(items)} items | keys: {keys[:8]}")
                # Show useful fields
                for fld in ['keyword', 'title', 'asin', 'price', 'search_volume', 'competition', 'url', 'domain']:
                    if fld in sample:
                        print(f"   {fld}: {str(sample[fld])[:80]}")
            elif code == 20000 and result_data:
                print(f"   result keys: {list(result_data.keys())[:10]}")
            elif code != 20000:
                print(f"   → {msg}")
            print()

asyncio.run(main())
