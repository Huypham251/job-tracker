import logging
import threading

from app.sync.worker import run_forever

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None


def start_worker_thread() -> None:
    """Starts sync/worker.py's run_forever() loop on a background daemon
    thread, unmodified — used only when settings.run_worker_in_process is
    true (production, where there's no separate `python -m app.sync.worker`
    process). Local dev keeps running the worker as that separate process,
    per CLAUDE.md's documented workflow; this function is never called
    unless the env var opts in, so local dev is unaffected. Idempotent: a
    second call while a thread is already running is a no-op, so the
    lifespan hook can call this without tracking whether it already ran."""
    global _worker_thread
    if _worker_thread is not None:
        return
    _worker_thread = threading.Thread(target=run_forever, name="sync-worker", daemon=True)
    _worker_thread.start()
    logger.info("Started in-process sync worker thread")
