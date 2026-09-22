# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Timezone handling for persisted report timestamps.

All persisted timestamps are naive wall-clock datetimes in a configurable
timezone (IST by default, preserving historical behavior). The timezone can
be selected via the ``--report-tz`` CLI option or the ``report_tz`` key in
a datastore YAML section.

Supported formats:

- IANA zone names: ``Asia/Kolkata``, ``UTC``, ``America/New_York``
- Fixed offsets: ``+05:30``, ``-08:00``
- Shorthand hours: ``+05``

"""

import datetime as dt

#: Default timezone: IST (fixed +05:30), preserving legacy behavior.
DEFAULT_TZ = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")

#: Legacy alias kept for backwards compatibility with imports.
IST = DEFAULT_TZ


def parse_timezone(value):
    """Parse a timezone specification into a ``tzinfo``.

    :param value: IANA name (``Asia/Kolkata``), fixed offset (``+05:30``),
        or ``None``.
    :return: A ``tzinfo`` instance; :data:`DEFAULT_TZ` when ``value`` is
        falsy or unparseable (a warning-free fallback to legacy behavior).
    """

    if not value:
        return DEFAULT_TZ
    text = str(value).strip()

    # Fixed offset forms: +05:30, -08:00, +05
    try:
        if text.startswith(("+", "-")) and (text[1:].replace(":", "").isdigit()):
            parts = text[1:].split(":")
            hours = int(parts[0])
            minutes = int(parts[1]) if len(parts) > 1 and parts[1] else 0
            delta = dt.timedelta(hours=hours, minutes=minutes)
            if text[0] == "-":
                delta = -delta
            return dt.timezone(delta)
    except (ValueError, IndexError):
        pass

    # IANA zone name (requires zoneinfo; stdlib since Python 3.9)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:
        pass

    # Unrecognized: fall back to legacy default rather than crash a test run.
    return DEFAULT_TZ


def now_naive(tzinfo):
    """Return current wall-clock time in ``tzinfo`` as naive datetime.

    :param tzinfo: Timezone to render the wall clock in.
    :return: Naive datetime with ``tzinfo=None``.
    """

    return dt.datetime.now(tzinfo).replace(tzinfo=None)


def epoch_to_naive(value, tzinfo):
    """Convert epoch seconds to naive wall-clock datetime in ``tzinfo``.

    :param value: POSIX epoch timestamp.
    :param tzinfo: Timezone to render the wall clock in.
    :return: Naive datetime, or ``None`` if input is ``None``.
    """

    if value is None:
        return None
    return (
        dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
        .astimezone(tzinfo)
        .replace(tzinfo=None)
    )
