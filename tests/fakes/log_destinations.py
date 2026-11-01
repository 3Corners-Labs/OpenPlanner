"""Fakes for the log destination senders (ADR 0085): a scriptable sender and target source."""

from collections.abc import Callable, Sequence

from app.adapters.db.log_destinations import LogTarget
from app.domain.log_destinations import DeliveryError, Reason


class FakeSender:
    """``LogDestinationSender`` fake: records batches and fails on demand."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.failures: list[Reason] = []
        self.closed = False

    async def send(self, lines: Sequence[str]) -> None:
        if self.failures:
            raise DeliveryError(self.failures.pop(0))
        self.batches.append(list(lines))

    async def aclose(self) -> None:
        self.closed = True

    @property
    def lines(self) -> list[str]:
        return [line for batch in self.batches for line in batch]


class FakeFactory:
    """``SenderFactory`` fake: one ``FakeSender`` per built pipeline, kept for the test."""

    def __init__(self) -> None:
        self.built: list[tuple[LogTarget, FakeSender]] = []

    def __call__(self, target: LogTarget, timeout: Callable[[], float]) -> FakeSender:
        sender = FakeSender()
        self.built.append((target, sender))
        return sender

    @property
    def last(self) -> FakeSender:
        return self.built[-1][1]


class FakeTargets:
    """``TargetSource`` fake: whatever the test puts in ``targets``."""

    def __init__(self) -> None:
        self.targets: list[LogTarget] = []
        self.fail = False

    async def enabled(self) -> list[LogTarget]:
        if self.fail:
            raise RuntimeError("database unreachable")
        return list(self.targets)
