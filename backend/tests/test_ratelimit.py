from app.core.ratelimit import FixedWindowLimiter


def test_limiter_allows_up_to_the_limit_then_reports_the_wait() -> None:
    clock = [1000.0]
    limiter = FixedWindowLimiter(clock=lambda: clock[0])

    assert all(limiter.hit("sync", "u1", 3, 60) is None for _ in range(3))
    wait = limiter.hit("sync", "u1", 3, 60)
    assert wait is not None and 0 < wait <= 60


def test_limiter_keys_and_buckets_are_independent() -> None:
    limiter = FixedWindowLimiter(clock=lambda: 1000.0)
    for _ in range(3):
        limiter.hit("sync", "u1", 3, 60)

    assert limiter.hit("sync", "u2", 3, 60) is None
    assert limiter.hit("messages", "u1", 3, 60) is None


def test_limiter_starts_a_fresh_window() -> None:
    clock = [1000.0]
    limiter = FixedWindowLimiter(clock=lambda: clock[0])
    for _ in range(4):
        limiter.hit("sync", "u1", 3, 60)

    clock[0] += 60
    assert limiter.hit("sync", "u1", 3, 60) is None


def test_limiter_forgets_old_windows() -> None:
    clock = [1000.0]
    limiter = FixedWindowLimiter(clock=lambda: clock[0])
    for key in range(50):
        limiter.hit("sync", f"u{key}", 3, 60)

    clock[0] += 600
    limiter.hit("sync", "u-new", 3, 60)
    assert len(limiter) == 1


def test_reset_clears_every_counter() -> None:
    limiter = FixedWindowLimiter(clock=lambda: 1000.0)
    for _ in range(4):
        limiter.hit("sync", "u1", 3, 60)
    limiter.reset()
    assert limiter.hit("sync", "u1", 3, 60) is None
