import asyncio
import time
from config import logger

class TaskManager:
    """
    Manages background tasks with strong references to prevent premature garbage collection,
    logs any unhandled exceptions, and coordinates debounced background cloud backups.
    """
    def __init__(self):
        self._tasks: set[asyncio.Task] = set()
        self._debounce_task: asyncio.Task | None = None
        self._last_request_time: float = 0.0

    @property
    def active_task_count(self) -> int:
        return len(self._tasks)

    def create_tracked_task(self, coro, name: str | None = None) -> asyncio.Task:
        """
        Creates an asyncio.Task from the coroutine, holds a strong reference in self._tasks,
        and logs any exceptions upon completion.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def _on_done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc:
                    logger.error(
                        f"Tracked background task '{name or t.get_name()}' failed with exception: {exc}",
                        exc_info=exc
                    )

        task.add_done_callback(_on_done)
        return task

    def schedule_debounced_backup(self, bot, chat_id: str | None = None, delay: float = 10.0) -> asyncio.Task:
        """
        Schedules a debounced background cloud backup.
        At most one backup is triggered per `delay` seconds.
        Subsequent adds/edits extend the quiet window so that multiple rapid mutations
        coalesce into a single backup.
        """
        self._last_request_time = time.monotonic()

        async def _debounced_runner():
            while True:
                elapsed = time.monotonic() - self._last_request_time
                remaining = delay - elapsed
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)

            from services.backup_service import backup_to_telegram
            try:
                await backup_to_telegram(bot, chat_id=chat_id)
            except Exception as bkp_err:
                logger.error(f"Debounced backup execution error: {bkp_err}", exc_info=True)

        if self._debounce_task is None or self._debounce_task.done():
            self._debounce_task = self.create_tracked_task(_debounced_runner(), name="debounced_cloud_backup")
        return self._debounce_task

# Singleton instance
task_manager = TaskManager()

def create_tracked_task(coro, name: str | None = None) -> asyncio.Task:
    return task_manager.create_tracked_task(coro, name=name)

def schedule_debounced_backup(bot, chat_id: str | None = None, delay: float = 10.0) -> asyncio.Task:
    return task_manager.schedule_debounced_backup(bot, chat_id=chat_id, delay=delay)
