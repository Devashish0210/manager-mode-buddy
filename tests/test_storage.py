import pytest

from manager_mode_buddy.models import ParsedCareer, Player
from manager_mode_buddy import storage


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))


def _career() -> ParsedCareer:
    players = [
        Player(
            id=123456,
            name="Example Player",
            name_source="sofifa",
            nationality_id=14,
            preferred_foot="Right",
            height_cm=181,
            weight_kg=75,
            overall=75,
            overall_source="career_playergrowthuserseason",
            potential=87,
            position="CAM",
            position_confirmed=True,
            other_positions=["CM", "RW"],
            skill_moves=4,
            weak_foot=3,
            club_id=999,
            is_free_agent=False,
            attributes={"vision": 82, "finishing": 70},
        ),
        Player(id=2),
    ]
    return ParsedCareer(save_path="C:/saves/ManagerCareerFake", players=players, team_ids=[999, 1000])


def test_default_profile_name_slugifies_save_filename():
    assert storage.default_profile_name("C:/saves/Arsenal 2027.save") == "arsenal_2027"


def test_save_and_load_career_round_trips_all_fields():
    career = _career()
    storage.save_career("arsenal_2027", career)

    loaded = storage.load_career("arsenal_2027")

    assert loaded.save_path == career.save_path
    assert loaded.team_ids == [999, 1000]
    assert len(loaded.players) == 2

    full = next(p for p in loaded.players if p.id == 123456)
    original = next(p for p in career.players if p.id == 123456)
    assert full == original

    sparse = next(p for p in loaded.players if p.id == 2)
    assert sparse.name is None
    assert sparse.attributes == {}
    assert sparse.other_positions == []


def test_load_career_missing_profile_raises():
    with pytest.raises(storage.CareerNotFoundError):
        storage.load_career("does_not_exist")


def test_list_careers_empty_when_nothing_imported():
    assert storage.list_careers() == []


def test_list_careers_reports_imported_profiles():
    storage.save_career("arsenal_2027", _career())
    storage.save_career("leeds_2030", _career())

    profiles = {meta.profile: meta for meta in storage.list_careers()}
    assert set(profiles) == {"arsenal_2027", "leeds_2030"}
    assert profiles["arsenal_2027"].player_count == 2
    assert profiles["arsenal_2027"].team_count == 2


def test_reimporting_a_profile_replaces_its_data():
    storage.save_career("arsenal_2027", _career())

    smaller = ParsedCareer(save_path="x", players=[Player(id=1)], team_ids=[])
    storage.save_career("arsenal_2027", smaller)

    loaded = storage.load_career("arsenal_2027")
    assert len(loaded.players) == 1
    assert loaded.team_ids == []
