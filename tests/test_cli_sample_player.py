from manager_mode_buddy.cli import _pick_sample_player
from manager_mode_buddy.models import Player


def test_skips_low_id_template_like_rows():
    """Regression test: real saves have turned up low-id rows with impossible stats
    (playerid=1 with potential < overall; playerid=2 with overall=0) that look like
    template/placeholder records, not real players. The sample picker should skip them."""
    junk1 = Player(id=1, overall=88, potential=1, overall_source="career_playergrowthuserseason")
    junk2 = Player(id=2, overall=0, potential=30, overall_source="career_playergrowthuserseason")
    real = Player(id=462067, overall=71, potential=76, overall_source="career_playergrowthuserseason")

    picked = _pick_sample_player([junk1, junk2, real])
    assert picked is real


def test_prefers_growth_sourced_player_over_identity_sourced():
    identity_sourced = Player(id=50000, overall=70, overall_source="identity")
    growth_sourced = Player(id=60000, overall=72, overall_source="career_playergrowthuserseason")

    picked = _pick_sample_player([identity_sourced, growth_sourced])
    assert picked is growth_sourced


def test_falls_back_to_any_player_with_overall_if_nothing_plausible():
    only_option = Player(id=1, overall=88, potential=1, overall_source="career_playergrowthuserseason")
    picked = _pick_sample_player([only_option])
    assert picked is only_option


def test_returns_none_for_empty_list():
    assert _pick_sample_player([]) is None
