# Multi-Platform Bot + i18n Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand the Amazon Photo Bot to support 7 messaging platforms (Telegram, WhatsApp, Instagram, Facebook Messenger, Viber, Discord, LINE) with Hebrew/Russian/English i18n.

**Architecture:** Platform Adapter pattern with per-platform implementations. Core bot logic in bot_core.py calls adapter methods. Shared DB, unified Telegram admin, single container.

**Tech Stack:** Python 3.11+, aiohttp (webhooks), python-telegram-bot (Telegram), discord.py (Discord), line-bot-sdk (LINE), aiosqlite (DB).

**Design doc:** docs/plans/2026-03-04-multi-platform-i18n-design.md

---

## Phase 1: Foundation (Adapter Interface + i18n + Formatter)

### Task 1: Create PlatformAdapter abstract base class

**Files:**
- Create: `adapters/__init__.py`
- Create: `adapters/base.py`

Create the adapters directory and write base.py with:
- `MessageRef` dataclass (platform, chat_id, message_id, raw)
- `Button` dataclass (label, url, callback_data)
- `CarouselItem` dataclass (image_url, title, subtitle, url, buttons)
- `PlatformAdapter` ABC with:
  - Capability flags: max_caption_length, max_message_length, supports_photo_edit, supports_inline_buttons, max_buttons_per_row, supports_carousels
  - Abstract methods: start(), stop(), download_photo(), get_user_id(), get_platform_user_id(), send_text(), send_photo(), edit_text(), delete_message()
  - Default implementations: send_carousel() (sends items one-by-one), edit_photo() (raises NotImplementedError)

**Commit:** `feat: add PlatformAdapter abstract base class`

---

### Task 2: Create i18n system

**Files:**
- Create: `i18n.py`
- Create: `locale/en.json`, `locale/he.json`, `locale/ru.json`

i18n.py provides:
- `load_locales()` -- reads JSON files from locale/ directory
- `t(key, lang, **kwargs)` -- returns translated string with format substitution, falls back to English
- `available_languages()` -- returns [(code, native_name)] for language picker
- `SUPPORTED_LANGS = ("en", "he", "ru")`, `DEFAULT_LANG = "en"`

Locale JSON files contain all user-facing strings extracted from current style.py:
- Onboarding: choose_language, lang_set, welcome, help
- Loading: loading_vision, loading_search, loading_similar
- Identification: id_title, id_brand, id_category, id_features, id_confidence, id_search_query
- Confidence: confidence_high/medium/low
- Filter: filter_ask, filter_all, filter_israel
- Product: product_price, product_rating, product_reviews, product_prime, product_sold_by, product_israel_*, product_counter
- Buttons: btn_shop, btn_prev, btn_next, btn_filter_toggle, btn_try_different, btn_similar, btn_related
- Errors: err_no_results, err_no_photo, err_rate_limit, err_analysis_failed, err_search_failed, err_generic
- Search: text_search_prompt

Hebrew and Russian translations for all keys. Hebrew strings are RTL (platforms handle this natively).

**Commit:** `feat: add i18n system with English, Hebrew, Russian locale files`

---

### Task 3: Create platform-aware Formatter

**Files:**
- Create: `formatter.py`

Replaces style.py for multi-platform use. `Formatter` class takes platform and lang in constructor.

Methods:
- `_bold(text)`, `_italic(text)`, `_esc(text)`, `_link(label, url)` -- platform-specific formatting
- `_stars(rating)`, `_format_reviews(count)` -- display helpers
- `welcome()`, `help_text()`, `loading_vision()`, `loading_search()`, `language_picker()`
- `identification_card(result, is_admin)` -- AI identification result with brand, category, features, confidence, search query
- `product_caption(item, index, total, short_url, israel_status, is_admin)` -- product card with price, rating, badges, Israel status, shop link, counter
- `error(key)`, `text_search_loading(query)`

Platform formatting differences:
- Telegram: MarkdownV2 (`*bold*`, heavy escaping)
- WhatsApp: `*bold*`, `_italic_`, minimal escaping
- Discord: `**bold**`, `*italic*`, `[links](url)`
- Instagram/Messenger/Viber/LINE: plain text with emoji

Caption length limits enforced per platform (Telegram 1024, WhatsApp 1024, Messenger 640, Viber 512, Discord 4096, LINE 2000, Instagram 1000).

**Commit:** `feat: add platform-aware Formatter`

---

### Task 4: Add user language support to database

**Files:**
- Modify: `database.py`

Add users table:
```sql
CREATE TABLE IF NOT EXISTS users (
    user_key TEXT PRIMARY KEY,
    platform TEXT,
    lang TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Add migration logic to add columns if table already exists (try ALTER TABLE, catch duplicate column errors).

New async functions:
- `get_user_lang(user_key) -> str | None`
- `set_user_lang(user_key, lang) -> None`
- `ensure_user(user_key, platform) -> None`

**Commit:** `feat: add user language/platform tracking to database`

---

## Phase 2: Telegram Adapter (Refactor existing bot.py)

### Task 5: Create TelegramAdapter

**Files:**
- Create: `adapters/telegram.py`

Implements PlatformAdapter wrapping python-telegram-bot (PTB).

Capabilities: supports_photo_edit=True, supports_inline_buttons=True, max_buttons_per_row=8, max_caption_length=1024.

Constructor takes callbacks: on_photo, on_callback, on_text, on_command.

`start()` builds PTB Application, registers handlers (CommandHandler, MessageHandler, CallbackQueryHandler), starts polling.

PTB handler methods (_handle_photo, _handle_callback, _handle_text, _handle_command) extract user_id/chat_id and delegate to callbacks.

PlatformAdapter methods:
- download_photo: update.message.photo[-1] -> get_file -> download_as_bytearray
- send_text: bot.send_message with MarkdownV2
- send_photo: bot.send_photo with MarkdownV2
- edit_text: bot.edit_message_text
- edit_photo: bot.edit_message_media with InputMediaPhoto
- delete_message: bot.delete_message
- _build_keyboard: converts list[list[Button]] to InlineKeyboardMarkup

**Commit:** `feat: add TelegramAdapter implementing PlatformAdapter`

---

### Task 6: Create bot_core.py -- extract platform-agnostic logic from bot.py

**Files:**
- Create: `bot_core.py`

This is the biggest task. Extract ALL business logic from bot.py into BotCore class.

`BotCore.__init__(adapter: PlatformAdapter)` -- stores adapter, sessions dict, rate limits, analysis cache, background tasks set.

Key methods (copy logic from bot.py, replace Telegram calls with adapter calls, replace style.py calls with Formatter calls):

**handle_photo(event):**
1. Get user_key via adapter.get_platform_user_id()
2. Check lang -- if None, call _ask_language() and return
3. Rate limit check (same deque logic)
4. Download photo via adapter.download_photo()
5. Compress image (_compress_image, same as bot.py)
6. Check analysis cache (same dedup logic)
7. Send loading message via adapter.send_text()
8. Call analyse_image() from providers/manager.py
9. Build identification card via Formatter.identification_card()
10. Build filter keyboard using Button objects
11. Edit loading message via adapter.edit_text()
12. Store UserSession

**handle_callback(user_id, chat_id, data, event):**
Route by data prefix (same as bot.py):
- "lang:*" -- set language, send welcome
- "filter:yes/no" -- search Amazon, render results
- "nav:next/prev" -- navigate products
- "nav:change" -- toggle filter
- "nav:try" -- re-search with next provider
- "use:N" -- pick provider result
- "dfs:similar:ASIN" -- similar products
- "dfs:related:keyword" -- related search

**handle_text_search(user_id, chat_id, text, event):**
Same logic as bot.py but using adapter/formatter

**handle_command(user_id, chat_id, command, args, event):**
- start -> send welcome
- help -> send help
- language -> show language picker

**_ask_language(chat_id, user_key):**
Send language picker with 3 buttons (English / Hebrew / Russian)

**_render_product(user_key, chat_id, ...):**
- Build caption via Formatter.product_caption()
- Build navigation buttons (prev/next/shop/filter/similar)
- If adapter.supports_photo_edit and existing_msg_ref: edit photo in-place
- Elif adapter.supports_carousels: send carousel
- Else: send new photo message

**_search_and_render(user_key, chat_id, israel_only):**
- Call search_amazon() (same as bot.py)
- Apply Israel filter if needed
- Render first product
- Start background tasks for Israel verification + price history

Preserve from bot.py:
- _compress_image function (unchanged)
- UserSession dataclass (key changes to platform:user_id)
- _periodic_cleanup coroutine
- All DataForSEO Labs integration
- Background task tracking (_background_tasks set)
- Lazy-load pagination logic

**Commit:** `feat: add BotCore with platform-agnostic bot logic`

---

### Task 7: Update main.py to use adapter pattern

**Files:**
- Modify: `main.py`

Replace current PTB-only startup:
1. Call load_locales() at startup
2. Create TelegramAdapter with callbacks that delegate to BotCore
3. Create BotCore(telegram_adapter) and wire it up
4. Start adapter
5. Keep asyncio event loop running
6. Graceful shutdown on exit

**Commit:** `feat: update main.py to multi-adapter startup`

---

### Task 8: Integration test -- verify Telegram still works

Build and deploy, test manually:
- Send photo -> AI identification -> filter keyboard
- Select filter -> Amazon search -> product display
- Navigate next/prev
- Text search
- Admin panel still works
- Language picker works for new users

Fix any issues.

**Commit:** `fix: resolve integration issues from adapter refactor`

---

## Phase 3: WhatsApp Adapter

### Task 9: Create shared Meta API helpers

**Files:**
- Create: `adapters/shared_meta.py`

Functions:
- `verify_webhook_signature(payload, signature, app_secret)` -- HMAC-SHA256 verification
- `send_graph_api(endpoint, token, data, session)` -- POST to graph.facebook.com/v19.0
- `download_media(media_id, token, session)` -- GET media URL, then download bytes

**Commit:** `feat: add shared Meta Graph API helpers`

---

### Task 10: Create WhatsApp adapter

**Files:**
- Create: `adapters/whatsapp.py`

Capabilities: supports_photo_edit=False, supports_inline_buttons=True (max 3 buttons), supports_carousels=False.

Webhook handlers:
- handle_webhook_verify (GET) -- return hub.challenge
- handle_webhook (POST) -- verify signature, process messages

Message processing:
- image type -> on_photo callback
- interactive.button_reply -> on_callback
- interactive.list_reply -> on_callback
- text type -> on_text (or on_command if starts with /)

Sending:
- send_text: POST {phone_id}/messages with type=text
- send_text with buttons: POST with type=interactive, interactive.type=button (max 3 reply buttons, title max 20 chars)
- send_photo: POST with type=image + link + caption. Buttons sent as follow-up interactive message (WhatsApp does not support buttons on image messages)
- edit_text: not supported -- sends new message instead
- delete_message: not supported -- no-op

**Commit:** `feat: add WhatsApp Cloud API adapter`

---

### Task 11: Create webhook server

**Files:**
- Create: `webhook_server.py`

`create_webhook_app(adapters)` -- creates aiohttp.web.Application:
- For each adapter with handle_webhook: add POST /webhook/{platform}
- For each adapter with handle_webhook_verify: add GET /webhook/{platform}
- Add GET /health endpoint

Update main.py to start webhook server on port 8080 alongside existing shortener.

**Commit:** `feat: add unified webhook server`

---

## Phase 4: Remaining Platform Adapters

### Task 12: Create Instagram adapter

**Files:** Create `adapters/instagram.py`

Uses Meta Messenger API (same Graph API). Supports receiving images via DM, sending images + quick replies (up to 13). No inline buttons, no message editing.

Webhook format: messaging events with attachments array for images.
Sending: POST /{page_id}/messages with recipient.id and message.attachment.

**Commit:** `feat: add Instagram Messenger API adapter`

---

### Task 13: Create Facebook Messenger adapter

**Files:** Create `adapters/messenger.py`

Uses Messenger Platform API. Supports receiving images, sending images + buttons (max 3), generic template carousel (up to 10 elements). No message editing.

Implement send_carousel() using generic template with image_url, title, subtitle, buttons per element.

**Commit:** `feat: add Facebook Messenger adapter with carousel support`

---

### Task 14: Create Viber adapter

**Files:** Create `adapters/viber.py`

Uses Viber Bot API (chatapi.viber.com/pa/). Webhook-based. Supports keyboard buttons (up to 24), rich media messages. No message editing.

API: POST /pa/send_message with type=picture and keyboard object.
Webhook: message events with media URL for images.

**Commit:** `feat: add Viber Bot API adapter`

---

### Task 15: Create Discord adapter

**Files:** Create `adapters/discord_adapter.py`

Uses discord.py library (WebSocket, not webhooks). Supports receiving image attachments, embeds with rich formatting, buttons (ActionRow, max 5/row), message editing.

Unique: starts discord.Client in asyncio loop (like Telegram polling). Implements on_message event handler.

**Commit:** `feat: add Discord adapter with embed and button support`

---

### Task 16: Create LINE adapter

**Files:** Create `adapters/line.py`

Uses LINE Messaging API. Webhook-based. Supports Flex Messages (rich layouts), carousel template (up to 12 columns). No message editing.

API: POST api.line.me/v2/bot/message/reply and /push.
Implement send_carousel() using carousel container with Flex Message columns.

**Commit:** `feat: add LINE Messaging API adapter with Flex Message support`

---

### Task 17: Register all adapters in main.py

Modify main.py to conditionally create and start each adapter based on env var presence. Wire up webhook server with all webhook-based adapters.

**Commit:** `feat: register all platform adapters in main.py`

---

## Phase 5: Config, Docker, and Deployment

### Task 18: Update config.py

Add env vars: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN, META_APP_SECRET, INSTAGRAM_TOKEN, INSTAGRAM_PAGE_ID, MESSENGER_TOKEN, MESSENGER_VERIFY_TOKEN, VIBER_TOKEN, VIBER_BOT_NAME, DISCORD_TOKEN, LINE_CHANNEL_SECRET, LINE_CHANNEL_TOKEN.

**Commit:** `feat: add platform API config vars`

---

### Task 19: Update Dockerfile and requirements.txt

Add to requirements.txt: `discord.py>=2.3.0`, `line-bot-sdk>=3.0.0`. Other adapters use aiohttp (already installed).

**Commit:** `feat: add Discord and LINE SDK to dependencies`

---

### Task 20: Create .env.example

Document all env vars with comments.

**Commit:** `docs: add .env.example`

---

## Phase 6: Testing and Polish

### Task 21: Test each platform adapter

For each platform: verify webhook verification, photo flow, text search, button callbacks, product display, language picker.

### Task 22: Verify Telegram still works end-to-end

Full regression test of existing functionality.

### Task 23: Update admin panel stats

Add platform breakdown to stats in admin.py.

**Commit:** `feat: add per-platform stats to admin panel`

---

### Task 24: Final deployment

```bash
ssh root@5.189.145.27
cd /opt/amazon-photo-bot
git pull origin main
docker compose build
docker compose down && docker compose up -d
docker logs amazon-photo-bot --tail 20
```

---

## Implementation Order Summary

| Phase | Tasks | Description |
|---|---|---|
| 1 | 1-4 | Foundation: adapter base, i18n, formatter, DB |
| 2 | 5-8 | Telegram adapter + bot_core refactor |
| 3 | 9-11 | WhatsApp adapter + webhooks |
| 4 | 12-17 | Instagram, Messenger, Viber, Discord, LINE |
| 5 | 18-20 | Config, Docker, deployment |
| 6 | 21-24 | Testing, polish, deploy |

**Critical path:** Phase 1 -> Phase 2 (must verify Telegram still works before adding more platforms) -> Phase 3-4 (can be parallelized per platform) -> Phase 5 -> Phase 6.
