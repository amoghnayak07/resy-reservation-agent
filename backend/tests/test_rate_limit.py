from app.guards.rate_limit import SlidingWindowLimiter


def test_allows_up_to_limit_then_blocks_the_next() -> None:
    limiter = SlidingWindowLimiter()
    for i in range(3):
        assert limiter.check("key", limit=3, window_seconds=60, now=float(i)) is None

    retry_after = limiter.check("key", limit=3, window_seconds=60, now=3.0)
    assert retry_after is not None
    assert retry_after > 0


def test_window_slides_and_allows_again_after_expiry() -> None:
    limiter = SlidingWindowLimiter()
    assert limiter.check("key", limit=1, window_seconds=60, now=0.0) is None
    # still inside the window -> blocked
    assert limiter.check("key", limit=1, window_seconds=60, now=30.0) is not None
    # window has fully elapsed -> allowed again
    assert limiter.check("key", limit=1, window_seconds=60, now=61.0) is None


def test_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter()
    assert limiter.check("a", limit=1, window_seconds=60, now=0.0) is None
    assert limiter.check("b", limit=1, window_seconds=60, now=0.0) is None
    assert limiter.check("a", limit=1, window_seconds=60, now=0.0) is not None
