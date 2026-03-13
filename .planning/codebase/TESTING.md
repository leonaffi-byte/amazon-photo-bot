# Testing Patterns

**Analysis Date:** 2026-03-13

## Test Framework

**Runner:**
- pytest 7.x
- pytest-asyncio for async test support
- Config: `pytest.ini` in project root

**Assertion Library:**
- pytest built-in assertions and `pytest.raises()`

**Run Commands:**
```bash
pytest                          # Run all tests (33 test files)
pytest tests/test_database.py   # Run single test file
pytest -k "test_search_amazon"  # Run tests matching pattern
pytest -v                       # Verbose output
pytest --tb=short               # Shorter traceback format
```

**Configuration (pytest.ini):**
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

The `asyncio_mode = auto` setting automatically marks async functions with `@pytest.mark.asyncio`.

## Test File Organization

**Location:**
- All tests in `tests/` directory at project root
- Structure mirrors main codebase: `tests/test_bot.py`, `tests/test_database.py`, `tests/test_amazon_search.py`, etc.
- 33 test files total covering:
  - Core logic: `test_bot.py`, `test_bot_core.py`, `test_database.py`
  - Providers: `test_openai_provider.py`, `test_anthropic_provider.py`, etc.
  - Search backends: `test_paapi_backend.py`, `test_rapidapi_backend.py`, etc.
  - Utilities: `test_circuit_breaker.py`, `test_correlation.py`, `test_translator.py`
  - Admin/API: `test_admin.py`, `test_api_server.py`

**Naming:**
- Test files: `test_<module_name>.py`
- Test classes: `Test<Feature>` (e.g., `TestGetBackend`, `TestAffiliateTags`)
- Test functions: `test_<specific_behavior>` (e.g., `test_auto_prefers_paapi_when_both_keys_set`)

## Test Structure

**Suite Organization:**

Tests are organized using pytest classes for grouping related tests:

```python
@pytest.mark.asyncio
class TestGetBackend:
    async def test_auto_prefers_paapi_when_both_keys_set(self, monkeypatch):
        monkeypatch.setattr(config, "SEARCH_BACKEND", "auto")
        # ... test logic
        assert result == expected

    async def test_auto_falls_back_to_rapidapi_when_no_paapi(self, monkeypatch):
        monkeypatch.setattr(config, "SEARCH_BACKEND", "auto")
        # ... test logic
        assert result == expected
```

**Setup and Teardown:**

Shared fixtures in `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Redirect DATA_DIR to a fresh tmp directory for every test.
    This gives each test a clean SQLite file and prevents cross-test pollution.
    """
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))

    # Patch the module-level DB_PATH that was already computed at import time
    import database
    monkeypatch.setattr(database, "DB_PATH", str(data / "bot_data.db"))
    monkeypatch.setattr(database, "_DATA_DIR", data)

    # Also reset the internal locks and persistent connection so tests don't share state
    import asyncio
    monkeypatch.setattr(database, "_lock", asyncio.Lock())
    monkeypatch.setattr(database, "_conn_lock", asyncio.Lock())
    monkeypatch.setattr(database, "_persistent_conn", None)

    yield data
```

Database tests use `@pytest_asyncio.fixture(autouse=True)` for async setup:

```python
@pytest_asyncio.fixture(autouse=True)
async def init(tmp_data_dir):
    """Initialise the DB schema before every test."""
    await db.init_db()
```

**Teardown:**
- Pytest fixtures handle cleanup automatically
- `tmp_data_dir` fixture cleans up temporary directory after test
- Database fixtures reset schema and connections between tests

## Mocking

**Framework:** `unittest.mock` (standard library)

**Patterns:**

**AsyncMock for async functions:**
```python
from unittest.mock import AsyncMock

async def test_no_keys_raises_runtime_error(self, monkeypatch):
    with patch("key_store.get", new_callable=AsyncMock, return_value=None):
        with pytest.raises(RuntimeError, match="No search API keys"):
            await amazon_search._build_backend()
```

**MagicMock for sync functions:**
```python
def _mock_backend(self, primary_results=None):
    backend = MagicMock()
    backend.name = "MockBackend"

    async def fake_search(query, max_results, page=1):
        return primary_results or []

    backend.search = fake_search
    return backend
```

**patch for replacing modules/functions:**
```python
with patch("key_store.get", side_effect=mock_get):
    with patch("search_backends.paapi_backend.PaapiBackend") as MockPaapi:
        backend = await amazon_search._build_backend()
        MockPaapi.assert_called_once()
```

**patch.object for replacing class methods:**
```python
with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
    results = await amazon_search.search_amazon(make_product(), max_results=10)
```

**What to Mock:**
- External API calls (OpenAI, Amazon PA-API, RapidAPI)
- File system operations (when not using tmp_data_dir)
- Time-dependent operations (use `freezegun` or mock `time.time()`)
- Network calls via aiohttp

**What NOT to Mock:**
- Core business logic (search, filtering, ranking)
- Database operations (use real database with clean tmp_data_dir per test)
- Configuration module (patch with `monkeypatch.setattr()`)
- Helper functions in the same module (call directly)

## Fixtures and Factories

**Test Data Factories:**

Helper functions create test fixtures inline or as methods:

```python
def make_product(**kwargs) -> ProductInfo:
    """Factory for ProductInfo test data."""
    defaults = dict(
        product_name="Wireless Keyboard",
        brand="TestBrand",
        category="Electronics",
        key_features=["Wireless", "Backlit"],
        amazon_search_query="TestBrand wireless keyboard",
        alternative_query="wireless keyboard backlit",
        confidence="high",
        notes="",
    )
    defaults.update(kwargs)
    return ProductInfo(**defaults)


def make_item(asin: str, rating: float = 4.0, review_count: int = 100,
              fba: bool = False) -> AmazonItem:
    """Factory for AmazonItem test data."""
    return AmazonItem(
        asin=asin,
        title=f"Product {asin}",
        image_url=None,
        price_usd=29.99,
        currency="USD",
        rating=rating,
        review_count=review_count,
        is_amazon_fulfilled=fba,
        is_sold_by_amazon=False,
        is_prime=fba,
        availability="In Stock",
    )
```

**Location:**
- Factories defined in the test file that uses them (often as module-level functions or class methods)
- Shared fixtures in `tests/conftest.py`
- Database helpers: `db.add_tag()`, `db.init_db()` called directly in tests

## Coverage

**Requirements:**
- No coverage enforced in CI; coverage is informational
- No `pytest-cov` configuration

**View Coverage:**
```bash
pytest --cov=. --cov-report=html    # Generate HTML coverage report
# Open htmlcov/index.html in browser
```

## Test Types

**Unit Tests:**
- Scope: Single module or class in isolation
- Approach: Mock external dependencies (APIs, database, file I/O)
- Example: `test_circuit_breaker.py` tests `CircuitBreaker` class with mocked async functions
- Strategy: Fast execution, deterministic results

**Example from `test_circuit_breaker.py`:**
```python
async def test_circuit_opens_after_threshold():
    cb = CircuitBreaker("test-open", failure_threshold=3, recovery_timeout=60.0)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())
    assert cb.state == CircuitState.OPEN
    assert cb._failure_count == 3
```

**Integration Tests:**
- Scope: Multiple modules interacting (e.g., database with business logic)
- Approach: Real database in tmp_data_dir; mock external APIs
- Example: `test_database.py` tests database CRUD with real SQLite
- Strategy: Verify data flow between layers

**Example from `test_database.py`:**
```python
@pytest.mark.asyncio
class TestAffiliateTags:
    async def test_add_tag(self):
        tag = await db.add_tag("mytag-20", "Primary", admin_id=1, admin_name="Alice")
        assert tag.tag == "mytag-20"
        assert tag.description == "Primary"
        assert not tag.is_active
```

**E2E Tests:**
- Framework: Not extensively used in this codebase
- Approach: Would test full bot flow from message to response
- Current: Limited E2E coverage; most flows tested via unit + integration

## Common Patterns

**Async Testing:**

Tests automatically run async functions via `asyncio_mode = auto` in pytest.ini:

```python
@pytest.mark.asyncio
class TestSearchAmazon:
    async def test_returns_primary_results(self):
        items = [make_item(f"B{i:010d}") for i in range(5)]
        backend = self._mock_backend(primary_results=items)

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            results = await amazon_search.search_amazon(make_product(), max_results=10)

        assert len(results) == 5
```

**Error Testing:**

Using `pytest.raises()` as a context manager:

```python
async def test_no_keys_raises_runtime_error(self, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_BACKEND", "auto")

    with patch("key_store.get", new_callable=AsyncMock, return_value=None):
        with pytest.raises(RuntimeError, match="No search API keys"):
            await amazon_search._build_backend()
```

With message matching:

```python
async def test_add_duplicate_raises(self):
    await db.add_tag("dup-20", "First", admin_id=1, admin_name="Alice")
    with pytest.raises(ValueError, match="already exists"):
        await db.add_tag("dup-20", "Second", admin_id=1, admin_name="Alice")
```

**Configuration/Monkeypatching:**

Using `monkeypatch` fixture for test isolation:

```python
async def test_auto_prefers_paapi_when_both_keys_set(self, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_BACKEND", "auto")
    # ... test logic
    # config.SEARCH_BACKEND is restored after test
```

**Parametrized Tests:**

Not heavily used; data is passed via factory methods instead. Example if needed:

```python
@pytest.mark.parametrize("confidence,weight", [
    ("high", 1.0),
    ("medium", 0.6),
    ("low", 0.2),
])
def test_confidence_weights(confidence, weight):
    assert CONFIDENCE_WEIGHTS[confidence] == weight
```

**State Verification:**

Tests verify internal state after operations:

```python
async def test_half_open_after_recovery_timeout():
    cb = CircuitBreaker("test-half-open", failure_threshold=2, recovery_timeout=0.1)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())
    assert cb._state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
```

## Test Isolation

**Database Isolation:**

The `tmp_data_dir` autouse fixture in `conftest.py` provides complete test isolation:

```python
@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Redirect DATA_DIR to a fresh tmp directory for every test.
    This gives each test a clean SQLite file and prevents cross-test pollution.
    """
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))

    # Patch module-level attributes
    import database
    monkeypatch.setattr(database, "DB_PATH", str(data / "bot_data.db"))
    monkeypatch.setattr(database, "_DATA_DIR", data)

    # Reset locks and connections
    import asyncio
    monkeypatch.setattr(database, "_lock", asyncio.Lock())
    monkeypatch.setattr(database, "_conn_lock", asyncio.Lock())
    monkeypatch.setattr(database, "_persistent_conn", None)

    yield data
    # Cleanup happens automatically after test
```

**Fixture Reset:**

Backend-specific fixtures reset state between tests:

```python
@pytest.fixture(autouse=True)
def reset_backend_fixture():
    """Each test gets a fresh backend."""
    amazon_search.reset_backend()
    yield
    amazon_search.reset_backend()
```

## Key Testing Modules

**Core Modules Tested:**

| Module | Test File | Coverage |
|--------|-----------|----------|
| `database.py` | `test_database.py` | CRUD, schema, migrations |
| `amazon_search.py` | `test_amazon_search.py` | Backend selection, search, filtering |
| `bot_core.py` | `test_bot_core.py` | Session state, callbacks, analysis |
| `providers/` | `test_*_provider.py` (7 files) | Each provider's API integration |
| `search_backends/` | `test_*_backend.py` (4 files) | Each backend's search logic |
| `circuit_breaker.py` | `test_circuit_breaker.py` | Circuit breaker state machine |
| `admin.py` | `test_admin.py` | Admin command handling |
| `api_server.py` | `test_api_server.py` | FastAPI endpoints |

---

*Testing analysis: 2026-03-13*
