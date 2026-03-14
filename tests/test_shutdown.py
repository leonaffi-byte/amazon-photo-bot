"""
tests/test_shutdown.py — Tests for the graceful-shutdown task-draining logic.

Verifies the _active_tasks mechanism in main.py:
  - Tasks that complete within the 10s grace period are not cancelled.
  - Tasks that exceed the 10s grace period are cancelled.
  - An empty task set proceeds without error.
  - Done callbacks remove tasks from the set automatically.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Helpers replicating main.py shutdown logic ────────────────────────────────

async def _simulate_shutdown(
    task_coros: list,
    grace_period: float = 10.0,
) -> tuple[set, set]:
    """
    Simulate the graceful-shutdown drain loop from main.py.

    Starts each coroutine as a task, then runs the drain logic.
    Returns (done_tasks, cancelled_tasks).
    """
    tasks = set()

    def _remove_done(t):
        tasks.discard(t)

    for coro in task_coros:
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(_remove_done)

    # Give tasks a tick to start
    await asyncio.sleep(0)

    # Drain loop (mirrors main.py shutdown)
    cancelled: set[asyncio.Task] = set()
    completed: set[asyncio.Task] = set()

    if tasks:
        snapshot = list(tasks)
        done, pending = await asyncio.wait(snapshot, timeout=grace_period)
        completed = done
        if pending:
            for t in pending:
                t.cancel()
                cancelled.add(t)
            await asyncio.gather(*pending, return_exceptions=True)

    return completed, cancelled


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTaskDrainLogic:

    @pytest.mark.asyncio
    async def test_fast_task_not_cancelled(self):
        """A task that finishes before the grace period is never cancelled."""
        async def fast_work():
            await asyncio.sleep(0.01)
            return "done"

        done, cancelled = await _simulate_shutdown([fast_work()], grace_period=5.0)
        assert len(done) == 1
        assert len(cancelled) == 0

    @pytest.mark.asyncio
    async def test_slow_task_is_cancelled(self):
        """A task that exceeds the grace period is cancelled."""
        async def slow_work():
            await asyncio.sleep(60)   # far longer than grace period

        done, cancelled = await _simulate_shutdown([slow_work()], grace_period=0.05)
        assert len(cancelled) == 1
        assert len(done) == 0

    @pytest.mark.asyncio
    async def test_empty_task_set_no_error(self):
        """Shutdown with no in-flight tasks completes without error."""
        done, cancelled = await _simulate_shutdown([], grace_period=5.0)
        assert len(done) == 0
        assert len(cancelled) == 0

    @pytest.mark.asyncio
    async def test_mixed_tasks_partial_cancel(self):
        """Fast tasks complete; only the slow one is cancelled."""
        results = []

        async def fast_work():
            await asyncio.sleep(0.01)
            results.append("fast")

        async def slow_work():
            await asyncio.sleep(60)
            results.append("slow")   # never reached

        done, cancelled = await _simulate_shutdown(
            [fast_work(), slow_work()],
            grace_period=0.1,
        )
        assert len(cancelled) == 1
        assert "fast" in results
        assert "slow" not in results

    @pytest.mark.asyncio
    async def test_multiple_fast_tasks_all_complete(self):
        """Multiple fast tasks all complete within grace period."""
        results = []

        async def task(n):
            await asyncio.sleep(0.01)
            results.append(n)

        done, cancelled = await _simulate_shutdown(
            [task(i) for i in range(5)],
            grace_period=5.0,
        )
        assert len(done) == 5
        assert len(cancelled) == 0
        assert sorted(results) == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_done_callback_removes_task_from_set(self):
        """Tasks with done callbacks auto-remove themselves from the tracking set."""
        tracking: set[asyncio.Task] = set()

        def _remove(t):
            tracking.discard(t)

        async def work():
            await asyncio.sleep(0.01)

        t = asyncio.create_task(work())
        tracking.add(t)
        t.add_done_callback(_remove)

        assert t in tracking
        await t
        # After completion the callback fires synchronously (in the event loop)
        await asyncio.sleep(0)   # let event loop process callbacks
        assert t not in tracking


class TestTrackTaskHelper:
    """Tests for the track_task() helper imported from main.py."""

    @pytest.mark.asyncio
    async def test_track_task_adds_to_set(self):
        """track_task() registers the task in _active_tasks."""
        import main as m
        original = set(m._active_tasks)

        async def noop():
            await asyncio.sleep(0)

        t = m.track_task(asyncio.create_task(noop()))
        assert t in m._active_tasks
        await t
        # Cleanup: restore state
        m._active_tasks.difference_update(m._active_tasks - original)

    @pytest.mark.asyncio
    async def test_track_task_auto_removes_on_completion(self):
        """track_task() removes the task from _active_tasks when done."""
        import main as m
        original_size = len(m._active_tasks)

        event = asyncio.Event()

        async def controlled_work():
            await event.wait()

        t = m.track_task(asyncio.create_task(controlled_work()))
        assert t in m._active_tasks

        event.set()
        await asyncio.sleep(0)   # let done callback fire
        await t
        await asyncio.sleep(0)   # give callback another tick

        assert t not in m._active_tasks
