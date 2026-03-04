# Optimizations + Per-User Subtag Tracking

## 1. Image Compression
- Resize to max 1024px longest side, JPEG quality 85%
- Apply before sending to AI providers
- Location: new helper in bot.py or image_analyzer.py
- Cuts base64 payload ~70-80%

## 2. Session & Cache Cleanup
- Periodic asyncio task every 5 min
- Evict sessions older than 10 min
- Evict analysis cache entries past TTL
- Clean rate limiter buckets for inactive users

## 3. Shared aiohttp Sessions
- One persistent ClientSession per search backend
- Reuse across requests for TCP connection pooling
- Create on first use, close on shutdown

## 4. Vision Provider Timeouts
- Wrap all provider calls with asyncio.wait_for(timeout=45)
- Currently only Groq has a timeout

## 5. Per-User Subtag
- Append &subtag=tg_<user_id> to all affiliate URLs
- Modify AmazonItem.affiliate_url() to accept subtag param
- Pass user_id through the flow to URL construction
- Log subtag in search_logs

## 6. Minor Fixes
- Fix OpenAI MIME type detection (detect actual format)
- Fix Groq missing sanitize_query()
- Remove hard 1s sleep before fallback search
