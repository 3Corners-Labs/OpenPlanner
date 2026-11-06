"""The data volume as housekeeping measures it (ADR 0040), without reading the real disk."""

from app.domain.storage import MIB, StorageUsage


class Disk:
    """Stands in for the measure of the data volume that ``Housekeeping`` calls.

    The volume holds 100 MiB with a 3 MiB database; the test sets how much of it is free.
    """

    free_percent: float = 50

    def __call__(self) -> StorageUsage:
        return StorageUsage(int(self.free_percent * MIB), 100 * MIB, 3 * MIB)
