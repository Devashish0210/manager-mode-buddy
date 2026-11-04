"""
Integration test against a real FC25 ManagerCareer save.

This deliberately does not ship a save file or EA's meta.xml in the repo (see HANDOFF.md's
licensing note -- meta.xml is EA's own data, redistributed by third parties, not something to
commit). Point the environment variables below at your own local copies to run this test;
otherwise it's skipped.
"""
import os

import pytest

from manager_mode_buddy.extractor import extract

SAVE_PATH = os.environ.get("MMB_TEST_SAVE")
META_XML_PATH = os.environ.get("MMB_TEST_META_XML")

pytestmark = pytest.mark.skipif(
    not (SAVE_PATH and META_XML_PATH),
    reason="set MMB_TEST_SAVE and MMB_TEST_META_XML to a real save + fifa_ng_db-meta.xml to run",
)


@pytest.fixture(scope="module")
def result():
    # Parsing a real save scans multiple multi-MB blobs for several tables' worth of
    # candidate schemas -- module-scoped so the ~5-10 minute extraction only runs once for
    # every test in this file, not once per test.
    return extract(SAVE_PATH, META_XML_PATH)


def test_extract_produces_players_with_validated_attributes(result):
    assert len(result.players) > 1000

    with_overall = [p for p in result.players if p.overall is not None]
    assert len(with_overall) > 1000

    # career_playergrowthuserseason-sourced players should have a full attribute set
    from_growth = [p for p in with_overall if p.overall_source == "career_playergrowthuserseason"]
    assert from_growth
    assert len(from_growth[0].attributes) >= 30


def test_extract_finds_the_known_free_agent_pseudo_club(result):
    assert any(p.is_free_agent for p in result.players)


def test_extract_reports_teams(result):
    assert len(result.team_ids) > 0


def test_no_player_has_potential_below_overall(result):
    """Regression test for a real bug found in Milestone 1: FC25-family games reserve very
    low playerids for template/placeholder records (not real players), and the parser's
    multi-candidate merging occasionally produces a spurious row too -- both are cheaply
    caught by a genuine game invariant (potential is never below current overall) that
    extractor.py now filters on. A specific case found this session: playerid=1 decoded with
    overall=88 but potential=1."""
    violations = [p for p in result.players if p.overall is not None and p.potential is not None
                  and p.potential < p.overall]
    assert violations == []
    assert not any(p.id == 1 for p in result.players)


def test_no_player_has_an_overall_or_potential_outside_1_99(result):
    """Regression test for a third real case found in Milestone 1: playerid=1536 decoded with
    overall=100 and potential=121 in both the growth table and the identity pool -- both
    impossible, since overall/potential are hard-capped at 99 in every FC-family table.
    extractor.py's `_valid_rating` now treats an out-of-range decode as unavailable rather
    than a real (fabricated-looking) rating."""
    for p in result.players:
        if p.overall is not None:
            assert 1 <= p.overall <= 99, p
        if p.potential is not None:
            assert 1 <= p.potential <= 99, p
