"""
PlayStyle decoding. The bit -> name mapping itself was established empirically (see
playstyles.py); what's tested here is the mechanics built on top of it -- the bit split across
two columns, how PlayStyle+ relates to PlayStyle, and that traits stay out of the PlayStyle
list.
"""
import pytest

from manager_mode_buddy import playstyles as PS
from manager_mode_buddy.filters import PlayStyleFilter, TraitFilter
from manager_mode_buddy.models import Player


def bit(n: int) -> tuple:
    """(trait1, trait2) with just bit `n` set, split the way the save stores it."""
    return (1 << n, 0) if n < PS.TRAIT1_BITS else (0, 1 << (n - PS.TRAIT1_BITS))


def test_the_mask_spans_both_stored_columns():
    """trait2 continues the same bit space at bit 30, which is the whole reason a naive read
    of either column alone gets goalkeeper PlayStyles wrong."""
    assert PS.mask(*bit(0)) == 1
    assert PS.mask(*bit(29)) == 1 << 29
    assert PS.mask(*bit(30)) == 1 << 30   # first bit of trait2
    assert PS.mask(*bit(40)) == 1 << 40


def test_decodes_a_playstyle_from_each_column():
    styles, _, _ = PS.decode(*bit(4))                      # trait1: Power Header
    assert styles == ["Power Header"]
    styles, _, _ = PS.decode(*bit(33))                     # trait2: Deflector
    assert styles == ["Deflector"]


def test_playstyle_plus_is_also_a_playstyle():
    """A gold Acrobatic is still Acrobatic -- a search for it has to find the player."""
    t1, t2 = bit(25)
    styles, plus, _ = PS.decode(0, 0, t1, t2)
    assert styles == ["Acrobatic"] and plus == ["Acrobatic"]


def test_plain_and_plus_masks_are_combined():
    p1, p2 = bit(4)     # Power Header, plain
    i1, i2 = bit(0)     # Finesse Shot, gold
    styles, plus, _ = PS.decode(p1, p2, i1, i2)
    assert styles == ["Finesse Shot", "Power Header"]
    assert plus == ["Finesse Shot"]


def test_legacy_traits_are_reported_separately_from_playstyles():
    """The game stores these in the same mask but doesn't present them as PlayStyles, so a
    user checking against their screen must not be shown them as such."""
    styles, _, traits = PS.decode(*bit(40))
    assert styles == []
    assert traits == ["Leadership"]


@pytest.mark.parametrize("unnamed", PS.UNNAMED_BITS)
def test_bits_that_were_never_identified_produce_nothing(unnamed):
    """Bits 34, 35 and 38 appear only on career-generated players, so no reference data could
    name them. They must stay silent rather than be guessed into a label."""
    styles, _, traits = PS.decode(*bit(unnamed))
    assert styles == [] and traits == []


def test_no_playstyles_decodes_to_empty_lists_not_none():
    assert PS.decode(0, 0, 0, 0) == ([], [], [])
    assert PS.decode(None, None) == ([], [], [])


def test_there_are_exactly_the_thirty_four_playstyles_fc25_has():
    assert len(PS.ALL_PLAYSTYLES) == 34
    assert len(set(PS.ALL_PLAYSTYLES)) == 34
    assert sorted(PS.PLAYSTYLES) == list(range(34))


def test_lookup_is_case_insensitive_and_accepts_the_in_game_wording():
    assert PS.resolve("power header") == "Power Header"
    assert PS.resolve("  POWER   HEADER ") == "Power Header"
    # The reference dataset and the game word these two differently; both must resolve.
    assert PS.resolve("Whipped Pass") == "Whipped Cross"
    assert PS.resolve("rush out") == "Close Down"
    assert PS.resolve("Nonsense") is None


def test_categories_are_listed_in_the_games_own_order():
    grouped = PS.by_category(frozenset({"Deflector", "Power Header", "Jockey"}))
    assert [c for c, _ in grouped] == ["Scoring", "Defending", "Goalkeeper"]


# --- filtering -------------------------------------------------------------------------------

def _player(**kw) -> Player:
    return Player(id=1, **kw)


def test_playstyle_filter_requires_all_of_them():
    p = _player(playstyles=["Power Header", "Aerial"])
    assert PlayStyleFilter(("Power Header",)).matches(p)
    assert PlayStyleFilter(("Power Header", "Aerial")).matches(p)
    assert not PlayStyleFilter(("Power Header", "Trivela")).matches(p)


def test_playstyle_plus_filter_wants_the_gold_one_specifically():
    p = _player(playstyles=["Acrobatic", "Aerial"], playstyles_plus=["Acrobatic"])
    assert PlayStyleFilter(("Acrobatic",), plus_only=True).matches(p)
    assert not PlayStyleFilter(("Aerial",), plus_only=True).matches(p)
    # ...but a plain search still finds the gold one.
    assert PlayStyleFilter(("Acrobatic",)).matches(p)


def test_a_player_with_no_playstyles_never_matches():
    """Unknown is not a pass, the same rule the rest of the filter engine follows."""
    assert not PlayStyleFilter(("Aerial",)).matches(_player())


def test_trait_filter_does_not_match_playstyles_or_vice_versa():
    p = _player(playstyles=["Aerial"], traits=["Leadership"])
    assert TraitFilter(("Leadership",)).matches(p)
    assert not TraitFilter(("Aerial",)).matches(p)
    assert not PlayStyleFilter(("Leadership",)).matches(p)


def test_filter_descriptions_say_which_kind_they_are():
    assert "PlayStyle+" in PlayStyleFilter(("Aerial",), plus_only=True).describe()
    assert PlayStyleFilter(("Aerial",)).describe() == "PlayStyle: Aerial"
    assert TraitFilter(("Leadership",)).describe() == "trait: Leadership"


# --- end-to-end through the CLI --------------------------------------------------------------

def test_search_filters_by_playstyle_and_rejects_a_typo(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from manager_mode_buddy import cli
    from manager_mode_buddy.extractor import ExtractionResult

    squad = [
        Player(id=1001, name="Target Man", position="ST", overall=80, potential=85,
               playstyles=["Power Header", "Aerial"], playstyles_plus=["Aerial"]),
        Player(id=1002, name="Playmaker", position="CAM", overall=80, potential=85,
               playstyles=["Incisive Pass"]),
    ]
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract",
                        lambda *a, **k: ExtractionResult(players=squad, team_ids=[]))
    meta = tmp_path / "fifa_ng_db-meta.xml"
    meta.write_text("<database></database>")
    save = tmp_path / "ManagerCareer20260101000000"
    save.write_bytes(b"\x00")
    runner = CliRunner()
    assert runner.invoke(cli.app, ["import", str(save), "--meta-xml", str(meta),
                                   "--profile", "ps"]).exit_code == 0

    hit = runner.invoke(cli.app, ["search", "--profile", "ps", "--playstyle", "Power Header"])
    assert hit.exit_code == 0, hit.output
    assert "Target Man" in hit.output and "Playmaker" not in hit.output

    # Survives the storage round trip, and the gold one is distinguishable.
    gold = runner.invoke(cli.app, ["search", "--profile", "ps", "--playstyle-plus", "Aerial"])
    assert "Target Man" in gold.output
    none = runner.invoke(cli.app, ["search", "--profile", "ps",
                                   "--playstyle-plus", "Power Header"])
    assert "Target Man" not in none.output

    # A typo would otherwise silently match nobody, which reads like "no player has it".
    typo = runner.invoke(cli.app, ["search", "--profile", "ps", "--playstyle", "Power Headr"])
    assert typo.exit_code != 0
    assert "isn't a PlayStyle" in typo.output


def test_playstyles_command_lists_them_all():
    from typer.testing import CliRunner

    from manager_mode_buddy import cli
    result = CliRunner().invoke(cli.app, ["playstyles"])
    assert result.exit_code == 0, result.output
    assert "34 PlayStyles" in result.output
    assert "Deflector" in result.output and "Power Header" in result.output
    assert "Leadership" in result.output   # legacy traits named as such


def test_player_detail_shows_playstyles_grouped(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from manager_mode_buddy import cli
    from manager_mode_buddy.extractor import ExtractionResult

    squad = [Player(id=1001, name="Target Man", position="ST", overall=80, potential=85,
                    playstyles=["Power Header", "Aerial"], playstyles_plus=["Aerial"],
                    traits=["Leadership"])]
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract",
                        lambda *a, **k: ExtractionResult(players=squad, team_ids=[]))
    meta = tmp_path / "fifa_ng_db-meta.xml"
    meta.write_text("<database></database>")
    save = tmp_path / "ManagerCareer20260101000000"
    save.write_bytes(b"\x00")
    runner = CliRunner()
    runner.invoke(cli.app, ["import", str(save), "--meta-xml", str(meta), "--profile", "ps2"])

    out = runner.invoke(cli.app, ["player", "1001", "--profile", "ps2"]).output
    assert "PLAYSTYLES" in out
    assert "Aerial +" in out          # the gold one is marked
    assert "Power Header" in out
    assert "Leadership" in out and "TRAITS" in out
