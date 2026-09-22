"""In-memory RFQ collection. Restart clears results; re-ingestion adds
another entry — see architecture/ARCHITECTURE.md ("Stored RFQ") and
architecture/DECISIONS.md ("In-memory store"). Only a successful RfqResult
is ever stored; a NonRfqResult or a failed ingestion never reaches here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models import RfqResult


@dataclass
class StoredRfq:
    """The validated public result plus subject and ingestion time for
    display. This metadata never enters the public /ingest response."""

    result: RfqResult
    subject: str | None
    ingested_at: datetime


class RfqStore:
    """One process-wide, in-memory list. `list.append`/iteration are
    atomic under the GIL, so this needs no extra locking for the single
    worker-thread-per-request model described in ARCHITECTURE.md."""

    def __init__(self) -> None:
        self._items: list[StoredRfq] = []

    def add(self, result: RfqResult, subject: str | None) -> StoredRfq:
        stored = StoredRfq(result=result, subject=subject, ingested_at=datetime.now(timezone.utc))
        self._items.append(stored)
        return stored

    def list(self) -> list[StoredRfq]:
        return list(self._items)
