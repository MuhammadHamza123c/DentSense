"""Rate limiter unit tests."""

from __future__ import annotations

from app.services.rate_limit import InMemoryRateLimiter


def test_allows_requests_up_to_limit() -> None:
    limiter = InMemoryRateLimiter()

    for _ in range(3):
        allowed, retry_after = limiter.hit("login:1.2.3.4", limit=3)

        assert allowed is True
        assert retry_after == 0


def test_blocks_requests_over_limit_with_retry_after() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(2):
        limiter.hit("login:1.2.3.4", limit=2)

    allowed, retry_after = limiter.hit("login:1.2.3.4", limit=2)

    assert allowed is False
    assert 1 <= retry_after <= 60


def test_keys_are_isolated() -> None:
    limiter = InMemoryRateLimiter()
    limiter.hit("login:1.1.1.1", limit=1)
    limiter.hit("login:1.1.1.1", limit=1)

    allowed, _ = limiter.hit("login:2.2.2.2", limit=1)

    assert allowed is True


def test_reset_clears_state() -> None:
    limiter = InMemoryRateLimiter()
    limiter.hit("login:1.1.1.1", limit=1)
    limiter.hit("login:1.1.1.1", limit=1)

    limiter.reset()

    allowed, _ = limiter.hit("login:1.1.1.1", limit=1)
    assert allowed is True
