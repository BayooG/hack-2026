"""Google Calendar access: free slots in the primary calendar and booking them.

Usage:
    python gcal.py [DAYS]    # print free slots for the next DAYS days (default 5)
"""

import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import run_context

# Events with these words in the title count as email time to group reply blocks with.
GROUP_KEYWORDS = ("reply", "replies", "email", "inbox")
# Group with existing email time only if that delays the block by at most this much.
MAX_GROUP_DELAY = timedelta(days=1)

Slot = tuple[datetime, datetime]


def _service():
    return run_context.current().service("calendar", "v3")


def _timezone() -> ZoneInfo:
    return run_context.current().cached(
        "calendar:timezone",
        lambda: ZoneInfo(_service().calendars().get(calendarId="primary").execute()["timeZone"]),
    )


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


def _window(days: int) -> Slot:
    """From now (rounded up to the quarter hour) to the end of the last day."""
    tz = _timezone()
    now = datetime.now(tz).replace(second=0, microsecond=0)
    now += timedelta(minutes=-now.minute % 15)
    return now, datetime.combine(now.date() + timedelta(days=days), time.min, tz)


def free_slots(days: int = 5, min_minutes: int = 30) -> list[Slot]:
    """Free slots of at least min_minutes within working hours, from now on."""
    ctx = run_context.current()
    hours = None if ctx.anytime else (ctx.work_start, ctx.work_end)
    tz = _timezone()
    now, end = _window(days)
    today = now.date()
    busy = _busy(now, end)

    slots = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        if day.weekday() in ctx.work_days:
            slots += _gaps(day, hours, tz, now, busy, timedelta(minutes=min_minutes))
    return slots


def _gaps(
    day: date, hours: tuple[time, time] | None, tz: ZoneInfo, now: datetime,
    busy: list[Slot], min_length: timedelta,
) -> list[Slot]:
    """Free gaps in one day; hours=None means the whole day."""
    if hours is None:
        day_start, day_end = (datetime.combine(d, time.min, tz) for d in (day, day + timedelta(days=1)))
    else:
        day_start, day_end = datetime.combine(day, hours[0], tz), datetime.combine(day, hours[1], tz)
    cursor = max(day_start, now)
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


def _email_blocks(start: datetime, end: datetime) -> list[Slot]:
    """Timed events in the range that are email time: the agent's own reply
    blocks or the user's events with a GROUP_KEYWORDS word in the title."""
    events = _service().events().list(
        calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime",
    ).execute()["items"]
    return [
        event_slot(e) for e in events
        if "dateTime" in e["start"] and (
            "gmailMessageId" in e.get("extendedProperties", {}).get("private", {})
            or any(word in e.get("summary", "").lower() for word in GROUP_KEYWORDS)
        )
    ]


def _pick_slot(slots: list[Slot], anchors: list[Slot], minutes: int) -> tuple[Slot, Slot | None] | None:
    """A slot of the given length, right before or after an anchor if one is
    free soon enough, else at the start of the first free slot. Returns the
    slot and the anchor it is next to (or None)."""
    if not slots:
        return None
    length = timedelta(minutes=minutes)
    candidates = []
    for start, end in slots:
        for anchor in anchors:
            if start == anchor[1]:
                candidates.append(((start, start + length), anchor))
            if end == anchor[0]:
                candidates.append(((end - length, end), anchor))
    first = slots[0][0]
    if candidates:
        slot, anchor = min(candidates)
        if slot[0] - first <= MAX_GROUP_DELAY:
            return slot, anchor
    return (first, first + length), None


def reply_slot(minutes: int, days: int = 5) -> tuple[Slot, Slot | None] | None:
    """Where to block reply time: next to existing email time when possible.
    Returns the slot and the email-time event it is next to (or None), or
    None if there is no free slot."""
    slots = free_slots(days, minutes)
    return _pick_slot(slots, _email_blocks(*_window(days)), minutes)


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
