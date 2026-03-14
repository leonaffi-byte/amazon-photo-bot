# Phase 6: Admin Tech Debt Cleanup - Research

**Researched:** 2026-03-14
**Domain:** Python async, SQLite queries, python-telegram-bot v20, FastAPI admin dashboard
**Confidence:** HIGH

## Summary

Phase 6 closes two partially-satisfied requirements identified in the v1.0 milestone audit. The gaps are surgical: two hardcoded zeros in `admin_service.py` and one missing `CommandHandler` registration in `adapters/telegram.py`. No architectural changes are needed — the database already has queries that return today's counts, and the webtoken handler logic already exists in legacy `bot.py`. This phase is about wiring up what already exists.

**ADMN-02** is a single-function fix: `admin_service.get_stats()` hardcodes `today_searches=0` and `today_users=0`. The database already has `get_stats_since(since: datetime)` which accepts a `since` parameter and returns `unique_users` and `total_searches` for the window. Passing today's UTC midnight as `since` yields the correct today counts.

**ADMN-06** requires two coordinated changes: (1) add a `webtoken` branch to `bot_core.BotCore.handle_command()` with admin-auth guard and token delivery, and (2) register `CommandHandler("webtoken", self._handle_command)` in `TelegramAdapter.start()`. The legacy `bot.py::webtoken_command` handler is then dead code that should be removed. The `generate_fallback_token()` function in `admin_dashboard/auth.py` is already correct and unchanged.

**Primary recommendation:** Two targeted edits in three files (`admin_service.py`, `bot_core.py`, `adapters/telegram.py`) plus cleanup of `bot.py`. Verifiable immediately without mocks via the existing `test_admin_service.py` and manual Telegram test.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ADMN-02 | Dashboard shows bot statistics (users, searches, clicks, revenue estimates) | `db.get_stats_since()` provides today's counts; `admin_service.BotStats` fields already exist; template already renders `stats.today_searches` and `stats.today_users` |
| ADMN-06 | Admin can view and manage provider health status via web UI — and `/webtoken` command delivers login token via Telegram | Provider health page is complete; gap is solely the missing `CommandHandler("webtoken")` in `TelegramAdapter.start()` and missing branch in `bot_core.handle_command()` |
</phase_requirements>

## Standard Stack

### Core (unchanged — what Phase 3 established)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| aiosqlite | current | Async SQLite queries for today stats | Already in use throughout `database.py` |
| python-telegram-bot | v20+ | `CommandHandler` registration in `TelegramAdapter` | Production Telegram adapter |
| FastAPI + Jinja2 | current | Web dashboard rendering | Already serving stat cards via HTMX polling |

### No new dependencies

This phase introduces zero new packages. All infrastructure exists.

**Installation:**
```bash
# No new installs required
```

## Architecture Patterns

### Existing Code Inventory (what the planner needs to know)

**ADMN-02: Today stats gap**

`admin_service.py` lines 509-521:
```python
async def get_stats() -> BotStats:
    raw = await db.get_stats()
    return BotStats(
        total_users=raw.get("unique_users", 0),
        total_searches=raw.get("total_searches", 0),
        total_clicks=0,          # not tracked
        today_searches=0,        # <-- HARDCODED
        today_users=0,           # <-- HARDCODED
        ...
    )
```

`database.py` already has `get_stats_since(since: datetime) -> dict` (line 1374) which returns:
```python
{
    "unique_users": int,      # DISTINCT user_id count since `since`
    "total_searches": int,    # row count since `since`
    ...
}
```

The fix: compute today's UTC midnight, call `get_stats_since(today_midnight)`, use the result to populate the two fields.

**ADMN-06: /webtoken gap**

`adapters/telegram.py` `TelegramAdapter.start()` registers these commands:
```python
app.add_handler(CommandHandler("start",       self._handle_command))
app.add_handler(CommandHandler("help",        self._handle_command))
app.add_handler(CommandHandler("language",    self._handle_command))
app.add_handler(CommandHandler("providers",   self._handle_command))
app.add_handler(CommandHandler("setloggroup", self._handle_command))
# CommandHandler("webtoken") is MISSING
```

`bot_core.py` `BotCore.handle_command()` has branches for start/help/language/providers/setloggroup but NOT webtoken.

Legacy `bot.py::webtoken_command` (lines 1352-1366) has the correct implementation:
```python
async def webtoken_command(update, context):
    user_id = update.effective_user.id
    if not (user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id)):
        await update.message.reply_text("Unauthorized.")
        return
    from admin_dashboard.auth import generate_fallback_token
    token = generate_fallback_token()
    await update.message.reply_text(
        f"Web admin token \\(expires in 24h\\):\n`{token}`\n\nVisit /admin/login and paste this token\\.",
        parse_mode="MarkdownV2",
    )
```

The `bot_core.handle_command` approach is the correct pattern: all other commands route through `self._on_command` → `bot_core.handle_command`. The `webtoken` branch should follow the same pattern with `self._is_admin(user_id)` and `self.adapter.send_text()`.

**Admin auth pattern in bot_core:**
```python
async def _is_admin(self, user_id: int) -> bool:
    return user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id)
```

**send_text in bot_core** uses MarkdownV2 by default (the adapter handles parse_mode).

### Pattern 1: Today stats via get_stats_since

**What:** Compute UTC midnight of current day, pass to existing `db.get_stats_since()`, extract `unique_users` and `total_searches`.

**When to use:** Anywhere "today" scoped counts are needed from `search_logs`.

```python
# Source: database.py::get_stats_since (line 1374)
from datetime import datetime, timezone

async def get_stats() -> BotStats:
    raw = await db.get_stats()
    today_midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_raw = await db.get_stats_since(today_midnight)
    return BotStats(
        total_users=raw.get("unique_users", 0),
        total_searches=raw.get("total_searches", 0),
        total_clicks=0,
        today_searches=today_raw.get("total_searches", 0),
        today_users=today_raw.get("unique_users", 0),
        searches_per_tag=raw.get("searches_per_tag", {}),
        israel_filter_uses=raw.get("israel_filter_uses", 0),
        last_search=raw.get("last_search"),
    )
```

### Pattern 2: webtoken branch in bot_core.handle_command

**What:** Add `elif command == "webtoken"` branch following the admin-guard pattern used by `setloggroup`.

**When to use:** Any admin-only Telegram command that delivers sensitive data.

```python
# Source: derived from bot.py::webtoken_command + bot_core patterns
elif command == "webtoken":
    if not await self._is_admin(user_id):
        await self.adapter.send_text(chat_id, "Unauthorized\.")
        return
    from admin_dashboard.auth import generate_fallback_token
    token = generate_fallback_token()
    await self.adapter.send_text(
        chat_id,
        f"Web admin token \\(expires in 24h\\):\n`{token}`\n\nVisit /admin/login and paste this token\\.",
    )
```

Note: `adapter.send_text()` in `TelegramAdapter` always sets `parse_mode="MarkdownV2"`, so the caller must escape special characters. The backtick monospace and parenthesis escaping are already correct in the legacy handler.

### Pattern 3: CommandHandler registration in TelegramAdapter.start()

**What:** Add one line to `TelegramAdapter.start()` after existing command registrations.

```python
# Source: adapters/telegram.py::start() — existing pattern
app.add_handler(CommandHandler("webtoken", self._handle_command))
```

This is identical to the other command registrations — `self._handle_command` parses the command name and routes to `self._on_command` → `bot_core.handle_command`.

### Anti-Patterns to Avoid

- **Adding a standalone `CommandHandler` with a new async function in `TelegramAdapter`:** Breaks the routing pattern. All commands must flow through `bot_core.handle_command` for Telegram so the logic is platform-agnostic.
- **Calling `db.get_daily_search_counts(days=1)`:** Returns a list; `get_stats_since()` already returns the right structure and is more robust for today-scoped unique user counts. `get_daily_search_counts` only tracks search rows, not unique users.
- **Adding a new `get_today_stats()` DB function:** Unnecessary — `get_stats_since()` already accepts a datetime parameter.
- **Leaving `bot.py::webtoken_command` as live code:** The audit explicitly calls it dead code. Remove or mark with `# DEAD CODE — production uses TelegramAdapter+BotCore`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Today's search count | New SQL query | `db.get_stats_since(today_midnight)` | Already parameterized, tested, handles timezone |
| Today's unique users | New SQL query | Same `db.get_stats_since()` — `unique_users` field | Same function returns both |
| Webtoken delivery | New standalone handler | `bot_core.handle_command` `elif` branch | Keeps routing consistent across platforms |
| Admin auth check | Inline `config.ADMIN_IDS` check | `self._is_admin(user_id)` | Already combines config + DB admin table |

**Key insight:** Both fixes are wiring problems, not implementation problems. The database queries and handler logic exist — they just aren't connected to the right call sites.

## Common Pitfalls

### Pitfall 1: Timezone drift in "today" boundary

**What goes wrong:** Using `datetime.now()` (naive/local) instead of `datetime.now(timezone.utc)` causes the today boundary to shift by the server's UTC offset. The DB stores `searched_at` as UTC ISO strings.

**Why it happens:** Python's `datetime.now()` is naive and system-timezone-dependent.

**How to avoid:** Always use `datetime.now(timezone.utc)` and `.replace(hour=0, minute=0, second=0, microsecond=0)` to get UTC midnight.

**Warning signs:** Today count shows wrong numbers late at night or varies with server timezone.

### Pitfall 2: MarkdownV2 escaping in token message

**What goes wrong:** Parentheses `()` and hyphens `-` in the token message must be escaped in MarkdownV2. The legacy `bot.py` handler already escapes `(expires in 24h)` as `\(expires in 24h\)`.

**Why it happens:** `adapter.send_text()` in `TelegramAdapter` sets `parse_mode="MarkdownV2"` unconditionally.

**How to avoid:** Copy the exact string from `bot.py::webtoken_command` line 1364 — it is already correct.

**Warning signs:** Telegram throws `BadRequest: Can't parse entities` in logs.

### Pitfall 3: Forgetting to register the CommandHandler in TelegramAdapter

**What goes wrong:** Adding the `webtoken` branch to `bot_core.handle_command` is useless unless `TelegramAdapter.start()` also registers `CommandHandler("webtoken", self._handle_command)`. PTB silently ignores unknown commands.

**Why it happens:** Two-step wiring — the router AND the command registration both need updating.

**How to avoid:** Verify both changes together. Test by actually sending `/webtoken` in Telegram (not just unit-testing the branch).

### Pitfall 4: Not removing bot.py::webtoken_command

**What goes wrong:** Leaving the legacy handler creates confusion — future devs may think `bot.py::build_application()` is still the production path.

**Why it happens:** Easy to forget cleanup step after adding the real implementation.

**How to avoid:** The phase success criterion explicitly states "legacy `bot.py::webtoken_command` handler is either removed or explicitly marked as dead code." Treat this as a hard deliverable.

## Code Examples

Verified patterns from actual codebase:

### admin_service.get_stats() — current (broken) state
```python
# admin_service.py lines 509-521
async def get_stats() -> BotStats:
    raw = await db.get_stats()
    return BotStats(
        total_users=raw.get("unique_users", 0),
        total_searches=raw.get("total_searches", 0),
        total_clicks=0,
        today_searches=0,   # placeholder
        today_users=0,      # placeholder
        ...
    )
```

### database.get_stats_since() — the query to use
```python
# database.py line 1374 — already implemented and used by scheduler/reports
async def get_stats_since(since: datetime) -> dict:
    since_str = since.isoformat()
    async with _get_conn() as db:
        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM search_logs WHERE searched_at >= ?",
            (since_str,),
        ) as cur:
            unique_users = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM search_logs WHERE searched_at >= ?",
            (since_str,),
        ) as cur:
            total_searches = (await cur.fetchone())[0]
        ...
    return {"unique_users": unique_users, "total_searches": total_searches, ...}
```

### TelegramAdapter.start() — current command registrations (location to add webtoken)
```python
# adapters/telegram.py lines 72-77
app.add_handler(CommandHandler("start",       self._handle_command))
app.add_handler(CommandHandler("help",        self._handle_command))
app.add_handler(CommandHandler("language",    self._handle_command))
app.add_handler(CommandHandler("providers",   self._handle_command))
app.add_handler(CommandHandler("setloggroup", self._handle_command))
# ADD: app.add_handler(CommandHandler("webtoken", self._handle_command))
```

### bot_core.handle_command() — admin-guarded command pattern
```python
# bot_core.py lines 861-870 — existing setloggroup pattern to follow
elif command == "setloggroup":
    if not await self._is_admin(user_id):
        return
    import log_group
    log_group.start_listening(int(user_id))
    await self.adapter.send_text(chat_id, "OK, now add me to a group ...")
```

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| `bot.py::build_application()` + handlers | `TelegramAdapter` + `BotCore.handle_command()` | Legacy path is unreachable in production; features added only to `bot.py` are dead |
| `today_searches=0` hardcoded | `db.get_stats_since(today_midnight)` | Today counts become accurate in dashboard stat cards |

**Deprecated/outdated:**
- `bot.py::webtoken_command`: defined but unreachable in production (production uses `TelegramAdapter`+`BotCore`, not `bot.py::build_application()`). Phase 6 must remove or mark dead.
- `bot.py::build_application()`: legacy parallel path. Not touched in this phase but flagged as tech debt.

## Open Questions

1. **Should `/webtoken` regenerate a new token or return the current one?**
   - What we know: `generate_fallback_token()` always generates a new token and stores it, invalidating any previous token. Each call to `/webtoken` will therefore invalidate the previous token.
   - What's unclear: Is invalidating the previous token acceptable? (Phase 3 research flagged this as open.)
   - Recommendation: Accept current behavior — generating a fresh token per `/webtoken` call is the safe default. Token is valid 24h. Admins who want to share the same token should do so manually.

2. **`total_clicks` in BotStats stays 0 — is that acceptable?**
   - What we know: The DB schema does not have a `link_clicks` table tied to user-visible click tracking in the same way as `search_logs`. `get_stats_since()` queries `link_clicks` table which may exist (used in scheduled reports). `db.get_stats()` doesn't return click counts.
   - What's unclear: Whether the dashboard should show real click counts in this phase.
   - Recommendation: Out of scope for Phase 6 (not mentioned in success criteria). Leave `total_clicks=0` as-is. Phase 6 success criteria only mention `today_searches` and `today_users`.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode = auto`) |
| Config file | `pytest.ini` |
| Quick run command | `pytest tests/test_admin_service.py -x -q` |
| Full suite command | `pytest` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ADMN-02 | `today_searches` reflects actual DB search count for today | unit | `pytest tests/test_admin_service.py::TestStatsService::test_get_stats_reflects_logged_searches -x` | exists (partial — doesn't assert today_searches specifically) |
| ADMN-02 | `today_users` reflects actual distinct users for today | unit | `pytest tests/test_admin_service.py -x -q -k "today"` | Wave 0 gap |
| ADMN-06 | `/webtoken` command branch exists in `bot_core.handle_command` | unit | `pytest tests/test_bot.py -x -q -k "webtoken"` | Wave 0 gap |
| ADMN-06 | `/webtoken` delivers token via adapter.send_text | unit | `pytest tests/test_bot.py -x -q -k "webtoken"` | Wave 0 gap |

### Sampling Rate

- **Per task commit:** `pytest tests/test_admin_service.py tests/test_bot.py -x -q`
- **Per wave merge:** `pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_admin_service.py` — Add `test_today_searches_reflects_actual_data` and `test_today_users_reflects_actual_data` (today-specific assertions in the existing `TestStatsService` class)
- [ ] `tests/test_bot.py` or `tests/test_admin_web.py` — Add `test_webtoken_command_admin_only` and `test_webtoken_command_delivers_token` (mock `generate_fallback_token`, assert `send_text` called with token)

## Sources

### Primary (HIGH confidence)

- Direct code inspection: `admin_service.py` lines 509-521 — confirmed hardcoded zeros
- Direct code inspection: `adapters/telegram.py` lines 58-108 — confirmed missing CommandHandler("webtoken")
- Direct code inspection: `database.py` lines 1374-1430 — confirmed `get_stats_since()` returns `unique_users` and `total_searches`
- Direct code inspection: `bot_core.py` lines 815-870 — confirmed handle_command pattern and `_is_admin` helper
- `.planning/v1.0-MILESTONE-AUDIT.md` — authoritative gap documentation

### Secondary (MEDIUM confidence)

- `tests/test_admin_service.py` lines 316-362 — confirms existing test structure and `BotStats` field names
- `admin_dashboard/auth.py` — confirms `generate_fallback_token()` behavior (always regenerates)
- `bot.py` lines 1352-1366 — reference implementation for webtoken message text and escaping

## Metadata

**Confidence breakdown:**
- Gap identification: HIGH — confirmed directly in source code and audit document
- Fix approach: HIGH — all required infrastructure exists; changes are additive/surgical
- Test gaps: HIGH — confirmed no webtoken tests exist, today-specific stat tests absent
- Edge cases: MEDIUM — MarkdownV2 escaping correctness relies on copying the correct string from legacy handler

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable domain — no fast-moving dependencies)
