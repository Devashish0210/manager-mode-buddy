import pytest

from manager_mode_buddy import filters as F
from manager_mode_buddy.models import Player


def player(**kwargs) -> Player:
    defaults = dict(id=100000, overall=70, potential=80, position="CM")
    defaults.update(kwargs)
    return Player(**defaults)


# --- position ------------------------------------------------------------------------------

def test_position_filter_matches_side_specific_variants():
    assert F.PositionFilter(("CM",)).matches(player(position="RCM"))
    assert F.PositionFilter(("CB",)).matches(player(position="LCB"))
    assert not F.PositionFilter(("CM",)).matches(player(position="ST"))


def test_position_filter_ignores_secondary_positions_unless_asked():
    """Secondary positions decode less reliably than the primary one (see PositionFilter), so
    the default is the trustworthy field and widening is explicit."""
    p = player(position="LM", other_positions=["CAM"])
    assert not F.PositionFilter(("CAM",)).matches(p)
    assert F.PositionFilter(("CAM",), include_secondary=True).matches(p)


def test_position_filter_excludes_players_with_no_known_position():
    assert not F.PositionFilter(("CM",)).matches(player(position=None))


# --- overall / potential / growth ----------------------------------------------------------

def test_overall_filter_respects_both_bounds():
    f = F.OverallFilter(minimum=72, maximum=76)
    assert f.matches(player(overall=74))
    assert not f.matches(player(overall=71))
    assert not f.matches(player(overall=77))


def test_unknown_value_never_matches_a_range_filter():
    """The rule the whole engine rests on: a filtered field the save didn't expose is not a
    pass. A search for 'potential >= 85' must not return players whose potential is unknown."""
    assert not F.PotentialFilter(minimum=85).matches(player(potential=None))
    assert not F.OverallFilter(maximum=99).matches(player(overall=None))


def test_growth_filter_uses_potential_minus_overall():
    assert F.GrowthFilter(minimum=10).matches(player(overall=70, potential=80))
    assert not F.GrowthFilter(minimum=11).matches(player(overall=70, potential=80))
    assert not F.GrowthFilter(minimum=1).matches(player(overall=70, potential=None))


# --- attributes ----------------------------------------------------------------------------

def test_attribute_filter_matches_on_parsed_attributes():
    p = player(attributes={"vision": 82, "shortpassing": 77})
    assert F.AttributeFilter("vision", minimum=80).matches(p)
    assert not F.AttributeFilter("shortpassing", minimum=78).matches(p)


def test_attribute_filter_excludes_players_missing_that_attribute():
    assert not F.AttributeFilter("vision", minimum=1).matches(player(attributes={}))


@pytest.mark.parametrize("expression,expected", [
    ("vision>=80", F.AttributeFilter("vision", minimum=80)),
    ("vision > 80", F.AttributeFilter("vision", minimum=81)),
    ("vision<=80", F.AttributeFilter("vision", maximum=80)),
    ("vision < 80", F.AttributeFilter("vision", maximum=79)),
    ("vision=80", F.AttributeFilter("vision", minimum=80, maximum=80)),
    ("finishing=75-85", F.AttributeFilter("finishing", minimum=75, maximum=85)),
    ("Short Passing >= 78", F.AttributeFilter("shortpassing", minimum=78)),
    ("defensive_awareness>=70", F.AttributeFilter("defensiveawareness", minimum=70)),
])
def test_parse_attribute_filter_accepts_the_documented_forms(expression, expected):
    assert F.parse_attribute_filter(expression) == expected


@pytest.mark.parametrize("expression", ["nonsense", "vision >>= 80", "vision>=", ">=80"])
def test_parse_attribute_filter_rejects_malformed_expressions(expression):
    with pytest.raises(F.FilterError):
        F.parse_attribute_filter(expression)


def test_parse_attribute_filter_names_the_unknown_attribute():
    with pytest.raises(F.FilterError, match="unknown attribute 'wingspan'"):
        F.parse_attribute_filter("wingspan>=80")


def test_parse_attribute_filter_rejects_inverted_range():
    with pytest.raises(F.FilterError, match="invalid range"):
        F.parse_attribute_filter("vision=85-75")


# --- identity/club -------------------------------------------------------------------------

def test_foot_free_agent_club_and_name_filters():
    p = player(preferred_foot="Left", is_free_agent=True, club_id=111592, name="A. Player")
    assert F.FootFilter("left").matches(p)
    assert not F.FootFilter("Right").matches(p)
    assert F.FreeAgentFilter().matches(p)
    assert F.ClubFilter(111592).matches(p)
    assert F.NameFilter("player").matches(p)
    assert not F.NameFilter("player").matches(player(name=None))


# --- composition ---------------------------------------------------------------------------

def test_apply_ands_every_filter_together():
    squad = [
        player(id=1001, position="CAM", overall=74, potential=88, attributes={"vision": 85}),
        player(id=1002, position="CAM", overall=74, potential=88, attributes={"vision": 70}),
        player(id=1003, position="ST", overall=74, potential=88, attributes={"vision": 85}),
        player(id=1004, position="CAM", overall=64, potential=88, attributes={"vision": 85}),
    ]
    matched = F.apply(squad, [
        F.PositionFilter(("CAM",)),
        F.OverallFilter(minimum=70),
        F.AttributeFilter("vision", minimum=80),
    ])
    assert [p.id for p in matched] == [1001]


def test_no_filters_returns_everyone():
    squad = [player(id=1), player(id=2)]
    assert F.apply(squad, []) == squad


# --- sorting -------------------------------------------------------------------------------

def test_sort_players_by_headline_keys_and_attributes():
    squad = [
        player(id=1, overall=70, potential=80, attributes={"vision": 60}),
        player(id=2, overall=75, potential=78, attributes={"vision": 90}),
    ]
    assert [p.id for p in F.sort_players(squad, "potential")] == [1, 2]
    assert [p.id for p in F.sort_players(squad, "overall")] == [2, 1]
    assert [p.id for p in F.sort_players(squad, "growth")] == [1, 2]
    assert [p.id for p in F.sort_players(squad, "Vision")] == [2, 1]
    assert [p.id for p in F.sort_players(squad, "potential", descending=False)] == [2, 1]


def test_players_missing_the_sort_value_sort_last_when_descending():
    squad = [player(id=1, potential=None), player(id=2, potential=80)]
    assert [p.id for p in F.sort_players(squad, "potential")] == [2, 1]


def test_sort_players_rejects_an_unknown_key():
    with pytest.raises(F.FilterError, match="unknown sort key"):
        F.sort_players([], "charisma")


# --- plausibility screen --------------------------------------------------------------------

def test_plausible_filter_keeps_a_normal_player():
    assert F.PlausiblePlayerFilter().matches(player(overall=64, attributes={"vision": 71}))


def test_plausible_filter_rejects_the_decode_artifact_shapes():
    """Both signatures measured on the real save: an impossible overall, and an attribute above
    the game's hard 99 cap."""
    assert not F.PlausiblePlayerFilter().matches(player(overall=1, potential=99))
    assert not F.PlausiblePlayerFilter().matches(player(overall=None))
    assert not F.PlausiblePlayerFilter().matches(player(overall=77, attributes={"vision": 103}))
