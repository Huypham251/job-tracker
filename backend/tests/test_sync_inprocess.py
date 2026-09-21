import threading
import time

from app.sync import inprocess


def test_start_worker_thread_starts_a_background_daemon_thread(monkeypatch) -> None:
    started = threading.Event()

    def fake_run_forever() -> None:
        started.set()
        time.sleep(10)  # would block forever if this were the real loop

    monkeypatch.setattr(inprocess, "run_forever", fake_run_forever)
    monkeypatch.setattr(inprocess, "_worker_thread", None)

    inprocess.start_worker_thread()

    assert started.wait(timeout=2), "worker thread never called run_forever"
    assert inprocess._worker_thread is not None
    assert inprocess._worker_thread.daemon is True
    assert inprocess._worker_thread.is_alive()


def test_start_worker_thread_is_idempotent(monkeypatch) -> None:
    calls = []

    def fake_run_forever() -> None:
        calls.append(1)
        time.sleep(10)

    monkeypatch.setattr(inprocess, "run_forever", fake_run_forever)
    monkeypatch.setattr(inprocess, "_worker_thread", None)

    inprocess.start_worker_thread()
    first_thread = inprocess._worker_thread
    inprocess.start_worker_thread()  # second call must not start a second thread

    assert inprocess._worker_thread is first_thread
    assert len(calls) == 1
