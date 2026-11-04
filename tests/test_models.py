import pytest

from manager_mode_buddy.models import ParsedCareer, Player


def test_player_only_requires_id():
    p = Player(id=123456)
    assert p.name is None
    assert p.overall is None
    assert p.attributes == {}
    assert p.other_positions == []


def test_player_is_immutable():
    p = Player(id=1)
    with pytest.raises(Exception):
        p.overall = 80  # type: ignore[misc]


def test_parsed_career_counts():
    players = [Player(id=1), Player(id=2)]
    career = ParsedCareer(save_path="x", players=players, team_ids=[10, 20, 30])
    assert career.player_count == 2
    assert career.team_count == 3
