"""Tests for error classification and retry delay computation."""

from genesis_router.retry import classify_error, compute_delay
from genesis_router.types import ErrorCategory, RetryPolicy


class TestClassifyError:
    def test_transient_codes(self):
        for code in (408, 429, 500, 502, 503, 504):
            assert classify_error(code, "") == ErrorCategory.TRANSIENT

    def test_permanent_codes(self):
        assert classify_error(401, "") == ErrorCategory.PERMANENT
        assert classify_error(404, "") == ErrorCategory.PERMANENT

    def test_quota_402(self):
        assert classify_error(402, "") == ErrorCategory.QUOTA_EXHAUSTED

    def test_quota_403_with_keywords(self):
        assert classify_error(403, "quota exceeded") == ErrorCategory.QUOTA_EXHAUSTED
        assert classify_error(403, "billing limit reached") == ErrorCategory.QUOTA_EXHAUSTED

    def test_403_without_keywords_is_permanent(self):
        assert classify_error(403, "forbidden") == ErrorCategory.PERMANENT

    def test_timeout_in_message(self):
        assert classify_error(None, "Connection timeout") == ErrorCategory.TRANSIENT

    def test_degraded_keywords(self):
        assert classify_error(None, "malformed response") == ErrorCategory.DEGRADED
        assert classify_error(None, "truncated output") == ErrorCategory.DEGRADED

    def test_unknown_defaults_to_transient(self):
        assert classify_error(None, "unknown error") == ErrorCategory.TRANSIENT


class TestComputeDelay:
    def test_first_attempt_is_base(self):
        policy = RetryPolicy(base_delay_ms=1000, jitter_pct=0.0)
        delay = compute_delay(policy, 0)
        assert delay == 1.0  # 1000ms / 1000

    def test_exponential_backoff(self):
        policy = RetryPolicy(
            base_delay_ms=1000,
            backoff_multiplier=2.0,
            max_delay_ms=100000,
            jitter_pct=0.0,
        )
        assert compute_delay(policy, 0) == 1.0
        assert compute_delay(policy, 1) == 2.0
        assert compute_delay(policy, 2) == 4.0

    def test_max_delay_cap(self):
        policy = RetryPolicy(
            base_delay_ms=1000,
            backoff_multiplier=10.0,
            max_delay_ms=5000,
            jitter_pct=0.0,
        )
        assert compute_delay(policy, 5) == 5.0  # capped

    def test_jitter_stays_in_range(self):
        policy = RetryPolicy(base_delay_ms=1000, jitter_pct=0.25)
        for _ in range(50):
            delay = compute_delay(policy, 0)
            assert 0.75 <= delay <= 1.25
