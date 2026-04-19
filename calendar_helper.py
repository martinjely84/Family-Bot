"""
calendar_helper.py — iCloud Calendar integration via CalDAV.

Apple iCloud supports the CalDAV standard, so we can read and write events
to your (and your wife's) shared Apple Calendar from Python.

Requirements:
  - An iCloud account
  - An App-Specific Password (NOT your main Apple ID password)
    Generate one at: https://appleid.apple.com → Sign-In and Security → App-Specific Passwords
  - The name of the shared calendar you want the bot to use (e.g. "Family")
"""

import os
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import caldav
from caldav.elements import dav, cdav
import dateparser
import icalendar

logger = logging.getLogger(__name__)

# ── Config (read from environment) ───────────────────────────────────────────
ICLOUD_USERNAME      = os.environ.get("ICLOUD_USERNAME", "")        # your Apple ID email
ICLOUD_APP_PASSWORD  = os.environ.get("ICLOUD_APP_PASSWORD", "")    # app-specific password
CALENDAR_NAME        = os.environ.get("ICLOUD_CALENDAR_NAME", "Family")  # shared cal name
TIMEZONE             = os.environ.get("TIMEZONE", "America/Chicago").strip().lstrip("=")

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"


# ── Internal helpers ──────────────────────────────────────────────────────────
def _get_client():
    return caldav.DAVClient(
        url=ICLOUD_CALDAV_URL,
        username=ICLOUD_USERNAME,
        password=ICLOUD_APP_PASSWORD,
    )


def _get_calendars(client: caldav.DAVClient):
    """Return all calendars matching CALENDAR_NAME (there may be duplicates)."""
    principal = client.principal()
    calendars = principal.calendars()
    matches = []
    for cal in calendars:
        try:
            cal_name = cal.get_properties([dav.DisplayName()])["{DAV:}displayname"]
            if cal_name.lower() == CALENDAR_NAME.lower():
                matches.append(cal)
        except Exception:
            continue
    if not matches:
        # Fallback: all calendars
        for cal in calendars:
            try:
                cal.get_properties([dav.DisplayName()])
                matches.append(cal)
            except Exception:
                continue
    return matches


def _tz():
    try:
        return ZoneInfo(TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def _fmt_event(event) -> str:
    """Format a single caldav event into a readable string."""
    try:
        comp = icalendar.Calendar.from_ical(event.data)
        for component in comp.walk():
            if component.name == "VEVENT":
                summary = str(component.get("SUMMARY", "No title"))
                dtstart = component.get("DTSTART")
                dtend   = component.get("DTEND")

                if dtstart is None:
                    return f"• {summary}"

                start = dtstart.dt
                end   = dtend.dt if dtend else None

                # All-day events (date, not datetime)
                if isinstance(start, date) and not isinstance(start, datetime):
                    return f"• {summary} (all day)"

                # Timed events
                local_start = start.astimezone(_tz()) if start.tzinfo else start.replace(tzinfo=_tz())
                time_str = local_start.strftime("%-I:%M %p")

                if end and isinstance(end, datetime):
                    local_end = end.astimezone(_tz()) if end.tzinfo else end.replace(tzinfo=_tz())
                    time_str += f"–{local_end.strftime('%-I:%M %p')}"

                return f"• {summary} at {time_str}"
    except Exception as e:
        logger.warning(f"Error formatting event: {e}")
    return "• (event)"


# ── Public API ────────────────────────────────────────────────────────────────
def is_configured() -> bool:
    return bool(ICLOUD_USERNAME and ICLOUD_APP_PASSWORD)


def get_events_for_day(target_date: date) -> list[str]:
    """Return a list of formatted event strings for a given date."""
    if not is_configured():
        return []

    try:
        client = _get_client()
        cals   = _get_calendars(client)
        if not cals:
            return [f"(Calendar '{CALENDAR_NAME}' not found)"]

        tz    = _tz()
        start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=tz)
        end   = start + timedelta(days=1)

        results = []
        for cal in cals:
            try:
                results.extend(cal.date_search(start=start, end=end, expand=True))
            except Exception:
                continue
        return [_fmt_event(e) for e in results] if results else []

    except Exception as e:
        logger.error(f"iCloud CalDAV error: {e}")
        return [f"(Could not fetch calendar: {e})"]


def get_events_for_range(start_date: date, end_date: date) -> dict[str, list[str]]:
    """
    Return events grouped by date for a date range.
    Returns {date_label: [event_strings]}
    """
    if not is_configured():
        return {}

    try:
        client = _get_client()
        cals   = _get_calendars(client)
        if not cals:
            return {}

        tz    = _tz()
        start = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz)
        end   = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=tz)

        events = []
        for cal in cals:
            try:
                events.extend(cal.date_search(start=start, end=end, expand=True))
            except Exception:
                continue

        grouped: dict[str, list[str]] = {}
        for event in events:
            try:
                comp = icalendar.Calendar.from_ical(event.data)
                for component in comp.walk():
                    if component.name == "VEVENT":
                        dtstart = component.get("DTSTART")
                        if dtstart is None:
                            continue
                        ev_start = dtstart.dt
                        if isinstance(ev_start, datetime):
                            ev_date = ev_start.astimezone(tz).date()
                        else:
                            ev_date = ev_start
                        label = ev_date.strftime("%A %-d %b")
                        grouped.setdefault(label, []).append(_fmt_event(event))
            except Exception:
                continue

        return grouped

    except Exception as e:
        logger.error(f"iCloud CalDAV error: {e}")
        return {}


def add_event(
    title: str,
    event_datetime: datetime | date,
    duration_minutes: int = 60,
    all_day: bool = False,
) -> bool:
    """
    Add an event to the shared calendar.
    Returns True on success, False on failure.
    """
    if not is_configured():
        return False

    try:
        client = _get_client()
        cals   = _get_calendars(client)
        cal    = cals[-1] if cals else None
        if cal is None:
            return False

        tz = _tz()

        # Build iCal event
        ical_cal = icalendar.Calendar()
        ical_cal.add("prodid", "-//FamilyBot//EN")
        ical_cal.add("version", "2.0")

        event = icalendar.Event()
        event.add("summary", title)
        event.add("uid", f"{datetime.now().timestamp()}@familybot")
        event.add("dtstamp", datetime.now(tz=tz))

        if all_day or isinstance(event_datetime, date) and not isinstance(event_datetime, datetime):
            d = event_datetime if isinstance(event_datetime, date) else event_datetime.date()
            event.add("dtstart", d)
            event.add("dtend", d + timedelta(days=1))
        else:
            if isinstance(event_datetime, datetime):
                start = event_datetime.replace(tzinfo=tz) if event_datetime.tzinfo is None else event_datetime
            else:
                start = datetime.combine(event_datetime, datetime.min.time()).replace(tzinfo=tz)
            end = start + timedelta(minutes=duration_minutes)
            event.add("dtstart", start)
            event.add("dtend", end)

        ical_cal.add_component(event)
        cal.add_event(ical_cal.to_ical().decode("utf-8"))
        return True

    except Exception as e:
        logger.error(f"Failed to add calendar event: {e}")
        return False


def list_calendars() -> list[str]:
    """List all available calendar names (useful for setup/debugging)."""
    if not is_configured():
        return []
    try:
        client = _get_client()
        principal = client.principal()
        names = []
        for cal in principal.calendars():
            try:
                name = cal.get_properties([dav.DisplayName()])["{DAV:}displayname"]
                names.append(name)
            except Exception:
                pass
        return names
    except Exception as e:
        logger.error(f"Error listing calendars: {e}")
        return []
