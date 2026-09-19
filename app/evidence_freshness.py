from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Iterable

DEFAULT_MAX_AGE_DAYS = 365
_FRESHNESS_STATES = ("current", "stale", "not_yet_valid", "unassessed")


def parse_source_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def assess_source_freshness(
    published_at: str | None,
    *,
    as_of: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> str:
    """Classify only explicit publication/effective timestamps.

    Fetch/access time is intentionally not used as a proxy for publication time:
    recently downloading an old document must not make its contents look current.
    """
    published = parse_source_datetime(published_at)
    if published is None:
        return "unassessed"
    reference = as_of or datetime.now(UTC)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    reference = reference.astimezone(UTC)
    if published > reference:
        return "not_yet_valid"
    age_days = (reference - published).days
    return "stale" if age_days > max_age_days else "current"


def summarize_source_freshness(sources: Iterable[object]) -> dict[str, int]:
    counts = {state: 0 for state in _FRESHNESS_STATES}
    for source in sources:
        state = str(getattr(source, "freshness", "unassessed"))
        if state not in counts:
            state = "unassessed"
        counts[state] += 1
    return counts
