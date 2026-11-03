"""Rate-limit local por IP (token bucket em memória).

O sidecar é local, mas o token pode vazar por um navegador comprometido.
O limite evita que uma única origem consuma a API e o LLM sem controle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    rate_per_second: float
    capacity: float
    tokens: float
    updated_at: float

    @classmethod
    def create(cls, per_minute: int, burst: int) -> "TokenBucket":
        rate = max(0.001, per_minute / 60.0)
        cap = max(1.0, float(burst))
        return cls(rate_per_second=rate, capacity=cap, tokens=cap, updated_at=time.monotonic())

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_second)
        self.updated_at = now

    def take(self) -> tuple[bool, float]:
        """Consome 1 token. Retorna (permitido, segundos_para_retry)."""
        now = time.monotonic()
        self._refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0
        missing = 1.0 - self.tokens
        retry = missing / self.rate_per_second if self.rate_per_second else 60.0
        return False, max(1.0, retry)


@dataclass
class RateLimiter:
    per_minute: int
    burst: int
    _buckets: dict[str, TokenBucket] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0

    def allow(self, key: str) -> tuple[bool, float]:
        if not self.enabled:
            return True, 0.0
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket.create(self.per_minute, self.burst)
            self._buckets[key] = bucket
        return bucket.take()

    def reset(self) -> None:
        self._buckets.clear()
