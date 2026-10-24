"""Google Calendar access: free slots in the primary calendar and booking them.

Usage:
    python gcal.py [DAYS]    # print free slots for the next DAYS days (default 5)
"""

import sys
from datetime import date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import google_auth

WORK_START = time(9, 0)
WORK_END = time(18, 0)
WORKDAYS = range(0, 5)  # Monday to Friday

Slot = tuple[datetime, datetime]


@cache
def _service():
    return google_auth.service("calendar", "v3")


@cache
def _timezone() -> ZoneInfo:
    calendar = _service().calendars().get(calendarId="primary").execute()
    return ZoneInfo(calendar["timeZone"])


def _busy(start: datetime, end: datetime) -> list[Slot]:
    response = _service().freebusy().query(body={
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": "primary"}],
    }).execute()
    periods = response["calendars"]["primary"]["busy"]
    return sorted(
        (datetime.fromisoformat(p["start"]), datetime.fromisoformat(p["end"]))
        for p in periods
    )


def free_slots(days: int = 5, min_minutes: int = 30) -> list[Slot]:
    """Free slots of at least min_minutes within working hours, from now on."""
    tz = _timezone()
    now = datetime.now(tz).replace(second=0, microsecond=0)
    now += timedelta(minutes=-now.minute % 15)
    today = now.date()
    end = datetime.combine(today + timedelta(days=days), time.min, tz)
    busy = _busy(now, end)

    slots = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        if day.weekday() in WORKDAYS:
            slots += _gaps(day, tz, now, busy, timedelta(minutes=min_minutes))
    return slots


def _gaps(day: date, tz: ZoneInfo, now: datetime, busy: list[Slot], min_length: timedelta) -> list[Slot]:
    cursor = max(datetime.combine(day, WORK_START, tz), now)
    day_end = datetime.combine(day, WORK_END, tz)
    gaps = []
    for busy_start, busy_end in busy:
        if busy_end <= cursor or busy_start >= day_end:
            continue
        if busy_start - cursor >= min_length:
            gaps.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if day_end - cursor >= min_length:
        gaps.append((cursor, day_end))
    return gaps


def find_event_for_message(message_id: str) -> dict | None:
    """The event created for this Gmail message, past or future, including
    ones the user deleted (status "cancelled")."""
    events = _service().events().list(
        calendarId="primary", privateExtendedProperty=f"gmailMessageId={message_id}",
        showDeleted=True, maxResults=1,
    ).execute()["items"]
    return events[0] if events else None


def event_slot(event: dict) -> Slot:
    return (datetime.fromisoformat(event["start"]["dateTime"]),
            datetime.fromisoformat(event["end"]["dateTime"]))


def create_event(title: str, slot: Slot, description: str, message_id: str, source_url: str) -> str:
    """Create an event in the primary calendar, tagged with the Gmail message
    it is for, and return its link."""
    start, end = slot
    event = _service().events().insert(calendarId="primary", body={
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "source": {"title": "Open email in Gmail", "url": source_url},
        "extendedProperties": {"private": {"gmailMessageId": message_id}},
    }).execute()
    return event["htmlLink"]


def format_slot(slot: Slot) -> str:
    start, end = (t.astimezone(_timezone()) for t in slot)
    return f"{start:%a %d %b %H:%M}–{end:%H:%M}"


if __name__ == "__main__":
    for slot in free_slots(int(sys.argv[1]) if len(sys.argv) > 1 else 5):
        print(format_slot(slot))
