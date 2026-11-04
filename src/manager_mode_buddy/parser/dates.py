"""
FC25's `DBOFIELDTYPE_DATE` encoding, and estimating a save's current in-game date.

**The encoding: days since 1582-10-14.** Earlier sessions tried reading the 20-bit `birthdate`
field as a packed `year<<9 | month<<5 | day` bitfield; it produced nonsense against a known
player and was left undecoded (HANDOFF.md, Milestone 1 finding 2). It is simply a day count.
The epoch is the day before the Gregorian calendar began -- an epoch several engines of this
lineage use -- and it is pinned by three independent checks on the real save:

  - The one player whose exact date of birth was known from earlier research (playerid 462067,
    2007-10-10) decodes to exactly that date, and to no other epoch tried.
  - Across 13,555 players present in both the save and a public reference dataset, the decoded
    birth **year** matches the reference for 99.3%. A one-day epoch error would show up as a
    much larger disagreement, since it would move every player born on 1 January.
  - The same field on `playerjointeamdate` decodes to a sensible transfer-history distribution
    (dense through the career's seasons, then empty), rather than noise.

**The current date is a different problem.** `career_calendar.currdate` holds it as a plain
YYYYMMDD integer, but that table is a single row and the generic table locator does not find it
reliably -- the one candidate it produced decoded `currdate` as exactly the field's declared
minimum, i.e. garbage. Rather than fabricate a date, `estimate_current_date` infers one from
evidence that *is* readable at scale: the dates on which players joined their current clubs.
Those cluster densely through the seasons already played and stop at the present, so the end of
the dense region is approximately "now". On the test save this gives 2027-08-21, corroborated
independently by the latest credible contract activity in `career_playercontract`
(2027-08-04) -- two unrelated tables agreeing within three weeks.

That residual uncertainty is why age is reported as "as of <date>" everywhere it appears: a
few weeks of error only affects players whose birthday falls inside the window, but it is a
real limitation and is not worth hiding.
"""
from datetime import date, timedelta
from typing import Iterable, List, Optional

# Days since this date. See the module docstring for how it was pinned.
EPOCH = date(1582, 10, 14)

# A decoded date outside this range is not a real date -- it's a junk row, or the "unset"
# sentinel (the field's all-bits-set value, 1048575, decodes to the year 4453).
_EARLIEST = date(1900, 1, 1)
_LATEST = date(2100, 1, 1)


def decode_date(days: Optional[int]) -> Optional[date]:
    """Decode a DBOFIELDTYPE_DATE day count, or None if it isn't a plausible date."""
    if days is None or not isinstance(days, int) or days < 0:
        return None
    try:
        decoded = EPOCH + timedelta(days=days)
    except (OverflowError, ValueError):
        return None
    return decoded if _EARLIEST <= decoded <= _LATEST else None


def age_on(born: date, when: date) -> int:
    """Completed years, counting a birthday that hasn't happened yet as not counted."""
    return when.year - born.year - ((when.month, when.day) < (born.month, born.day))


# Tuning for estimate_current_date. A real save has hundreds of transfers per window, so a
# genuine "recent" date always has plenty of company; the sparse tail past the current date is
# decode noise (on the test save: thousands of joins per year through 2027, then 1-23 per year
# for 2028-2033).
_WINDOW_DAYS = 180
_MIN_NEIGHBOURS = 30


def estimate_current_date(join_dates: Iterable[date]) -> Optional[date]:
    """Estimate a save's current in-game date from the dates players joined their clubs.

    Walks down from the newest date and returns the first one with at least `_MIN_NEIGHBOURS`
    other joins in the preceding `_WINDOW_DAYS` -- i.e. the end of the dense region, ignoring
    the scattered far-future dates that decode artifacts produce. Returns None if there simply
    isn't enough transfer history to be confident, in which case callers must treat the current
    date (and therefore age) as unknown rather than guess.
    """
    dates: List[date] = sorted(d for d in join_dates if d is not None)
    if len(dates) < _MIN_NEIGHBOURS * 2:
        return None
    for index in range(len(dates) - 1, 0, -1):
        candidate = dates[index]
        cutoff = candidate - timedelta(days=_WINDOW_DAYS)
        neighbours = 0
        for earlier in reversed(dates[:index]):
            if earlier <= cutoff:
                break
            neighbours += 1
            if neighbours >= _MIN_NEIGHBOURS:
                return candidate
    return None
