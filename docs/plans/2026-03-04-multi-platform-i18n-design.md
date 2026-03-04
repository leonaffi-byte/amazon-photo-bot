# Multi-Platform Bot + i18n Design

**Date:** 2026-03-04  
**Status:** Approved

## Overview

Expand the Amazon Photo Bot from Telegram-only to 7 messaging platforms with Hebrew/Russian/English language support.

## Platforms

| Platform | API | Transport | Rich Messages |
|---|---|---|---|
| Telegram | python-telegram-bot (existing) | Polling | Inline keyboards, photo editing |
| WhatsApp | Meta Cloud API | Webhook | 3 buttons, lists (10 items) |
| Instagram | Meta Messenger API | Webhook | Quick replies (13) |
| Facebook Messenger | Meta Messenger Platform | Webhook | Buttons (3), carousels |
| Viber | Viber Bot API | Webhook | Keyboard (24 buttons) |
| Discord | discord.py | WebSocket | Embeds, buttons (5/row), dropdowns |
| LINE | LINE Messaging API | Webhook | Flex messages, carousels |

## Architecture: Platform Adapter Pattern

### Core Principle

Separate platform-specific I/O from business logic. The core bot logic calls abstract adapter methods; each platform implements its own adapter.

```
Core Bot Logic (bot_core.py)
    ↓ calls
PlatformAdapter (adapters/base.py)
    ↓ implemented by
TelegramAdapter | WhatsAppAdapter | InstagramAdapter | MessengerAdapter | ViberAdapter | DiscordAdapter | LineAdapter
```

### PlatformAdapter Interface

```python
class PlatformAdapter(ABC):
    platform_name: str
    max_caption_length: int
    max_message_length: int
    supports_photo_edit: bool
    supports_inline_buttons: bool
    max_buttons_per_row: int
    supports_carousels: bool

    # Lifecycle
    async def start_listening(self) -> None
    async def stop(self) -> None

    # Receiving
    async def download_photo(self, event) -> bytes
    async def get_user_id(self, event) -> str
    async def get_user_lang(self, event) -> str | None

    # Sending
    async def send_text(self, chat_id, text, buttons=None) -> MessageRef
    async def send_photo(self, chat_id, photo_url, caption, buttons=None) -> MessageRef
    async def send_carousel(self, chat_id, items: list[CarouselItem]) -> MessageRef
    async def edit_text(self, msg_ref, text, buttons=None) -> None
    async def edit_photo(self, msg_ref, photo_url, caption, buttons=None) -> None
    async def delete_message(self, msg_ref) -> None
```

`MessageRef`: platform-agnostic reference (chat_id + message_id).  
`Button`: `{label, url}` or `{label, callback_data}`.  
`CarouselItem`: `{image_url, title, subtitle, buttons}`.

### Platform Capabilities

| Platform | Photo Edit | Carousel | Max Buttons | Caption Limit |
|---|---|---|---|---|
| Telegram | Yes | No | Unlimited | 1024 |
| WhatsApp | No | No | 3 | 1024 |
| Instagram | No | No | 13 | 1000 |
| Messenger | No | Yes | 3 | 640 |
| Viber | No | No | 24 | 512 |
| Discord | Yes (embeds) | No | 5/row | 4096 |
| LINE | No | Yes | 13 | 2000 |

### Product Navigation Strategy

- **Telegram/Discord**: Edit photo message in-place (current behavior).
- **Messenger/LINE**: Use native carousels — send all products at once.
- **WhatsApp/Instagram/Viber**: Send new photo message per product navigation.

## Core Bot Logic (bot_core.py)

Extracted from current `bot.py`. Platform-agnostic orchestration:

```python
class BotCore:
    def __init__(self, adapter: PlatformAdapter, db: Database)
    
    async def handle_photo(self, event) -> None
    async def handle_callback(self, user_id, data) -> None
    async def handle_text_search(self, user_id, text) -> None
```

Session key: `"{platform}:{user_id}"` (e.g., `"whatsapp:972501234567"`).

## i18n

### Locale Files

```
locale/
  en.json   # English (default)
  he.json   # Hebrew
  ru.json   # Russian
```

### Translation Function

```python
def t(key: str, lang: str = "en", **kwargs) -> str:
    template = _strings.get(lang, {}).get(key) or _strings["en"][key]
    return template.format(**kwargs) if kwargs else template
```

### Language Selection Flow

1. New user sends first message → bot shows language picker (3 buttons).
2. User picks → stored in DB (`users.lang` column).
3. All subsequent messages rendered in chosen language.
4. Change via `/language` command or "Change language" button.

### Scope

- All user-facing strings translated (welcome, loading, identification, products, errors, buttons).
- Admin panel stays English-only.
- RTL (Hebrew) handled natively by platforms.

## Formatting (formatter.py)

Replaces `style.py`. Takes structured data, renders per-platform:

```python
class Formatter:
    def __init__(self, platform: str, lang: str = "en")
    def identification_card(self, result, is_admin=False) -> str
    def product_caption(self, item, index, total, ...) -> str
    def welcome(self) -> str
    def loading_vision(self) -> str
    def escape(self, text: str) -> str
```

Platform-specific formatting:
- Telegram: MarkdownV2 (`*bold*`, `_italic_`, heavy escaping)
- WhatsApp: WhatsApp markdown (`*bold*`, `_italic_`)
- Discord: Discord markdown (`**bold**`, `*italic*`, embeds)
- Others: Plain text with emoji

## Webhook Server

Extends existing aiohttp server (port 8080, currently used for URL shortener):

```
POST /webhook/whatsapp   → WhatsAppAdapter.handle_webhook
POST /webhook/instagram  → InstagramAdapter.handle_webhook
POST /webhook/messenger  → MessengerAdapter.handle_webhook
POST /webhook/viber      → ViberAdapter.handle_webhook
POST /webhook/line       → LineAdapter.handle_webhook
GET  /webhook/{platform} → Verification endpoint
```

Telegram stays on polling. Discord uses its own WebSocket connection.

## Configuration

Platforms enabled by presence of API tokens in `.env`:

```
WHATSAPP_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_VERIFY_TOKEN=...
INSTAGRAM_TOKEN=...
MESSENGER_TOKEN=...
MESSENGER_VERIFY_TOKEN=...
VIBER_TOKEN=...
DISCORD_TOKEN=...
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_TOKEN=...
```

No token = platform not started.

## Database Changes

- `users` table: add `lang` column (default `NULL` = prompt for language).
- `users` table: add `platform` column (`"telegram"`, `"whatsapp"`, etc.).
- `search_logs`: add `platform` column for analytics.
- User ID format: `"{platform}:{native_id}"` as primary key.

## File Structure

```
amazon-photo-bot/
├── main.py                    # Starts enabled adapters + webhook server
├── bot_core.py                # Platform-agnostic orchestration
├── i18n.py                    # t() translation function
├── formatter.py               # Platform-aware formatting
├── webhook_server.py          # Unified webhook routes
├── adapters/
│   ├── base.py                # PlatformAdapter ABC, MessageRef, Button
│   ├── telegram.py            # Telegram (PTB polling)
│   ├── whatsapp.py            # WhatsApp Cloud API
│   ├── instagram.py           # Instagram Messenger API
│   ├── messenger.py           # Facebook Messenger
│   ├── viber.py               # Viber Bot API
│   ├── discord_adapter.py     # Discord (discord.py)
│   ├── line.py                # LINE Messaging API
│   └── shared_meta.py         # Shared Meta API logic
├── locale/
│   ├── en.json
│   ├── he.json
│   └── ru.json
├── providers/                 # (unchanged)
├── search_backends/           # (unchanged)
├── admin.py                   # Telegram-only admin (unchanged)
├── database.py                # (+ lang, platform columns)
└── ...
```

## Shared DB, Unified Admin

- All platforms share one SQLite database.
- Admin panel remains on Telegram only (existing inline keyboard UI).
- Stats/analytics include platform breakdown.

## Deployment

- Single Docker container, all platforms in one process.
- `main.py` starts all enabled adapters concurrently in the asyncio event loop.
- Webhook server listens on port 8080 (already exposed).
