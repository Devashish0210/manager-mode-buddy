from datetime import date, timedelta

from manager_mode_buddy.parser import dates


def test_the_known_birth_date_decodes_exactly():
    """Ground truth from earlier reverse-engineering: playerid 462067 was born 2007-10-10, and
    the raw `birthdate` in the real save is 155224. This is the check that pins the epoch --
    if it ever fails, the epoch is wrong, not the test."""
    assert dates.decode_date(155224) == date(2007, 10, 10)


def test_sentinels_and_junk_decode_to_nothing_rather_than_a_fake_date():
    assert dates.decode_date(1048575) is None      # the field's all-bits-set "unset" value
    assert dates.decode_date(0) is None            # the epoch itself is not a birth date
    assert dates.decode_date(None) is None
    assert dates.decode_date(-1) is None
    assert dates.decode_date("155224") is None


def test_age_counts_completed_years():
    born = date(2007, 10, 10)
    assert dates.age_on(born, date(2027, 10, 9)) == 19    # birthday not reached yet
    assert dates.age_on(born, date(2027, 10, 10)) == 20   # birthday today
    assert dates.age_on(born, date(2027, 10, 11)) == 20


def _dense(end: date, count: int = 100):
    return [end - timedelta(days=i) for i in range(count)]


def test_current_date_estimate_is_the_end_of_the_dense_region():
    assert dates.estimate_current_date(_dense(date(2027, 8, 21))) == date(2027, 8, 21)


def test_scattered_far_future_dates_do_not_move_the_estimate():
    """Decode artifacts put a handful of joins years past the end of the career; the estimate
    has to ignore them or every age in the save shifts."""
    noisy = _dense(date(2027, 8, 21)) + [date(2031, 5, 13), date(2032, 8, 2), date(2033, 5, 11)]
    assert dates.estimate_current_date(noisy) == date(2027, 8, 21)


def test_estimate_returns_none_without_enough_history():
    """Better no age at all than every age off by however many seasons have been played."""
    assert dates.estimate_current_date(_dense(date(2027, 8, 21), count=10)) is None
    assert dates.estimate_current_date([]) is None
