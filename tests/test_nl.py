"""Tests for the natural-language layer.

None of these call the API. The translation boundary is deliberately split so the part that
matters for correctness -- turning a model-written spec into real filters -- is pure and
testable: `nl.interpret` does the network call and hands `nl.to_interpretation` a dict. What
these tests protect is that a spec is *validated*, not trusted: the model can only ever produce
a query, and a query MMB doesn't recognise is dropped rather than acted on.
"""
import pytest

from manager_mode_buddy import filters as F, nl
from manager_mode_buddy.models import ParsedCareer, Player

SQUAD = [
    Player(id=1001, name="A. Target", position="ST", overall=76, potential=86, height_cm=191,
           weight_kg=88, preferred_foot="Right", club_id=9,
           attributes={"strength": 84, "headingaccuracy": 82, "finishing": 74, "vision": 55}),
    Player(id=1002, name="B. Small", position="ST", overall=74, potential=84, height_cm=172,
           weight_kg=68, preferred_foot="Left", club_id=9,
           attributes={"strength": 60, "headingaccuracy": 55, "finishing": 80, "vision": 70}),
]


def facts() -> nl.CareerFacts:
    career = ParsedCareer(save_path="x", players=SQUAD, team_ids=[9])
    return nl.CareerFacts.from_career(career, SQUAD)


def test_career_facts_describe_only_what_the_save_produced():
    f = facts()
    assert set(f.attributes) == {"strength", "headingaccuracy", "finishing", "vision"}
    assert f.positions == ["ST"]
    assert f.has_names and f.has_clubs
    assert f.overall_range == (74, 76)
    assert f.height_range == (172, 191)


def test_schema_offers_only_this_careers_attributes_and_positions():
    schema = nl._schema(facts())
    attr_enum = schema["properties"]["attribute_filters"]["items"]["properties"]["attribute"]["enum"]
    assert set(attr_enum) == {"strength", "headingaccuracy", "finishing", "vision"}
    assert schema["properties"]["positions"]["items"]["enum"] == ["ST"]
    assert schema["additionalProperties"] is False


def test_system_prompt_states_what_cannot_be_filtered():
    prompt = nl.build_system_prompt(facts())
    assert "market value" in prompt
    assert "2 scoutable players" in prompt.replace(",", "")


EMPTY_SPEC = {
    "reading": "", "positions": None, "include_secondary_positions": None,
    "min_overall": None, "max_overall": None, "min_potential": None, "max_potential": None,
    "min_growth": None, "min_height_cm": None, "max_height_cm": None, "min_weight_kg": None,
    "max_weight_kg": None, "min_skill_moves": None, "min_weak_foot": None, "foot": None,
    "free_agents_only": None, "club_id": None, "name_contains": None,
    "attribute_filters": None, "sort_by": "potential", "sort_ascending": False,
    "limit": 25, "unsupported": [],
}


def spec(**overrides) -> dict:
    merged = dict(EMPTY_SPEC)
    merged.update(overrides)
    return merged


def test_a_target_man_query_becomes_real_filters():
    interpreted = nl.to_interpretation(spec(
        reading="Read 'target man' as a tall, strong striker.",
        positions=["ST"], min_height_cm=187, min_potential=85,
        attribute_filters=[
            {"attribute": "strength", "minimum": 75, "maximum": None},
            {"attribute": "headingaccuracy", "minimum": 75, "maximum": None},
        ],
        sort_by="potential",
    ), facts())

    assert interpreted.reading.startswith("Read 'target man'")
    kinds = [type(f) for f in interpreted.filters]
    assert F.PositionFilter in kinds and F.HeightFilter in kinds and F.PotentialFilter in kinds
    assert sum(1 for f in interpreted.filters if isinstance(f, F.AttributeFilter)) == 2

    matched = F.apply(SQUAD, interpreted.filters)
    assert [p.id for p in matched] == [1001]


def test_an_empty_spec_produces_no_filters():
    interpreted = nl.to_interpretation(spec(), facts())
    assert interpreted.filters == []
    assert F.apply(SQUAD, interpreted.filters) == SQUAD


def test_an_attribute_this_career_lacks_is_dropped_not_trusted():
    """The schema constrains the model, but the spec is still re-validated here: a field this
    save never produced must not become a filter that silently excludes everyone."""
    interpreted = nl.to_interpretation(spec(
        attribute_filters=[{"attribute": "gkreflexes", "minimum": 80, "maximum": None}],
    ), facts())
    assert interpreted.filters == []


def test_malformed_spec_entries_are_ignored():
    interpreted = nl.to_interpretation(spec(
        positions=[None, 5],
        min_overall="seventy",
        foot="either",
        attribute_filters=["strength>=80", {"attribute": "strength"}],
        name_contains="   ",
        club_id=True,          # a bool is not a club id
    ), facts())
    assert interpreted.filters == []


def test_unknown_sort_key_falls_back_to_potential():
    assert nl.to_interpretation(spec(sort_by="charisma"), facts()).sort_by == "potential"
    assert nl.to_interpretation(spec(sort_by="strength"), facts()).sort_by == "strength"


def test_limit_is_sanitised():
    assert nl.to_interpretation(spec(limit=0), facts()).limit == 25
    assert nl.to_interpretation(spec(limit=-5), facts()).limit == 25
    assert nl.to_interpretation(spec(limit=10), facts()).limit == 10


def test_unsupported_requests_are_carried_through_with_a_reason():
    interpreted = nl.to_interpretation(
        spec(unsupported=["under a price of 10m", "in the Premier League"]), facts())
    assert len(interpreted.unsupported) == 2
    assert "market value" in (nl.explain_gap("under a price of 10m") or "")
    assert "league" in (nl.explain_gap("in the Premier League") or "")
    assert nl.explain_gap("something else entirely") is None


def test_age_is_supported_not_a_gap():
    """Age used to be MMB's headline gap; the birth-date decode closed it, so the model must
    no longer be told to report it as unsupported."""
    assert not any("age" == key for key in nl.KNOWN_GAPS)
    interpreted = nl.to_interpretation(spec(min_age=18, max_age=23), facts())
    assert [f.describe() for f in interpreted.filters] == ["age 18-23"]


def test_free_agent_foot_and_name_filters_survive_translation():
    interpreted = nl.to_interpretation(spec(
        foot="Left", free_agents_only=True, name_contains="Small",
    ), facts())
    kinds = [type(f) for f in interpreted.filters]
    assert kinds == [F.FootFilter, F.FreeAgentFilter, F.NameFilter]


def _clear_keys(monkeypatch):
    for names in nl.KEY_ENVS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(nl.PROVIDER_ENV, raising=False)


def test_interpret_without_an_api_key_explains_itself(monkeypatch):
    """Clears every provider's key, not just Anthropic's: MMB speaks to more than one, so
    leaving GEMINI_API_KEY set (as a real user's shell often does) meant this test quietly
    passed by finding a key rather than by hitting the no-key path."""
    _clear_keys(monkeypatch)
    with pytest.raises(nl.NLError, match="no API key"):
        nl.interpret("tall striker", facts())


# --- provider selection ---------------------------------------------------------------------


def test_provider_follows_whichever_key_is_present(monkeypatch):
    """Someone with only a Google AI Studio key should never have to know MMB also speaks
    Anthropic."""
    _clear_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert nl.detect_provider() == "google"

    _clear_keys(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    assert nl.detect_provider() == "google"

    _clear_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert nl.detect_provider() == "anthropic"


def test_explicit_provider_and_env_override_detection(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert nl.detect_provider("gemini") == "google"
    assert nl.detect_provider("Claude") == "anthropic"

    monkeypatch.setenv(nl.PROVIDER_ENV, "google")
    assert nl.detect_provider() == "google"


def test_unknown_provider_is_rejected(monkeypatch):
    _clear_keys(monkeypatch)
    with pytest.raises(nl.NLError, match="unknown provider"):
        nl.detect_provider("openai")


def test_missing_key_names_the_right_environment_variables(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setattr(nl, "_keys_set_but_not_here", lambda: [])
    with pytest.raises(nl.NLError, match="GEMINI_API_KEY or GOOGLE_API_KEY"):
        nl._key_for("google", None, requested_provider="google")
    with pytest.raises(nl.NLError, match="ANTHROPIC_API_KEY"):
        nl._key_for("anthropic", None, requested_provider="anthropic")


def test_each_provider_gets_the_nullable_dialect_it_documents():
    """The two APIs agree on JSON Schema except in how a nullable field is spelled: Anthropic
    takes a union type array, Gemini documents anyOf and says nothing about type arrays."""
    union = nl._schema(facts(), dialect="union")
    anyof = nl._schema(facts(), dialect="anyof")

    assert union["properties"]["min_age"] == {"type": ["integer", "null"]}
    assert anyof["properties"]["min_age"] == {
        "anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert anyof["properties"]["foot"]["anyOf"][0]["enum"] == ["Left", "Right"]
    # Everything else is identical, including which fields are required.
    assert union["required"] == anyof["required"]


def test_model_default_depends_on_the_provider(monkeypatch):
    monkeypatch.delenv(nl.MODEL_ENV, raising=False)
    assert nl._model_for("google", None).startswith("gemini")
    assert nl._model_for("anthropic", None).startswith("claude")
    assert nl._model_for("google", "gemini-9") == "gemini-9"
    monkeypatch.setenv(nl.MODEL_ENV, "from-env")
    assert nl._model_for("anthropic", None) == "from-env"


def test_missing_key_with_no_provider_chosen_lists_every_option(monkeypatch):
    """MMB picks the provider when the user hasn't, so a missing key must not name only the
    one the default landed on -- the user may hold a key for the other."""
    _clear_keys(monkeypatch)
    monkeypatch.setattr(nl, "_keys_set_but_not_here", lambda: [])

    with pytest.raises(nl.NLError) as err:
        nl._key_for("anthropic", None, requested_provider=None)

    message = str(err.value)
    assert "ANTHROPIC_API_KEY" in message and "GEMINI_API_KEY" in message
    assert "Google AI Studio" in message


def test_a_key_saved_by_setx_but_absent_here_is_diagnosed(monkeypatch):
    """`setx` prints SUCCESS and does nothing for the terminal it was typed in. Saying so beats
    repeating "no API key found" at someone who just watched their key get saved."""
    _clear_keys(monkeypatch)
    monkeypatch.setattr(nl, "_keys_set_but_not_here", lambda: ["GEMINI_API_KEY"])

    with pytest.raises(nl.NLError, match="setx"):
        nl._key_for("google", None, requested_provider="google")


def test_an_explicitly_requested_provider_still_gets_a_specific_message(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setattr(nl, "_keys_set_but_not_here", lambda: [])

    with pytest.raises(nl.NLError) as err:
        nl._key_for("google", None, requested_provider="google")

    assert "Google AI Studio" in str(err.value)
    assert "ANTHROPIC_API_KEY" not in str(err.value)


# --- Google model churn -----------------------------------------------------------------

def test_google_models_rank_cheapest_useful_first():
    """Translating one sentence into a filter spec doesn't need `pro`, and flash-lite starts
    guessing at football vocabulary -- so full flash wins, newest generation first, with
    previews last because a preview name can vanish."""
    available = ["gemini-2.5-pro", "gemini-2.5-flash-lite", "gemini-3-flash-preview",
                 "gemini-3.6-flash", "gemini-2.5-flash", "gemini-3.6-pro"]
    assert sorted(available, key=nl._google_rank)[0] == "gemini-3.6-flash"
    assert sorted(available, key=nl._google_rank)[-1] == "gemini-2.5-pro"


def test_google_version_is_read_from_the_model_name():
    assert nl._google_version("gemini-3.6-flash") == 3.6
    assert nl._google_version("gemini-3-flash-preview") == 3.0
    assert nl._google_version("something-else") == 0.0


def test_retired_model_errors_are_recognised():
    """Google's wording for a retired model varies; all of these mean 'pick another one'."""
    assert nl._is_missing_model_error(Exception("404 NOT_FOUND"))
    assert nl._is_missing_model_error(Exception("is no longer available to new users"))
    assert nl._is_missing_model_error(Exception("model not supported"))
    assert not nl._is_missing_model_error(Exception("429 RESOURCE_EXHAUSTED"))
