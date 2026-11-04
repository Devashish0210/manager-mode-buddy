from manager_mode_buddy.extractor import _is_reserved_template_id, _valid_rating


def test_accepts_values_in_range():
    assert _valid_rating(1) == 1
    assert _valid_rating(50) == 50
    assert _valid_rating(99) == 99


def test_rejects_values_above_99():
    assert _valid_rating(100) is None
    assert _valid_rating(121) is None


def test_rejects_zero_and_negative():
    assert _valid_rating(0) is None
    assert _valid_rating(-5) is None


def test_passes_through_none():
    assert _valid_rating(None) is None


def test_playerid_1_is_a_reserved_template_slot():
    """Regression test: playerid=1 decodes to obviously-sentinel values (e.g. potential=121,
    tattooback=1023, birthdate=1048568 -- all "every bit set" patterns) across every physical
    copy of the identity tables plus the growth table (overall=0) in a real save. Each field
    individually could slip past _valid_rating/potential>=overall (see HANDOFF.md), so it's
    excluded by id directly rather than relying on those to always catch it."""
    assert _is_reserved_template_id(1) is True


def test_other_ids_are_not_reserved():
    assert _is_reserved_template_id(2) is False
    assert _is_reserved_template_id(462067) is False
