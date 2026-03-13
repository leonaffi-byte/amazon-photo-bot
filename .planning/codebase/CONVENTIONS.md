# Coding Conventions

**Analysis Date:** 2026-03-13

## Naming Patterns

**Files:**
- Lowercase with underscores: `bot_core.py`, `amazon_search.py`, `image_analyzer.py`
- Organized by functionality: `providers/base.py`, `search_backends/paapi_backend.py`, `adapters/telegram.py`
- Test files: `tests/test_<module>.py` (e.g., `test_amazon_search.py`, `test_database.py`)
- Private/internal utilities: Prefix with underscore: `_compress_image()`, `_fix_json()`, `_search_with_retry()`

**Functions:**
- camelCase-like but Python style (snake_case): `reset_backend()`, `search_amazon()`, `apply_db_settings()`
- Boolean functions/properties: descriptive, often with "is_" prefix: `is_admin`, `qualifies_for_israel_free_delivery`
- Private helper functions: Leading underscore: `_uid()`, `_clean_query()`, `_make_paapi()`

**Variables:**
- snake_case for all variables and constants: `product_name`, `amazon_search_query`, `PROVIDER_TIMEOUT_SECONDS`
- Module-level constants: UPPERCASE: `SYSTEM_PROMPT`, `MAX_PHOTO_BYTES`, `_SESSION_TTL`
- Abbreviations at module scope prefixed with underscore to avoid pollution: `_PILImage`, `_io`
- Temporary/internal variables: Often single-letter in narrow scopes: `exc`, `idx`, `ph`

**Types and Classes:**
- PascalCase for dataclasses and class names: `ProductInfo`, `ProviderResult`, `AmazonItem`, `UserSession`, `CircuitBreaker`
- Dataclass fields: Descriptive, snake_case: `product_name`, `is_amazon_fulfilled`, `qualifies_for_israel_free_delivery`
- Field defaults in dataclasses use `field(default_factory=...)` for mutable types: `field(default_factory=list)`
- Internal computed fields marked with `field(init=False)`: `quality_score`, `score`

## Code Style

**Formatting:**
- No formatter/linter configured (black, ruff, pylint not in use)
- Manual code style follows PEP 8 loosely
- Line length: Practical max ~100-120 characters, no hard limit enforced
- Imports grouped and ordered: `from __future__`, stdlib, third-party, local

**Linting:**
- No linter configured in `pyproject.toml`, `.pylintrc`, or `ruff.toml`
- Code is manually reviewed for quality

## Import Organization

**Order:**
1. `from __future__ import annotations` (always first if present)
2. Standard library imports (`asyncio`, `logging`, `time`, etc.)
3. Third-party imports (`PIL`, `aiohttp`, `telegram`, etc.)
4. Local project imports (`import config`, `from database import ...`)

**Path Aliases:**
- No import aliases used; imports use direct module names
- Local modules imported by name: `import config`, `import database as db`
- Relative imports NOT used; all imports are absolute from project root

**Examples from codebase:**
```python
# From bot_core.py
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional

from PIL import Image as _PILImage
import io as _io

import config
import database as db
import url_shortener
from adapters.base import Button, CarouselItem, MessageRef, PlatformAdapter
from correlation import get_correlation_id, new_correlation_id
from formatter import Formatter
from i18n import available_languages, t
from image_analyzer import ProductInfo
```

## Type Hints

**Style:**
- Python 3.11+ union syntax used: `str | None` instead of `Optional[str]`
- All function signatures include return type hints: `async def run() -> None:`, `def reset_backend() -> None:`
- Dataclass fields explicitly typed: `product_name: str`, `confidence: str`
- Complex types use `list[str]`, `dict[str, Any]`, `Optional[tuple[float, float, float, float]]`
- Forward references with `from __future__ import annotations` at file top

**Examples:**
```python
# From providers/base.py
@dataclass
class ProviderResult:
    """Result from a single vision provider."""
    provider_name: str          # e.g. "openai/gpt-4o"
    model_id: str               # full model id
    product_name: str
    brand: Optional[str]
    category: str
    key_features: list[str]
    amazon_search_query: str
    alternative_query: str
    confidence: str             # high | medium | low
    notes: str
    latency_ms: int             # wall-clock time for this call
    input_tokens: int
    output_tokens: int
    cost_usd: float             # estimated cost
    bbox: Optional[tuple[float, float, float, float]] = None  # (x%, y%, w%, h%)

    @property
    def cost_str(self) -> str:
        if self.cost_usd < 0.001:
            return f"${self.cost_usd * 1000:.3f}m"
        return f"${self.cost_usd:.4f}"

    def to_product_info(self):
        """Convert to the ProductInfo used by amazon_search."""
```

## Error Handling

**Patterns:**
- Try-except blocks follow specific fallback chains
- Broad `except Exception` used when all exceptions should be caught; specific exception types when appropriate
- Errors logged with `logger.error()`, `logger.warning()`, or `logger.critical()` with context
- RuntimeError raised for missing configuration: `raise RuntimeError("No search API keys")`
- ValueError raised for invalid input: `raise ValueError("already exists")`

**Examples from codebase:**

**Fallback chains** (from `amazon_search.py`):
```python
async def _search_with_retry(backend: SearchBackend, query: str, max_results: int, page: int = 1) -> list[AmazonItem]:
    try:
        return await backend.search(query, max_results, page=page)
    except Exception as exc:
        logger.warning("Backend %s quota exceeded, trying fallback...", backend.name)
        # Try alternative backend
        fallback = await _get_fallback_backend(backend)
        if fallback:
            return await fallback.search(query, max_results, page=page)
        raise
```

**Error logging with cleanup** (from `bot_core.py`):
```python
try:
    await adapter.stop()
except Exception as exc:
    logger.error("send_photo failed: %s, falling back to text", exc)
    # fallback logic
    try:
        # fallback attempt
    except Exception as exc2:
        logger.error("Text fallback also failed: %s", exc2)
```

**Exceptions in tests:**
```python
with pytest.raises(RuntimeError, match="service unavailable"):
    await cb.call(_fail())
```

## Logging

**Framework:** Standard library `logging` module

**Setup:**
- Module-level logger in every file: `logger = logging.getLogger(__name__)`
- Configured in `main.py` with StreamHandler (stdout) and FileHandler (bot.log)
- Log level set to INFO by default
- External libraries (httpx, httpcore, aiohttp) set to WARNING

**Patterns:**
- `logger.info()` — Normal operational events: "Database ready", "Bot is running", "Starting Israel check"
- `logger.warning()` — Recoverable issues: "edit_photo failed, falling back", "Backend quota exceeded"
- `logger.error()` — Errors that may impact user: "Vision analysis failed", "Amazon search failed"
- `logger.critical()` — Fatal startup errors: "database init failed"
- `logger.debug()` — Detailed operational info: cleanup counts, cache hits

**Log message style:**
- Include context variables: `logger.info("Photo received from %s [cid=%s]", user_key, cid)`
- Exceptions logged with `exc_info=True` for stack traces: `logger.error(..., exc_info=True)`
- No inline exception string concatenation; use format args

## Comments

**When to Comment:**
- Complex algorithms or non-obvious logic: e.g., quality scoring in `ProviderResult.__post_init__`
- Configuration priorities explained in docstrings: "Settings priority order: 1. Database, 2. Environment, 3. Defaults"
- Important warnings or gotchas: "Store raw image bytes so 'Try differently' can re-analyse without re-upload"

**Docstring/TSDoc:**
- Module docstrings present in most core files explaining purpose
- Dataclass docstrings: Single line before the class
- Function docstrings: Present for public APIs, brief (1-3 sentences)
- Inline comments for fields in dataclasses: `# e.g. "openai/gpt-4o"`, `# (x%, y%, w%, h%)`

**Examples:**
```python
# From config.py
"""
Central configuration — reads from .env file.

Settings priority order:
  1. Database (set via /admin -> Settings) — live, no restart needed
  2. Environment variable / .env file         — fallback / bootstrap

API keys follow the same priority via key_store.py.
"""

# From providers/base.py
def build_user_prompt(context_hint: Optional[str] = None) -> str:
    """Return the user prompt, optionally prepending a user-provided hint."""
    if context_hint and context_hint.strip():
        return (
            f"User context about this product: \"{context_hint.strip()}\"\n\n"
            + USER_PROMPT
        )
    return USER_PROMPT

# From bot_core.py
@dataclass
class UserSession:
    all_provider_results: list[ProviderResult] = field(default_factory=list)
    chosen_result: Optional[ProviderResult]    = None
    product_info: Optional[ProductInfo]        = None
    chosen_provider_idx: int = 0               # index into all_provider_results
    all_detected_products: list = field(default_factory=list)  # list[ProductInfo] when multi-product

    # page = current ITEM index (0-based) in filtered_items
    page: int = 0

    # Lazy loading: track which Amazon results page we last fetched
    amazon_page: int = 1      # next Amazon page to fetch (1 = first batch already done)
    more_available: bool = True
```

## Function Design

**Size:**
- Typical functions: 10-50 lines (methods up to ~100 lines for complex state)
- Large functions (200+ lines) exist when necessary for coherent logic: `handle_callback()` in bot_core.py
- Functions split by responsibility: search, format, persist, notify are separate

**Parameters:**
- Positional for required args: `async def search_amazon(product: ProductInfo, max_results: int)`
- Keyword-only for optional behavior: `israel_free_delivery_only: bool = False`
- Callbacks often take multiple parameters: `on_photo(adapter, event)`, `on_text(adapter, uid, cid, text, event)`

**Return Values:**
- Functions return concrete types or None: `-> list[AmazonItem]`, `-> str | None`, `-> None`
- Dataclass instances used for structured returns: `ProductInfo`, `ProviderResult`, `AmazonItem`
- Empty collections returned instead of None: `return []` rather than `return None`

## Module Design

**Exports:**
- Modules export public APIs at module level; internal helpers prefixed with `_`
- No `__all__` lists used; convention of underscore-prefix for private items
- Classes and functions that should be imported listed at file top in imports by other modules

**Barrel Files:**
- `adapters/__init__.py`, `providers/__init__.py`, `search_backends/__init__.py` are minimal or empty
- No re-exports; each module imports directly what it needs

**Examples:**
- `amazon_search.py` exports: `search_amazon()`, `backend_name()`, `reset_backend()`, `AmazonItem`
- `providers/manager.py` exports: `analyse_image()`, `get_providers()`
- `database.py` exports: All CRUD functions like `add_tag()`, `get_all_tags()`, etc.

## Async Patterns

**Async-first design:**
- All I/O uses async APIs: `aiohttp`, `aiosqlite`, Playwright async, `python-telegram-bot` v20 async
- No `asyncio.run()` in libraries; only in entry point `main.py`
- Concurrency via `asyncio.gather()`, `asyncio.create_task()`, `asyncio.wait()`

**Example from bot_core.py:**
```python
# Background tasks created as fire-and-forget
cleanup_tasks = []
for core in bot_cores:
    cleanup_tasks.append(asyncio.create_task(core.periodic_cleanup()))
```

## Configuration

**Approach:**
- Central `config.py` module reads from `.env` via `python-dotenv`
- Configuration priority: Database (via admin panel) > `.env` file > hard-coded defaults
- Settings changed via `/admin` panel write to database and update module attributes directly
- No configuration classes; simple module-level globals

**Setting a value:**
```python
# In config.py
VISION_MODE: str = os.getenv("VISION_MODE", "best")

# Can be overridden at runtime by settings_store.py writing to module attributes
# setattr(config, "VISION_MODE", new_value)
```

---

*Convention analysis: 2026-03-13*
