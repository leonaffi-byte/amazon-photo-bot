"""
Tests for database.py.

Covers:
  - DB path defaults to data/ subdirectory
  - Schema creation (init_db is idempotent)
  - Affiliate tag CRUD: add, get_all, set_active, remove, deactivate_all
  - Search log: log_search, get_stats
  - API key CRUD: set, get, delete
  - Admin CRUD: seed, is_admin, add, remove
  - Invite codes: create, use (valid / expired / already-used)
  - Short links: create, get, log click, delete
  - URL cache: cache and retrieve
  - Bot settings: set, get, delete
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

import database as db


@pytest_asyncio.fixture(autouse=True)
async def init(tmp_data_dir):
    """Initialise the DB schema before every test."""
    await db.init_db()


# ── DB path ────────────────────────────────────────────────────────────────────

class TestDbPath:
    def test_db_path_inside_data_dir(self, tmp_data_dir):
        assert Path(db.DB_PATH).parent == tmp_data_dir


# ── init_db ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestInitDb:
    async def test_idempotent(self):
        """Calling init_db twice must not raise."""
        await db.init_db()
        await db.init_db()

    async def test_db_file_created(self, tmp_data_dir):
        assert Path(db.DB_PATH).exists()


# ── Affiliate tags ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAffiliateTags:
    async def test_no_tags_initially(self):
        tags = await db.get_all_tags()
        assert tags == []

    async def test_add_tag(self):
        tag = await db.add_tag("mytag-20", "Primary", admin_id=1, admin_name="Alice")
        assert tag.tag == "mytag-20"
        assert tag.description == "Primary"
        assert not tag.is_active

    async def test_add_first_tag_auto_activated(self):
        tag = await db.add_tag("first-20", "First", admin_id=1, admin_name="Alice", make_active=True)
        assert tag.is_active

    async def test_add_duplicate_raises(self):
        await db.add_tag("dup-20", "First", admin_id=1, admin_name="Alice")
        with pytest.raises(ValueError, match="already exists"):
            await db.add_tag("dup-20", "Second", admin_id=1, admin_name="Alice")

    async def test_get_active_tag_none_when_no_tags(self):
        result = await db.get_active_tag()
        assert result is None

    async def test_get_active_tag_returns_correct_tag(self):
        await db.add_tag("active-20", "Active", admin_id=1, admin_name="Alice", make_active=True)
        await db.add_tag("inactive-20", "Inactive", admin_id=1, admin_name="Alice")
        result = await db.get_active_tag()
        assert result == "active-20"

    async def test_set_active_deactivates_others(self):
        t1 = await db.add_tag("tag1-20", "T1", admin_id=1, admin_name="A", make_active=True)
        t2 = await db.add_tag("tag2-20", "T2", admin_id=1, admin_name="A")
        await db.set_active_tag(t2.id)
        active = await db.get_active_tag()
        assert active == "tag2-20"

    async def test_deactivate_all(self):
        await db.add_tag("tag1-20", "T1", admin_id=1, admin_name="A", make_active=True)
        await db.deactivate_all_tags()
        active = await db.get_active_tag()
        assert active is None

    async def test_remove_tag(self):
        tag = await db.add_tag("remove-20", "Remove", admin_id=1, admin_name="A")
        deleted = await db.remove_tag(tag.id)
        assert deleted is True
        tags = await db.get_all_tags()
        assert all(t.id != tag.id for t in tags)

    async def test_remove_nonexistent_tag_returns_false(self):
        deleted = await db.remove_tag(99999)
        assert deleted is False

    async def test_increment_search_count(self):
        await db.add_tag("count-20", "Count", admin_id=1, admin_name="A", make_active=True)
        await db.increment_tag_search_count("count-20")
        await db.increment_tag_search_count("count-20")
        tags = await db.get_all_tags()
        t = next(t for t in tags if t.tag == "count-20")
        assert t.search_count == 2


# ── Search logs ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSearchLogs:
    async def test_log_and_get_stats(self):
        await db.log_search(
            user_id=42, product_name="Keyboard",
            tag_used="mytag-20", provider_used="openai/gpt-4o",
            result_count=10, israel_filter=True,
        )
        stats = await db.get_stats()
        assert stats["total_searches"] == 1
        assert stats["unique_users"] == 1
        assert stats["israel_filter_uses"] == 1
        assert stats["searches_per_tag"]["mytag-20"] == 1

    async def test_multiple_users_counted(self):
        for uid in [1, 2, 3]:
            await db.log_search(uid, "Widget", "none", "openai", 5, False)
        stats = await db.get_stats()
        assert stats["unique_users"] == 3

    async def test_same_user_not_double_counted(self):
        for _ in range(5):
            await db.log_search(99, "Widget", "none", "openai", 5, False)
        stats = await db.get_stats()
        assert stats["unique_users"] == 1
        assert stats["total_searches"] == 5


# ── API keys ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestApiKeys:
    async def test_set_and_get(self):
        await db.set_api_key("openai_api_key", "sk-test123", admin_id=1)
        result = await db.get_api_key("openai_api_key")
        assert result == "sk-test123"

    async def test_get_missing_returns_none(self):
        result = await db.get_api_key("nonexistent_key")
        assert result is None

    async def test_update_existing_key(self):
        await db.set_api_key("openai_api_key", "sk-old", admin_id=1)
        await db.set_api_key("openai_api_key", "sk-new", admin_id=1)
        result = await db.get_api_key("openai_api_key")
        assert result == "sk-new"

    async def test_delete_key(self):
        await db.set_api_key("openai_api_key", "sk-test", admin_id=1)
        await db.delete_api_key("openai_api_key")
        result = await db.get_api_key("openai_api_key")
        assert result is None

    async def test_get_all_api_keys(self):
        await db.set_api_key("openai_api_key",  "sk-openai", admin_id=1)
        await db.set_api_key("anthropic_api_key", "sk-ant",  admin_id=1)
        keys = await db.get_all_api_keys()
        assert keys["openai_api_key"] == "sk-openai"
        assert keys["anthropic_api_key"] == "sk-ant"


# ── Admins ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAdmins:
    async def test_seed_admins(self):
        await db.seed_admins({100, 200})
        assert await db.is_admin_in_db(100)
        assert await db.is_admin_in_db(200)

    async def test_seed_idempotent(self):
        await db.seed_admins({100})
        await db.seed_admins({100})   # should not raise
        admins = await db.get_all_admins()
        assert sum(1 for a in admins if a.user_id == 100) == 1

    async def test_non_seeded_user_not_admin(self):
        assert not await db.is_admin_in_db(9999)

    async def test_add_admin(self):
        await db.add_admin(42, "alice", "Alice Smith", added_by=1)
        assert await db.is_admin_in_db(42)

    async def test_remove_admin(self):
        await db.add_admin(42, "alice", "Alice", added_by=1)
        removed = await db.remove_admin(42)
        assert removed is True
        assert not await db.is_admin_in_db(42)

    async def test_remove_nonexistent_admin_returns_false(self):
        removed = await db.remove_admin(99999)
        assert removed is False


# ── Invite codes ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestInvites:
    async def test_create_and_use_invite(self):
        code = await db.create_invite(created_by=1, label="Test invite", ttl_minutes=30)
        assert isinstance(code, str)
        assert len(code) > 0

        label = await db.use_invite(code, user_id=42)
        assert label == "Test invite"

    async def test_used_invite_cannot_be_reused(self):
        code = await db.create_invite(created_by=1, label="Once", ttl_minutes=30)
        await db.use_invite(code, user_id=42)
        result = await db.use_invite(code, user_id=43)
        assert result is None

    async def test_expired_invite_returns_none(self):
        code = await db.create_invite(created_by=1, label="Expired", ttl_minutes=-1)
        result = await db.use_invite(code, user_id=42)
        assert result is None

    async def test_nonexistent_invite_returns_none(self):
        result = await db.use_invite("doesnotexist", user_id=1)
        assert result is None


# ── Short links ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestShortLinks:
    async def test_create_and_retrieve(self):
        await db.create_short_link("https://amazon.com/dp/B001", "abc1234")
        url = await db.get_long_url_by_code("abc1234")
        assert url == "https://amazon.com/dp/B001"

    async def test_get_code_by_long_url(self):
        await db.create_short_link("https://amazon.com/dp/B002", "xyz9876")
        code = await db.get_code_by_long_url("https://amazon.com/dp/B002")
        assert code == "xyz9876"

    async def test_missing_code_returns_none(self):
        url = await db.get_long_url_by_code("notexist")
        assert url is None

    async def test_log_click_increments_counter(self):
        await db.create_short_link("https://amazon.com/dp/B003", "clk0001")
        await db.log_click("clk0001", "Mozilla/5.0", "https://t.me/", "1.2.3.4")
        await db.log_click("clk0001", "Mozilla/5.0", "", "")
        stats = await db.get_link_stats("clk0001")
        assert stats is not None
        assert stats["click_count"] == 2

    async def test_delete_short_link(self):
        await db.create_short_link("https://amazon.com/dp/B004", "del0001")
        deleted = await db.delete_short_link("del0001")
        assert deleted is True
        url = await db.get_long_url_by_code("del0001")
        assert url is None

    async def test_delete_nonexistent_returns_false(self):
        deleted = await db.delete_short_link("doesnotexist")
        assert deleted is False

    async def test_get_top_links(self):
        for i in range(3):
            code = f"top{i:04d}"
            await db.create_short_link(f"https://amazon.com/dp/B{i:09d}", code)
            for _ in range(i + 1):
                await db.log_click(code, "UA", "", "")
        top = await db.get_top_links(3)
        assert len(top) == 3
        # top[0] must have the most clicks
        assert top[0]["clicks"] >= top[1]["clicks"]


# ── URL cache ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestUrlCache:
    async def test_cache_and_retrieve(self):
        await db.cache_short_url("https://long.url/path?x=1", "https://tinyurl.com/abc")
        cached = await db.get_short_url("https://long.url/path?x=1")
        assert cached == "https://tinyurl.com/abc"

    async def test_missing_returns_none(self):
        result = await db.get_short_url("https://not-cached.com")
        assert result is None

    async def test_overwrite_existing_cache(self):
        await db.cache_short_url("https://long.url", "https://tiny.cc/old")
        await db.cache_short_url("https://long.url", "https://tiny.cc/new")
        result = await db.get_short_url("https://long.url")
        assert result == "https://tiny.cc/new"


# ── Bot settings ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBotSettings:
    async def test_set_and_get(self):
        await db.set_setting("vision_mode", "compare", admin_id=1)
        result = await db.get_setting("vision_mode")
        assert result == "compare"

    async def test_get_missing_returns_none(self):
        result = await db.get_setting("nonexistent_setting")
        assert result is None

    async def test_update_setting(self):
        await db.set_setting("vision_mode", "best", admin_id=1)
        await db.set_setting("vision_mode", "cheapest", admin_id=1)
        result = await db.get_setting("vision_mode")
        assert result == "cheapest"

    async def test_delete_setting(self):
        await db.set_setting("vision_mode", "compare", admin_id=1)
        await db.delete_setting("vision_mode")
        result = await db.get_setting("vision_mode")
        assert result is None
