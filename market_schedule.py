"""
market_schedule.py

Determines whether the NYSE (US stock market) is currently open.

Handles:
  - Regular hours: 9:30 AM – 4:00 PM Eastern Time, Monday–Friday
  - NYSE holidays (computed dynamically for any year)
  - Early-close days (1:00 PM ET):
      • Day before Independence Day (July 3, when applicable)
      • Day before Thanksgiving (Wednesday)
      • Christmas Eve (December 24, when a weekday)
  - Weekends
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(16, 0)
EARLY_CLOSE  = time(13, 0)


# ── Holiday computation helpers ───────────────────────────────────────────────

def _nearest_weekday(d: date) -> date:
    """Shift a date that falls on a weekend to its observed weekday."""
    if d.weekday() == 5:   # Saturday → Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:   # Sunday  → Monday
        return d + timedelta(days=1)
    return d


def _easter(year: int) -> date:
    """Return Easter Sunday for the given year (Anonymous Gregorian algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day   = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence of ``weekday`` (0=Mon … 6=Sun) in month/year."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset) + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of ``weekday`` in month/year."""
    next_month_first = (
        date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    )
    last_day = next_month_first - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


# ── Public holiday / early-close tables ──────────────────────────────────────

def get_nyse_holidays(year: int) -> set[date]:
    """
    Return the set of NYSE market-holiday dates for *year*.

    NYSE holidays (source: https://www.nyse.com/markets/hours-calendars):
      New Year's Day, MLK Jr. Day, Presidents' Day, Good Friday,
      Memorial Day, Juneteenth (since 2022), Independence Day,
      Labor Day, Thanksgiving Day, Christmas Day.
    Each is observed on the nearest weekday when it falls on a weekend.
    """
    h: set[date] = set()

    # New Year's Day
    h.add(_nearest_weekday(date(year, 1, 1)))

    # Martin Luther King Jr. Day — 3rd Monday of January
    h.add(_nth_weekday(year, 1, 0, 3))

    # Presidents' Day (Washington's Birthday) — 3rd Monday of February
    h.add(_nth_weekday(year, 2, 0, 3))

    # Good Friday — 2 days before Easter Sunday
    h.add(_easter(year) - timedelta(days=2))

    # Memorial Day — last Monday of May
    h.add(_last_weekday(year, 5, 0))

    # Juneteenth National Independence Day — June 19 (observed), from 2022
    if year >= 2022:
        h.add(_nearest_weekday(date(year, 6, 19)))

    # Independence Day — July 4 (observed)
    h.add(_nearest_weekday(date(year, 7, 4)))

    # Labor Day — 1st Monday of September
    h.add(_nth_weekday(year, 9, 0, 1))

    # Thanksgiving Day — 4th Thursday of November
    h.add(_nth_weekday(year, 11, 3, 4))

    # Christmas Day — December 25 (observed)
    h.add(_nearest_weekday(date(year, 12, 25)))

    return h


def get_nyse_early_closes(year: int) -> set[date]:
    """
    Return the set of NYSE early-close dates (market closes 1:00 PM ET).

    Early-close days:
      • July 3 (day before Independence Day) — unless July 3 *is* the holiday
      • Wednesday before Thanksgiving
      • December 24 (Christmas Eve) when it falls on a weekday
    """
    ec: set[date] = set()

    # Day before Independence Day --------------------------------------------
    jul4 = date(year, 7, 4)
    if jul4.weekday() == 5:
        # July 4 is Saturday → holiday observed on July 3 (Friday);
        # that day is the full holiday, not an early close.
        pass
    elif jul4.weekday() == 6:
        # July 4 is Sunday → holiday observed on July 5 (Monday);
        # July 3 (Friday) is still an early-close day.
        ec.add(date(year, 7, 3))
    else:
        # Normal case: July 3 is a weekday before the holiday.
        d = date(year, 7, 3)
        if d.weekday() < 5:
            ec.add(d)

    # Wednesday before Thanksgiving ------------------------------------------
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    ec.add(thanksgiving - timedelta(days=1))

    # Christmas Eve ----------------------------------------------------------
    dec24 = date(year, 12, 24)
    if dec24.weekday() < 5:
        ec.add(dec24)

    return ec


# ── Core status functions ─────────────────────────────────────────────────────

def _to_eastern(dt: datetime | None) -> datetime:
    """Normalise *dt* to an Eastern-timezone-aware datetime."""
    if dt is None:
        return datetime.now(tz=EASTERN)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=EASTERN)
    return dt.astimezone(EASTERN)


def is_market_open(now: datetime | None = None) -> bool:
    """
    Return ``True`` if the NYSE is currently open for regular trading.

    Parameters
    ----------
    now:
        Timezone-aware datetime to test.  Defaults to the current time.
    """
    now = _to_eastern(now)
    today = now.date()
    current = now.time()

    if today.weekday() >= 5:
        return False

    if today in get_nyse_holidays(today.year):
        return False

    close = (
        EARLY_CLOSE
        if today in get_nyse_early_closes(today.year)
        else MARKET_CLOSE
    )
    return MARKET_OPEN <= current < close


def seconds_until_market_open(now: datetime | None = None) -> float:
    """
    Return seconds until the next NYSE open.  Returns ``0.0`` if open now.
    Searches up to 14 calendar days ahead to handle long holiday stretches.
    """
    now = _to_eastern(now)

    if is_market_open(now):
        return 0.0

    candidate = now.date()
    for _ in range(14):
        holidays = get_nyse_holidays(candidate.year)
        if candidate.weekday() < 5 and candidate not in holidays:
            open_dt = datetime.combine(candidate, MARKET_OPEN, tzinfo=EASTERN)
            if open_dt > now:
                return (open_dt - now).total_seconds()
        candidate += timedelta(days=1)

    # Fallback — should never be reached in practice
    return 86_400.0


def market_status_str(now: datetime | None = None) -> str:
    """Return a human-readable string describing the current NYSE market status."""
    now = _to_eastern(now)
    today = now.date()
    holidays = get_nyse_holidays(today.year)
    early_closes = get_nyse_early_closes(today.year)

    if today.weekday() >= 5:
        return f"closed — weekend ({today.strftime('%A')})"

    if today in holidays:
        return f"closed — NYSE holiday ({today.strftime('%A, %B %-d')})"

    if today in early_closes:
        close_lbl = f"{EARLY_CLOSE.hour % 12 or 12}:{EARLY_CLOSE.minute:02d} {'AM' if EARLY_CLOSE.hour < 12 else 'PM'}"
        if is_market_open(now):
            return f"OPEN — early close day (closes {close_lbl} ET)"
        return f"closed — early close day (closed at {close_lbl} ET)"

    if is_market_open(now):
        return "OPEN (9:30 AM – 4:00 PM ET)"

    return "closed — outside regular hours (9:30 AM – 4:00 PM ET)"
