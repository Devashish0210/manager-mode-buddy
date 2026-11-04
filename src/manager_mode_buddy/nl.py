"""
Natural-language scouting queries (spec Section 29, "Natural language queries").

**Why this is not vector RAG.** The obvious reading of "do RAG on my save" is: embed a text
card per player, retrieve the nearest neighbours, hand them to the model. That is the wrong
tool for this data. A scouting request is a set of hard numeric constraints over ~22,000
structured rows -- "striker, 185cm+, potential 85 or better, not at a big club" -- and
embeddings cannot enforce a numeric bound. Nearest-neighbour search would return players who
*read* like the request while silently dropping ones who actually satisfy it, with no way to
tell which. Worse, it can only ever return the k rows it retrieved, so "how many strikers meet
this?" becomes unanswerable.

So the model is used for the part it is genuinely good at -- reading an English sentence and
deciding what it means -- and the deterministic filter engine does the part it is good at:

    English request -> [Claude, structured output] -> a validated filter spec
                    -> filters.apply() over every player -> exact, complete results

Everything the model produces is a *query*, never a result. It never sees player rows and
cannot invent a player, a rating, or a match count; every number in the output came out of the
save. The interpreted query is printed before the results so a wrong reading is obvious and
correctable, and any single filter can be overridden by dropping down to `mmb search`.

The model's other job here is domain translation: "target man" means height, strength,
heading, jumping, not a field the save has a column for. That mapping is genuinely a language
problem, and it is where an LLM earns its place in this tool.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import filters as F
from . import playstyles as PS
from .attributes import BY_FIELD, label_for
from .models import ParsedCareer, Player

# Two APIs, one job. Which one is used depends only on which key is present -- see
# `detect_provider`. The query they're asked for, and the validation their answer goes
# through, are identical.
PROVIDERS = ("anthropic", "google")
PROVIDER_LABELS = {"anthropic": "Anthropic", "google": "Google AI Studio"}
# Google retires model names on its own schedule (gemini-2.5-flash stopped accepting new users
# during development), so this default is a starting point, not a promise: `_call_google` falls
# back to the best model the key can actually use and says which one answered.
DEFAULT_MODELS = {"anthropic": "claude-opus-5", "google": "gemini-3.6-flash"}
KEY_ENVS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    # The Google SDK itself reads both of these; accept either so a key that already works
    # elsewhere works here.
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}
_PROVIDER_ALIASES = {
    "anthropic": "anthropic", "claude": "anthropic",
    "google": "google", "gemini": "google", "ai-studio": "google", "aistudio": "google",
}
MODEL_ENV = "MMB_MODEL"
PROVIDER_ENV = "MMB_PROVIDER"
# Kept for callers that predate multi-provider support.
API_KEY_ENV = "ANTHROPIC_API_KEY"

# Broad positions the user can ask for. Side-specific codes (RCM, LCB, ...) collapse into
# these in `filters._broad`, so this is the vocabulary worth exposing to the model.
POSITIONS = ["GK", "RB", "CB", "LB", "CDM", "CM", "CAM", "RM", "LM", "RW", "LW", "CF", "ST"]

SORT_KEYS = ["potential", "overall", "growth", "id"]


class NLError(RuntimeError):
    """Something went wrong translating a question into a query (no key, API failure, or a
    response that didn't survive validation)."""


@dataclass
class CareerFacts:
    """What this specific career can actually be asked about. Built from the imported data, not
    hard-coded, so the model is never told about a field this save didn't produce."""
    attributes: List[str]
    positions: List[str]
    playstyles: List[str]
    player_count: int
    has_names: bool
    has_clubs: bool
    overall_range: Optional[tuple]
    potential_range: Optional[tuple]
    height_range: Optional[tuple]
    age_range: Optional[tuple]
    as_of_date: Optional[str]

    @classmethod
    def from_career(cls, career: ParsedCareer, players: Sequence[Player]) -> "CareerFacts":
        attrs = set()
        positions = set()
        styles = set()
        overalls, potentials, heights, ages = [], [], [], []
        names = clubs = False
        for p in players:
            attrs.update(p.attributes)
            styles.update(p.playstyles)
            if p.position:
                positions.add(F._broad(p.position))
            if p.overall is not None:
                overalls.append(p.overall)
            if p.potential is not None:
                potentials.append(p.potential)
            if p.height_cm:
                heights.append(p.height_cm)
            if p.age is not None:
                ages.append(p.age)
            names = names or bool(p.name)
            clubs = clubs or bool(p.club_id)
        return cls(
            attributes=[f for f in BY_FIELD if f in attrs],
            positions=[p for p in POSITIONS if p in positions],
            playstyles=[s for s in PS.ALL_PLAYSTYLES if s in styles],
            player_count=len(players),
            has_names=names,
            has_clubs=clubs,
            overall_range=(min(overalls), max(overalls)) if overalls else None,
            potential_range=(min(potentials), max(potentials)) if potentials else None,
            height_range=(min(heights), max(heights)) if heights else None,
            age_range=(min(ages), max(ages)) if ages else None,
            as_of_date=career.as_of_date,
        )


@dataclass
class Interpretation:
    """A translated query: the filters to run, how to rank, and what the model could not
    express. `unsupported` is the honest half -- price, league and nationality names aren't in
    the parsed data, and a request for them has to be reported rather than quietly ignored."""
    filters: List[F.PlayerFilter] = field(default_factory=list)
    sort_by: str = "potential"
    ascending: bool = False
    limit: int = 25
    reading: str = ""
    unsupported: List[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None


# What MMB genuinely cannot filter on, with the reason. Sent to the model so it reports these
# back instead of silently approximating them, and printed to the user when it does.
KNOWN_GAPS = {
    "price": "the save stores no market value -- FC computes it at runtime",
    "value": "the save stores no market value -- FC computes it at runtime",
    "wage": "wages are in a contract table MMB does not parse yet",
    "league": "league membership is not parsed yet",
    "nationality": "nationality is a raw numeric id; country names decode but aren't stored yet",
    "club name": "clubs are raw numeric ids; team names decode but aren't stored yet",
    "contract": "contract length/expiry is not parsed yet",
    "transfer status": "transfer-listed / loan-listed status is not parsed yet",
}


def _nullable_array(items: dict, dialect: str) -> dict:
    if dialect == "anyof":
        return {"anyOf": [{"type": "array", "items": items}, {"type": "null"}]}
    return {"type": ["array", "null"], "items": items}


def _schema(facts: CareerFacts, dialect: str = "union") -> dict:
    """The structured-output schema. Every field is required and nullable rather than optional:
    strict JSON-schema output wants a closed shape, and a null is an unambiguous 'not asked
    for', where a missing key would be guesswork.

    `dialect` picks how "nullable" is spelled, which is the one place the two APIs differ:
    Anthropic takes a union type array, while Gemini's `response_json_schema` documents
    `anyOf` as supported and says nothing about type arrays. Same schema either way.
    """
    def nullable(kind: str) -> dict:
        if dialect == "anyof":
            return {"anyOf": [{"type": kind}, {"type": "null"}]}
        return {"type": [kind, "null"]}

    return {
        "type": "object",
        "properties": {
            "reading": {
                "type": "string",
                "description": "One sentence, addressed to the user, on how you read their "
                               "request. Mention any judgement call you made.",
            },
            "positions": _nullable_array(
                {"type": "string", "enum": facts.positions or POSITIONS}, dialect),
            "include_secondary_positions": nullable("boolean"),
            "playstyles": _nullable_array(
                {"type": "string", "enum": facts.playstyles or list(PS.ALL_PLAYSTYLES)}, dialect),
            "playstyles_plus": _nullable_array(
                {"type": "string", "enum": facts.playstyles or list(PS.ALL_PLAYSTYLES)}, dialect),
            "min_age": nullable("integer"),
            "max_age": nullable("integer"),
            "min_overall": nullable("integer"),
            "max_overall": nullable("integer"),
            "min_potential": nullable("integer"),
            "max_potential": nullable("integer"),
            "min_growth": nullable("integer"),
            "min_height_cm": nullable("integer"),
            "max_height_cm": nullable("integer"),
            "min_weight_kg": nullable("integer"),
            "max_weight_kg": nullable("integer"),
            "min_skill_moves": nullable("integer"),
            "min_weak_foot": nullable("integer"),
            "foot": (
                {"anyOf": [{"type": "string", "enum": ["Left", "Right"]}, {"type": "null"}]}
                if dialect == "anyof"
                else {"type": ["string", "null"], "enum": ["Left", "Right", None]}
            ),
            "free_agents_only": nullable("boolean"),
            "club_id": nullable("integer"),
            "name_contains": nullable("string"),
            "attribute_filters": _nullable_array({
                "type": "object",
                "properties": {
                    "attribute": {"type": "string", "enum": facts.attributes},
                    "minimum": nullable("integer"),
                    "maximum": nullable("integer"),
                },
                "required": ["attribute", "minimum", "maximum"],
                "additionalProperties": False,
            }, dialect),
            "sort_by": {"type": "string", "enum": SORT_KEYS + facts.attributes},
            "sort_ascending": {"type": "boolean"},
            "limit": {"type": "integer"},
            "unsupported": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Anything the user asked for that this schema cannot express, "
                               "in their own words. Empty if you captured everything.",
            },
        },
        "required": [
            "reading", "positions", "include_secondary_positions",
            "playstyles", "playstyles_plus", "min_age", "max_age",
            "min_overall", "max_overall",
            "min_potential", "max_potential", "min_growth", "min_height_cm", "max_height_cm",
            "min_weight_kg", "max_weight_kg", "min_skill_moves", "min_weak_foot", "foot",
            "free_agents_only", "club_id", "name_contains", "attribute_filters", "sort_by",
            "sort_ascending", "limit", "unsupported",
        ],
        "additionalProperties": False,
    }


def build_system_prompt(facts: CareerFacts) -> str:
    attribute_list = ", ".join(f"{label_for(f)} ({f})" for f in facts.attributes)
    gaps = "\n".join(f"- {name}: {reason}" for name, reason in KNOWN_GAPS.items())
    age_note = (
        f", as of {facts.as_of_date} (this career's own current date, so ages are the save's, "
        "not the real world's)" if facts.as_of_date else " (not available in this career)"
    )
    ranges = []
    if facts.overall_range:
        ranges.append(f"overall {facts.overall_range[0]}-{facts.overall_range[1]}")
    if facts.potential_range:
        ranges.append(f"potential {facts.potential_range[0]}-{facts.potential_range[1]}")
    if facts.height_range:
        ranges.append(f"height {facts.height_range[0]}-{facts.height_range[1]}cm")
    if facts.age_range:
        ranges.append(f"age {facts.age_range[0]}-{facts.age_range[1]}")

    return f"""You turn a football manager's scouting request into a database query over their
own EA Sports FC 25 career save. You never see or invent players: you only produce the query,
and a deterministic filter engine runs it over all {facts.player_count:,} scoutable players.

This career exposes:
- positions: {", ".join(facts.positions) or "none"}
- playstyles: {", ".join(facts.playstyles) or "none"}
- attributes: {attribute_list}
- height and weight in cm/kg, preferred foot, skill moves, weak foot, free-agent status
- age in completed years{age_note}
- player names: {"yes" if facts.has_names else "no (players are identified by id only)"}
- observed ranges: {"; ".join(ranges) or "unknown"}

It cannot filter on:
{gaps}
If the user asks for one of those, put it in `unsupported` in their own words. Never
substitute a different field for it and never pretend it was applied.

How to translate:
- Football language maps onto attributes. A "target man" is tall (min_height_cm around 187+)
  with strength, heading accuracy and jumping; a "poacher" wants finishing and positioning; a
  "deep-lying playmaker" wants vision, short/long passing and composure; a "ball-playing
  centre back" wants passing and composure alongside defending. Use the attributes this career
  actually has, and prefer two or three decisive ones over a long list that returns nothing.
- Thresholds: a good attribute for a first-team player is about 75+, very good 82+, elite 88+.
  Filters are ANDed, so every one you add shrinks the result set; keep them to what the user
  asked for plus what their described role genuinely requires.
- A player whose value for a filtered field is unknown is excluded, so do not add filters
  casually.
- "wonderkid" or "prospect" means young (max_age around 21), high potential, and meaningful
  growth over current overall -- not necessarily a high overall today. "Young" on its own is
  about 23 and under; "experienced" or "proven" is roughly 27+.
- Sort by whatever the request is really about: potential for prospects, overall for
  ready-now signings, growth for development projects, or a specific attribute when the user
  is hunting one quality.
- Default the limit to 25 unless the user asks for more or fewer.
- `reading` is one plain sentence to the user about how you read the request, naming any
  judgement call (e.g. "read 'target man' as 187cm+ with strength and heading 75+").

Be literal about explicit numbers the user gives, and conservative about ones they don't."""


def _model_for(provider: str, model: Optional[str]) -> str:
    return model or os.environ.get(MODEL_ENV) or DEFAULT_MODELS[provider]


ALL_KEY_ENVS = tuple(name for names in KEY_ENVS.values() for name in names)


def _keys_set_but_not_here() -> List[str]:
    """Key variables that exist in the user's persistent Windows environment but not in this
    process. `setx GEMINI_API_KEY ...` reports SUCCESS and then does nothing for the terminal
    it was typed in -- it only affects new ones. That produces a "no API key found" error
    seconds after the user watched a key get saved, so it's worth diagnosing precisely instead
    of repeating the generic advice. Values are never read out, only the names."""
    missing = [name for name in ALL_KEY_ENVS if not os.environ.get(name)]
    if not missing or os.name != "nt":
        return []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env:
            found = []
            for name in missing:
                try:
                    value, _ = winreg.QueryValueEx(env, name)
                except OSError:
                    continue
                if value:
                    found.append(name)
            return found
    except Exception:  # registry access is a nicety, never a requirement
        return []


def _no_key_anywhere() -> bool:
    return not any(os.environ.get(name) for name in ALL_KEY_ENVS)


def _no_key_message(provider: Optional[str]) -> str:
    """`provider` is the one the user actually asked for, or None when MMB was left to choose --
    in which case naming a single provider's variable would be misleading, since any of them
    would have worked."""
    if provider:
        wanted = " or ".join(KEY_ENVS[provider])
        lines = [f"no API key found for {PROVIDER_LABELS[provider]}. Set {wanted}, or pass "
                 "--api-key."]
    else:
        lines = ["no API key found. Set whichever you have:"]
        for name in ALL_KEY_ENVS:
            label = next(PROVIDER_LABELS[p] for p, names in KEY_ENVS.items() if name in names)
            lines.append(f'  $env:{name} = "..."   ({label})')

    stale = _keys_set_but_not_here()
    if stale:
        lines.append("")
        lines.append(f"{', '.join(stale)} is set in your Windows user environment but not in "
                     "this terminal:")
        lines.append("`setx` only applies to terminals opened after it runs. Either open a new "
                     "one, or set it here with $env:NAME = \"...\".")

    lines.append("")
    lines.append("`mmb search` needs no key and does the same filtering by hand.")
    return "\n".join(lines)


def _key_for(provider: str, api_key: Optional[str],
             requested_provider: Optional[str] = None) -> str:
    if api_key:
        return api_key
    for name in KEY_ENVS[provider]:
        value = os.environ.get(name)
        if value:
            return value
    # `requested_provider` is None when MMB chose the provider itself; the message then lists
    # every key it accepts instead of blaming the one the default landed on.
    raise NLError(_no_key_message(requested_provider))


def detect_provider(explicit: Optional[str] = None) -> str:
    """Which API to translate with: an explicit choice, then MMB_PROVIDER, then whichever
    key is actually present in the environment. Auto-detection means someone with only a
    Google AI Studio key never has to know MMB also speaks Anthropic, or vice versa."""
    chosen = (explicit or os.environ.get(PROVIDER_ENV) or "").strip().lower()
    if chosen:
        if chosen in _PROVIDER_ALIASES:
            return _PROVIDER_ALIASES[chosen]
        raise NLError(
            f"unknown provider {chosen!r}. Use one of: {', '.join(sorted(set(_PROVIDER_ALIASES)))}."
        )
    for provider in PROVIDERS:
        if any(os.environ.get(name) for name in KEY_ENVS[provider]):
            return provider
    return "anthropic"


def _call_anthropic(question: str, facts: CareerFacts, model: str, api_key: str,
                    model_is_default: bool = True) -> Tuple[str, str]:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on the local install
        raise NLError(
            "the `anthropic` package is required to use an Anthropic key. Install it with:\n"
            "    pip install anthropic"
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            system=build_system_prompt(facts),
            messages=[{"role": "user", "content": question}],
            # Anthropic's strict JSON-schema output. The union form ("type": ["integer",
            # "null"]) is what its own schema validation expects.
            output_config={"format": {"type": "json_schema",
                                      "schema": _schema(facts, dialect="union")}},
        )
    except Exception as exc:  # the SDK raises a family of errors; all are fatal here
        raise NLError(f"the Anthropic call failed: {exc}") from exc

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise NLError("the model returned no query to run.")
    return text, model


def _google_client(api_key: str):
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover - depends on the local install
        raise NLError(
            "the `google-genai` package is required to use a Google AI Studio key. Install "
            "it with:\n    pip install google-genai"
        ) from exc
    # The SDK logs an automatic-function-calling advisory on every generate_content call. MMB
    # defines no tools, so it never applies -- and it lands in the middle of the user's results.
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)
    return genai.Client(api_key=api_key)


# Models that can't do what `mmb ask` needs (text in, JSON out), by name. Filtering by name is
# crude, but the alternative -- trusting `supported_actions` -- reports generateContent for
# image and speech models too.
_GOOGLE_NON_TEXT = ("tts", "image", "embedding", "computer-use", "vision", "audio",
                    "deep-research", "antigravity")


def google_models(api_key: str) -> List[str]:
    """Every text model this key can call generateContent on, best first (see _google_rank)."""
    client = _google_client(api_key)
    names = []
    for entry in client.models.list():
        name = (getattr(entry, "name", "") or "").replace("models/", "")
        actions = getattr(entry, "supported_actions", None) or []
        if not name or (actions and "generateContent" not in actions):
            continue
        if any(bad in name.lower() for bad in _GOOGLE_NON_TEXT):
            continue
        names.append(name)
    return sorted(set(names), key=_google_rank)


def _google_version(name: str) -> float:
    """The generation number in a model name: gemini-3.6-flash -> 3.6, gemini-3-flash -> 3.0."""
    match = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    if not match:
        return 0.0
    return float(f"{match.group(1)}.{match.group(2) or 0}")


def _google_rank(name: str):
    """Order models by 'cheapest that is still good enough for this job'.

    Translating one sentence into a filter spec is a small, well-specified task, so the flash
    tier is the right default -- pro costs more for judgement this task doesn't need. Full
    flash is preferred over flash-lite because the one genuinely hard part is football
    vocabulary ("target man" -> height, strength, heading), and that is where a smaller model
    starts guessing. Newer generations first within a tier, and previews last, since a preview
    name can disappear without notice.
    """
    lowered = name.lower()
    if "flash-lite" in lowered:
        tier = 1
    elif "flash" in lowered:
        tier = 0
    elif "pro" in lowered:
        tier = 2
    else:
        tier = 3
    return (tier, "preview" in lowered, -_google_version(name), name)


def _is_missing_model_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in
               ("not found", "404", "not supported", "no longer available"))


def _google_model_hint(api_key: str, model: str) -> str:
    """Model names come and go faster than this code is updated, so when one is rejected, say
    which ones the key can actually use rather than leaving the user to guess. Deliberately
    unabridged: an earlier version cut the list at 12 alphabetically and hid the very model
    the API was recommending."""
    try:
        names = google_models(api_key)
    except Exception:
        return ""
    if not names:
        return ""
    return (f"\n\n{model!r} isn't available to this key. Models that are, best first:\n  "
            + "\n  ".join(names)
            + "\n\nPick one with --model, or set MMB_MODEL.")


def _generate_google(client, model: str, question: str, facts: CareerFacts):
    from google.genai import types

    return client.models.generate_content(
        model=model,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=build_system_prompt(facts),
            response_mime_type="application/json",
            # `response_json_schema` takes standard JSON Schema (unlike the older
            # `response_schema`, which is Gemini's own subset). It documents `anyOf` as
            # supported but says nothing about union type arrays, so nullable fields are
            # emitted as anyOf here -- see `_schema`.
            response_json_schema=_schema(facts, dialect="anyof"),
        ),
    )


def _call_google(question: str, facts: CareerFacts, model: str, api_key: str,
                 model_is_default: bool = True) -> Tuple[str, str]:
    """Returns (raw JSON text, the model that actually answered).

    Google retires model names on its own schedule -- `gemini-2.5-flash` stopped accepting new
    users while this was being written, and the pinned default broke. So when a *default* model
    is rejected, MMB asks the key which models it can use and retries once with the best of
    them, reporting which it used. A model the user named explicitly is never silently swapped.
    """
    client = _google_client(api_key)
    try:
        return getattr(_generate_google(client, model, question, facts), "text", "") or "", model
    except Exception as exc:
        if not (model_is_default and _is_missing_model_error(exc)):
            raise NLError(f"the Google call failed: {exc}{_google_model_hint(api_key, model)}") from exc

    # `models.list()` can name models the key still cannot call (gemini-2.5-flash is listed
    # for this account but 404s as "no longer available to new users"), so walk the ranked
    # list rather than trusting its first entry.
    try:
        candidates = [name for name in google_models(api_key) if name != model][:3]
    except Exception:
        candidates = []
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            answer = _generate_google(client, candidate, question, facts)
            return getattr(answer, "text", "") or "", candidate
        except Exception as retry_exc:
            last_error = retry_exc
            if not _is_missing_model_error(retry_exc):
                break

    detail = f": {last_error}" if last_error else ""
    raise NLError(f"the Google call failed. {model!r} is not available to this key, and "
                  f"falling back did not work either{detail}"
                  f"{_google_model_hint(api_key, model)}")


def anthropic_models(api_key: str) -> List[str]:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on the local install
        raise NLError("the `anthropic` package is required. Install it with:\n"
                      "    pip install anthropic") from exc
    client = anthropic.Anthropic(api_key=api_key)
    return [m.id for m in client.models.list()]


def list_models(provider: Optional[str] = None, api_key: Optional[str] = None) -> Tuple[str, List[str]]:
    """(provider, models it can call) -- so a user can see what their own key offers instead of
    guessing at names that change every few months."""
    chosen = detect_provider(provider)
    key = _key_for(chosen, api_key, requested_provider=chosen if provider else None)
    try:
        names = google_models(key) if chosen == "google" else anthropic_models(key)
    except NLError:
        raise
    except Exception as exc:
        raise NLError(f"could not list models: {exc}") from exc
    return chosen, names


_CALLERS = {"anthropic": _call_anthropic, "google": _call_google}


def interpret(question: str, facts: CareerFacts, model: Optional[str] = None,
              api_key: Optional[str] = None, provider: Optional[str] = None) -> Interpretation:
    """Turn a question into a validated filter spec using whichever API a key is present for.

    The provider only changes how the request is addressed: both are asked for the same
    structured query, and both answers go through the same `to_interpretation` validation, so
    neither can produce a filter MMB doesn't recognise.
    """
    chosen_provider = detect_provider(provider)
    chosen_model = _model_for(chosen_provider, model)
    # If nothing was chosen and nothing is set, the error should list every key MMB accepts,
    # not just the one the default happened to land on.
    was_asked_for = bool(provider or os.environ.get(PROVIDER_ENV))
    key = _key_for(chosen_provider, api_key,
                   requested_provider=chosen_provider if was_asked_for else None)

    model_is_default = not (model or os.environ.get(MODEL_ENV))
    text, answered_by = _CALLERS[chosen_provider](question, facts, chosen_model, key,
                                                  model_is_default)
    if not text:
        raise NLError("the model returned no query to run.")
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NLError(f"the model returned something that isn't a query: {exc}") from exc

    interpreted = to_interpretation(spec, facts)
    interpreted.provider = chosen_provider
    interpreted.model = answered_by
    return interpreted


def to_interpretation(spec: dict, facts: CareerFacts) -> Interpretation:
    """Convert a raw spec into filter objects.

    Everything here re-validates: the schema constrains the model, but a filter is only built
    from a value this code recognises, so a malformed or hallucinated field is dropped rather
    than trusted. Kept separate from `interpret` so it can be tested without an API call.
    """
    built: List[F.PlayerFilter] = []

    positions = [p for p in (spec.get("positions") or []) if isinstance(p, str)]
    if positions:
        built.append(F.PositionFilter(
            tuple(positions),
            include_secondary=bool(spec.get("include_secondary_positions")),
        ))

    for key, plus in (("playstyles", False), ("playstyles_plus", True)):
        asked = [s for s in (spec.get(key) or [])
                  if isinstance(s, str) and PS.resolve(s) in facts.playstyles]
        if asked:
            built.append(F.PlayStyleFilter(tuple(PS.resolve(s) for s in asked), plus_only=plus))

    def as_int(key: str) -> Optional[int]:
        value = spec.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    ranges = [
        (F.AgeFilter, "min_age", "max_age"),
        (F.OverallFilter, "min_overall", "max_overall"),
        (F.PotentialFilter, "min_potential", "max_potential"),
        (F.HeightFilter, "min_height_cm", "max_height_cm"),
        (F.WeightFilter, "min_weight_kg", "max_weight_kg"),
        (F.SkillMovesFilter, "min_skill_moves", None),
        (F.WeakFootFilter, "min_weak_foot", None),
    ]
    for filter_cls, min_key, max_key in ranges:
        low = as_int(min_key)
        high = as_int(max_key) if max_key else None
        if low is not None or high is not None:
            built.append(filter_cls(minimum=low, maximum=high))

    growth = as_int("min_growth")
    if growth is not None:
        built.append(F.GrowthFilter(minimum=growth))

    for raw in spec.get("attribute_filters") or []:
        if not isinstance(raw, dict):
            continue
        attribute = raw.get("attribute")
        if attribute not in facts.attributes:
            continue  # never filter on a field this career doesn't have
        low, high = raw.get("minimum"), raw.get("maximum")
        low = low if isinstance(low, int) else None
        high = high if isinstance(high, int) else None
        if low is None and high is None:
            continue
        built.append(F.AttributeFilter(attribute=attribute, minimum=low, maximum=high))

    foot = spec.get("foot")
    if foot in ("Left", "Right"):
        built.append(F.FootFilter(foot))
    if spec.get("free_agents_only"):
        built.append(F.FreeAgentFilter())
    club_id = as_int("club_id")
    if club_id is not None:
        built.append(F.ClubFilter(club_id))
    name = spec.get("name_contains")
    if isinstance(name, str) and name.strip():
        built.append(F.NameFilter(name.strip()))

    sort_by = spec.get("sort_by")
    if sort_by not in SORT_KEYS and sort_by not in facts.attributes:
        sort_by = "potential"
    limit = as_int("limit")

    unsupported = [u for u in (spec.get("unsupported") or []) if isinstance(u, str)]

    return Interpretation(
        filters=built,
        sort_by=sort_by,
        ascending=bool(spec.get("sort_ascending")),
        limit=limit if limit and limit > 0 else 25,
        reading=str(spec.get("reading") or "").strip(),
        unsupported=unsupported,
    )


def explain_gap(text: str) -> Optional[str]:
    """Attach MMB's own reason to something the model reported as unsupported, when it matches
    a known gap -- so the user learns why it can't be filtered, not just that it wasn't."""
    lowered = text.lower()
    for name, reason in KNOWN_GAPS.items():
        if name in lowered:
            return reason
    return None
