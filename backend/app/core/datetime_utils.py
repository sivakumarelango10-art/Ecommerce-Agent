from datetime import datetime, timezone


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime instance to a UTC timezone-aware datetime.

    If dt is None, returns None.
    If dt is naive (tzinfo is None), assumes UTC and attaches timezone.utc.
    If dt is aware with another timezone, converts to UTC via astimezone(timezone.utc).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    """Return the current time as a UTC timezone-aware datetime."""
    return datetime.now(timezone.utc)
