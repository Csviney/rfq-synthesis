"""In-memory RFQ collection. Restart clears results; re-ingestion adds
another entry. Only a successful RfqResult
is ever stored; a NonRfqResult or a failed ingestion never reaches here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models import RfqResult, TriageRecommendation


@dataclass
class StoredRfq:
    """The validated public result plus triage, subject, and ingestion
    time for display. None of this enters the public /ingest response."""

    result: RfqResult
    triage: TriageRecommendation
    subject: str | None
    ingested_at: datetime


class RfqStore:
    """One process-wide, in-memory list."""

    def __init__(self) -> None:
        self._items: list[StoredRfq] = []

    def add(self, result: RfqResult, triage: TriageRecommendation, subject: str | None) -> StoredRfq:
        stored = StoredRfq(
            result=result, triage=triage, subject=subject, ingested_at=datetime.now(timezone.utc)
        )
        self._items.append(stored)
        return stored

    def list(self) -> list[StoredRfq]:
        return list(self._items)
