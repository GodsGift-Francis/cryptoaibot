"""Injectable clock so tests are deterministic."""
from datetime import datetime, timedelta, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_ms(self) -> int:
        return int(self.now().timestamp() * 1000)


class FakeClock(SystemClock):
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    def set(self, when: datetime) -> None:
        self._now = when
