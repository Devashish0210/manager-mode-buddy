# Manager Mode Buddy (MMB)

An open-source, local-first CLI scouting companion for EA Sports FC 25 Manager Career Mode.

MMB reads your FC25 Manager Career save and lets you search, filter, inspect, and rank players
from **your actual save** - no uploading save files to a website, no relying on a
generic/default game database when your save has career-specific data.

## Status

Working end to end: pick a save, import it, filter it.

- `mmb saves` finds your Manager Career save files.
- `mmb import` parses one (interactively, or by name/path) into a local SQLite career profile
  under `~/.manager-buddy/saves/<profile>/`.
- `mmb search` filters that career by position, age, overall, potential, growth, height,
  weight and any parsed attribute, and prints a ranked results table.
- `mmb ask "..."` does the same thing from a plain-English description, using Claude to write
  the filters (optional; needs an API key).
- `mmb player <id>` shows one player's full detail.

Not built yet: roles, value/wage, the fit score, and the full-screen interactive UI. See [HANDOFF.md](HANDOFF.md) for the complete
status, what's confirmed vs. best-effort in the parsed data, and what's next.

## Installing (development)

```bash
pip install -e ".[dev]"
```

## Usage

Just run it:

```bash
mmb
```

That walks you through it: pick which save to search - listed by the name you gave it in game
("Signed Hartmann"), not the timestamped file name the game generates - confirm the current
date in your career, then describe the player you want. Every step below is also a direct
command, for scripting or when you know exactly what you want.

The picker is ordered newest save first and shows when your game last wrote each file. FC keeps
one file per career and **replaces it every time you save** - a new timestamped file appears
and the old one is deleted - so a career MMB imported last week no longer has a file on disk.
That career is marked `OUTDATED` when you've saved it again since; pick the newer file and MMB
re-reads it into the same profile, bringing it up to date.

```bash
mmb --help
```

### 1. Find your save

```bash
mmb saves
```

This scans the game's own save folders for `ManagerCareer*` files and lists them newest first:

- `%LOCALAPPDATA%\EA SPORTS FC 25\settings` - where FC 25 writes.
- `Documents\FC 24\settings` - the older FIFA/FC layout, including OneDrive-redirected
  Documents folders.

If your saves live somewhere else, pass `--dir <folder>` (repeatable), set `MMB_SAVE_DIRS`
(`os.pathsep`-separated), or just hand `mmb import` the full path.

### 2. Import it

Parsing needs one file MMB does not ship: `fifa_ng_db-meta.xml`, EA's own schema description
for its save/database format. It's redistributed by third parties in the FC modding community
(not by EA, and not by this repo - see the licensing note in HANDOFF.md); find your own copy
and point MMB at it with `--meta-xml`, or set `MMB_META_XML` once.

Player names come from the game's own name tables, which live in `fifa_ng_db.db` - the file
that ships beside `fifa_ng_db-meta.xml`. MMB uses the sibling of whatever you pass to
`--meta-xml` automatically, so if you have both files in one folder there is nothing to
configure; `--game-db` / `MMB_GAME_DB` points elsewhere. This covers everyone, including the
youth-intake and regen players your career invented.

If you only have the meta.xml, names fall back to an optional CSV with `player_id` and
`full_name` columns (e.g. a sofifa export) via `--names-csv` or `MMB_NAMES_CSV`; that covers
real players only. With neither, players display as `Player <id>` and you look them up by ID.
Names are display-only either way - filtering is always on save data.

```bash
mmb import                                  # pick from the detected saves
mmb import "Signed Hartmann"                # or name one from `mmb saves`
mmb import ManagerCareer20260908152939088   # the generated file name works too
mmb import "C:\path\to\ManagerCareer..." --profile arsenal_2027
```

Importing takes about ten seconds. Everything afterwards reads the imported SQLite copy and is
instant - the original save file is never touched again.

Without `--profile`, the profile name is a slugified version of your in-game save name
(`Signed Hartmann` -> `signed_hartmann`), falling back to the generated file name for a save
that doesn't have one.
Re-importing the same profile name replaces its stored data. The freshly imported career
becomes the active one; switch between careers with:

```bash
mmb careers          # list imported careers (* marks the active one)
mmb use arsenal_2027 # or `mmb use` to pick from a list
```

Every command below accepts `--profile <name>` to override the active career for one run.

### 2b. Tell it what day it is

Ages are counted from your career's current date. MMB estimates that date from your transfer
history, but you can read it straight off your game, which is exact:

```bash
mmb date                # what MMB currently thinks
mmb date 2027-08-21     # set it; every age is recomputed instantly, no re-parse
```

### 3. Search it

```bash
mmb search --pos ST --min-pot 85 --max-ovr 75
mmb search --pos CAM --min-ovr 70 --attr "vision>=80" --attr "Short Passing >= 78"
mmb search --free-agents --min-growth 10 --sort growth
```

Filters, all combined with AND:

| Flag | What it does |
| --- | --- |
| `--pos` | Primary position, repeatable (`--pos CM --pos CAM`). Side-specific codes collapse, so `CM` matches `RCM`. Add `--include-secondary` to match secondary positions too - a wider net, but they decode less reliably than the primary. |
| `--min-age` / `--max-age` | Age range, in the career's own season (see below). |
| `--min-ovr` / `--max-ovr` | Current overall range. |
| `--min-pot` / `--max-pot` | Potential range, from the career save rather than a generic database. |
| `--min-growth` | Minimum potential minus current overall. |
| `--attr` | Any parsed attribute, repeatable: `"vision>=80"`, `"Short Passing >= 78"`, `"finishing=75-85"`, `"dribbling<90"`. Raw field name or friendly label, any casing. |
| `--foot` | `Left` or `Right`. |
| `--min-height` / `--max-height` | Height in cm - the target-man filter. Also `--min-weight` / `--max-weight`. |
| `--free-agents` / `--club <id>` | Contract situation. |
| `--name` | Substring of the display name. |
| `--playstyle` | PlayStyle, repeatable and ANDed: `--playstyle "Power Header" --playstyle Aerial`. `mmb playstyles` lists all 34. |
| `--playstyle-plus` | Only players whose PlayStyle is the gold PlayStyle+ version. Rare - about 1 player in 120. |
| `--trait` | Legacy FIFA trait (Leadership, Injury Prone, Solid Player, Team Player). Stored by the game but no longer displayed as a PlayStyle. |
| `--sort` / `--ascending` / `--limit` | Ranking and how much to show (`--limit 0` for everything). |
| `--include-implausible` | Turn off the quality screen described below. |

Run `mmb attributes` to see exactly which attributes **this** career exposes and both names you
can filter them by - the list is discovered from your imported data, not hard-coded.
`mmb playstyles` lists the 34 PlayStyles by category.

Then inspect one result in full:

```bash
mmb player 462067
```

**Two rules worth knowing.**

First, `mmb search` hides rows that don't look like real players, and says how many it hid.
The save's decoder produces a known share of junk rows (see HANDOFF.md), and they cluster hard
around two impossible-value signatures, both measured against a public reference dataset on a
real save:

- **an overall below 40.** Those rows are 5% likely to be a player in the reference set; rows
  at 40+ are 62% likely, all have parsed attributes, and number ~21,800 - about what an FC25
  career actually holds.
- **any attribute above 99**, the game's hard cap. A further 4.4% of rows, of which only 3%
  are in the reference set.

Without the screen, a search sorted by potential is topped by rows reading "OVR 1 / POT 99",
or a playmaker with Vision 103, that don't exist in your game.
The screen only affects what's displayed; nothing is dropped from your imported data, and
`--include-implausible` shows everything.

Second, a player whose value for a filtered field is unknown does not
match. The save doesn't expose every field for every player, so `--min-pot 85` returns players
confirmed to have potential 85+, never players who might. If a search comes back empty, loosen
one filter at a time to find which one is doing the work.

### 4. Or just describe what you want

Works with an Anthropic key or a Google AI Studio key - whichever you have:

```bash
# Anthropic
pip install anthropic
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# or Google AI Studio (aistudio.google.com/apikey)
pip install google-genai
$env:GEMINI_API_KEY = "AIza..."

mmb ask "target man striker, tall and strong, potential 85+"
mmb ask "left-footed centre back under 23 who can pass"
mmb ask "wonderkid wingers with pace, cheap"
```

**This is not vector RAG over your players, on purpose.** Embedding a text card per player and
retrieving nearest neighbours cannot enforce "height >= 187 and potential >= 85" - it returns
players that *read* like the request and silently drops ones that actually match, and it can
never tell you how many qualify. So the model is used for the part it is good at, reading
your sentence, and the filter engine does the part it is good at:

```text
your sentence -> the model (structured output) -> a validated filter spec
              -> the same filters `mmb search` uses, over every player -> exact results
```

The model never sees player data and never chooses the results, so nothing in the table is
invented - it writes the query, your save answers it. The interpreted query is printed above
the results, so you can see how it read you and drop to `mmb search` to adjust one filter.
Anything it couldn't express (price, league) is listed as not applied, rather than quietly
approximated. `--dry-run` shows the query without running it.

MMB uses whichever provider you have a key for (`--provider anthropic|google` or
`MMB_PROVIDER` forces one). Default models are `claude-opus-5` and `gemini-2.5-flash`;
`--model` / `MMB_MODEL` picks another, and if a Google model name is rejected MMB lists the
ones your key can actually use. Both providers are asked for the same structured query and
both answers go through the same validation, so the choice only affects the translation.

Everything else in MMB works without an API key and never makes a network call.

**Known limitations of this data** (see HANDOFF.md for the full detail):
- Age is derived from a decoded date of birth (99.3% agreement with a public reference) and the
  career's *estimated* current date - the table holding the real one doesn't decode reliably,
  so MMB infers it from transfer history (2027-08-21 on the test save, corroborated to within
  three weeks by contract dates). Ages are therefore the save's own, not the real world's, and
  accurate to within a year: cross-checked against the reference dataset, 86% of players differ
  by exactly the expected constant and 13% by one more, which is the birthday-boundary effect
  of comparing two dates. Treat an age filter's edges as approximate.
- Primary `position` matched a public reference dataset for 89% of screened players on the real
  test save - good, not exact. Secondary positions couldn't be validated (that reference lists
  only one position per player) and show some internally-implausible values, which is why
  `--include-secondary` is opt-in. `mmb player` flags a position decoded from one of the rarer,
  inferred position codes.
- Potential tracks the reference dataset closely (83% within 2 points); current overall reads
  about 4 points higher on median, which is what you'd expect from a career several seasons in
  rather than a decode error - but it hasn't been separately proven.
- PlayStyles are decoded, PlayStyle+ included, for everyone who has them - 9,380 of 21,950
  players in the test save, 2,107 of them career-generated. The bit-to-name mapping wasn't
  guessed: it was solved against a reference dataset and scores F1 0.9968 against the shipped
  launch database, with `icontrait` (PlayStyle+) independently reproducing the same mapping
  bit-for-bit. Three of the 41 bits are set only on career-generated players, so nothing could
  name them; they are reported as nothing rather than guessed at. Two names follow the
  reference dataset's wording where the game may differ (`Whipped Cross`, `Close Down`) -
  `--playstyle` accepts the in-game wording too.
- Names are decoded for every player, career-generated ones included, and agree exactly with a
  public reference dataset for 99.1% of the 13,874 players both know about. The residual is
  that dataset preferring a longer legal name (`Hodei Arrillaga Elezgarai`) over the one the
  game shows (`Hodei`), plus a few transliteration differences - not decode failures. MMB shows
  the name the game shows, and an in-game name edit wins over both.
- Clubs are bare IDs in search results, so `--club` takes an id (free agents excepted, which
  are labelled). Club, league and nation names are all decodable now via the same string tables
  as player names, but aren't wired into the results table yet - see HANDOFF.md.
- The save stores no market value at all - FC computes price at runtime, so
  there is no price filter. (A value *can* be computed: on the reference dataset, a lookup on
  overall/potential/age plus a position multiplier reproduces published values to a median 3.6%
  error. That would be MMB's first derived number rather than a decoded one, so it is not built
  - see HANDOFF.md.)
- Even after the quality screen, some rows are decode artifacts rather than real players -
  fixing that properly needs an architectural change in the parser, documented in HANDOFF.md.
  Verify a signing in game before you build a transfer window around it.

## License

MIT - see [LICENSE](LICENSE).
