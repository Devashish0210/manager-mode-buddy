import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from . import filters as F
from . import nl
from . import playstyles as PS
from .attributes import discover, label_for
from .extractor import SaveParseError, extract
from .models import ParsedCareer, Player
from .saves import SaveFile, discover_saves, find_save, read_save_name
from .storage import (
    CareerMetadata,
    CareerNotFoundError,
    default_profile_name,
    get_active_career,
    list_careers,
    load_career,
    profile_dir,
    resolve_profile,
    save_career,
    set_active_career,
    set_as_of_date,
)

app = typer.Typer(
    name="mmb",
    help="Manager Mode Buddy - a local-first scouting assistant for EA Sports FC 25 Manager Career Mode.",
    # Bare `mmb` starts the interactive scout rather than printing help: spec Section 18 calls
    # the interactive UI the priority, and it is the only entry point that needs no arguments
    # to be useful. `mmb --help` still lists every direct command.
    invoke_without_command=True,
)


def _console_width() -> Optional[int]:
    """Rich falls back to 80 columns whenever output isn't a terminal (a pipe, a redirect, the
    test harness), which is narrow enough that the results table silently truncates player
    names -- the one column a user needs verbatim to look a player up in game. Let rich
    auto-detect a real terminal; give piped output a width the full table fits in."""
    return None if sys.stdout.isatty() else 160


console = Console(width=_console_width())


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Manager Mode Buddy - a local-first scouting assistant for EA Sports FC 25 Manager Career Mode."""
    if ctx.invoked_subcommand is None:
        try:
            _run_scout()
        except (typer.Abort, KeyboardInterrupt, EOFError):
            typer.echo("")
            typer.echo("Bye.")


@app.command()
def version() -> None:
    """Show the installed Manager Mode Buddy version."""
    typer.echo(f"Manager Mode Buddy v{__version__}")


# --- shared helpers -----------------------------------------------------------------------

def _resolve_meta_xml(meta_xml: Optional[str]) -> str:
    resolved = meta_xml or os.environ.get("MMB_META_XML")
    if not resolved:
        raise typer.BadParameter(
            "fifa_ng_db-meta.xml path required. Pass --meta-xml or set the MMB_META_XML "
            "environment variable. This file ships with EA's own game database, not with "
            "MMB -- see README.md for where to get a copy."
        )
    return resolved


def _run_extract(save: str, meta_xml: Optional[str], names_csv: Optional[str],
                  game_db: Optional[str] = None) -> ParsedCareer:
    resolved_meta_xml = _resolve_meta_xml(meta_xml)
    resolved_names_csv = names_csv or os.environ.get("MMB_NAMES_CSV")
    try:
        result = extract(save, resolved_meta_xml, resolved_names_csv, game_db)
    except SaveParseError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    return ParsedCareer(
        save_path=save,
        players=result.players,
        team_ids=result.team_ids,
        as_of_date=result.as_of_date,
    )


def _display_name(player: Player) -> str:
    return player.name or f"Player {player.id}"


def _load(profile: Optional[str]) -> tuple[str, ParsedCareer]:
    """Resolve which career to use and load it from SQLite. Every read-only command goes
    through here so they all accept --profile, all fall back to the active career, and all
    fail with the same actionable message."""
    try:
        name = resolve_profile(profile)
        return name, load_career(name)
    except CareerNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)


# Every real player seen anywhere in this project's research (in this save and others) has
# had a playerid in the thousands or higher; a handful of very low ids (1, 2, ...) have turned
# up decoded with plainly-impossible stats (overall=0, or potential far below overall before
# that specific case was fixed at the extractor level -- see HANDOFF.md's "What Milestone 1
# found"). These look like template/placeholder records FC-family games are known to reserve
# low ids for, not decode bugs, but this isn't proven for every save. Rather than filter them
# out of the actual dataset on an unconfirmed threshold (which would risk silently dropping a
# real low-id player MMB has just never happened to see), this floor is scoped narrowly to
# picking a plausible player for the `mmb parse` demo line -- a low-stakes cosmetic choice, not
# a data-quality claim about the full player list.
_PLAUSIBLE_MIN_ID = 1000
_PLAUSIBLE_MIN_OVERALL = 40


def _pick_sample_player(players: list[Player]) -> Optional[Player]:
    def plausible(p: Player) -> bool:
        return (
            p.overall is not None
            and p.overall >= _PLAUSIBLE_MIN_OVERALL
            and p.id >= _PLAUSIBLE_MIN_ID
        )

    # Prefer a player sourced from the exact-match-validated growth table over the broader
    # but noisier merged pool.
    return (
        next((p for p in players if plausible(p) and p.overall_source == "career_playergrowthuserseason"), None)
        or next((p for p in players if plausible(p)), None)
        or next((p for p in players if p.overall is not None), None)
    )


def _print_player_summary(career: ParsedCareer) -> None:
    typer.echo(f"Players: {career.player_count:,}")
    typer.echo(f"Teams: {career.team_count:,}")

    sample = _pick_sample_player(career.players)
    if sample:
        typer.echo("")
        typer.echo("Sample player:")
        typer.echo("")
        typer.echo(f"ID: {sample.id}")
        typer.echo(f"Name: {_display_name(sample)}")
        typer.echo(f"OVR: {sample.overall if sample.overall is not None else 'unknown'}")
        typer.echo(f"Potential: {sample.potential if sample.potential is not None else 'unknown'}")
        typer.echo(f"Position: {sample.position or 'unknown'}")


# --- save picker (spec Sections 16/17) ----------------------------------------------------

def _short(when: Optional[datetime]) -> str:
    return when.strftime("%Y-%m-%d %H:%M") if when else "-"


def _parse_iso(text: Optional[str]) -> Optional[datetime]:
    """Parse a stored timestamp as naive local time. `imported_at` is written UTC-aware while
    file mtimes come back naive, and Python refuses to order one against the other -- so they
    have to be normalised before they can share a sort key."""
    try:
        parsed = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    if parsed is None:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _disambiguate(labels: List[str]) -> List[str]:
    """Nothing stops two saves sharing an in-game name, and a picker listing the same label
    twice is worse than showing no label at all. Only the repeats get qualified, so the common
    case stays clean."""
    counts = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return [label if counts[label] == 1 else None for label in labels]


def _save_table(found: List[SaveFile]) -> Table:
    table = Table(title="FC MANAGER CAREER SAVES", title_justify="left", header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Save")
    table.add_column("Game")
    table.add_column("Modified")
    table.add_column("Size", justify="right")
    unique = _disambiguate([save.label for save in found])
    for index, (save, label) in enumerate(zip(found, unique), start=1):
        # A save whose name isn't unique gets its file name alongside, so the user can still
        # tell which is which and still pass something unambiguous to `mmb import`.
        shown = label or f"{save.label}  ({save.name})"
        table.add_row(
            str(index),
            shown,
            save.game,
            save.modified.strftime("%Y-%m-%d %H:%M"),
            f"{save.size_mb:.1f} MB",
        )
    return table


def _no_saves_message(extra_dirs: List[str]) -> None:
    typer.echo("No Manager Career saves found.")
    typer.echo("")
    typer.echo("Looked in the game's own save folders:")
    typer.echo("  %LOCALAPPDATA%\\EA SPORTS FC 25\\settings   (FC 25)")
    typer.echo("  Documents\\FC 24\\settings                  (older titles, OneDrive included)")
    typer.echo("")
    typer.echo("If your saves live elsewhere, pass --dir <folder>, set MMB_SAVE_DIRS, or")
    typer.echo("give `mmb import` the full path to the save file.")
    if extra_dirs:
        typer.echo(f"Also looked in: {', '.join(extra_dirs)}")


@app.command()
def saves(
    dir: List[str] = typer.Option(
        [], "--dir", help="Extra folder to scan for save files (repeatable). Also reads MMB_SAVE_DIRS.",
    ),
) -> None:
    """List the FC Manager Career save files found on this machine.

    This is the picker's catalogue: run it to see what MMB can see, then `mmb import` (with no
    argument) to choose one of these interactively, or `mmb import <name-or-path>` directly.
    """
    found = discover_saves(dir)
    if not found:
        _no_saves_message(dir)
        return
    console.print(_save_table(found))
    typer.echo("")
    typer.echo("Import one with: mmb import   (interactive picker), or mmb import \"<save name>\"")


def _pick_save(extra_dirs: List[str]) -> SaveFile:
    """Interactive save picker. Only used when `mmb import` is called with no save argument,
    so scripted/direct usage never gets a prompt."""
    found = discover_saves(extra_dirs)
    if not found:
        _no_saves_message(extra_dirs)
        raise typer.Exit(code=1)
    console.print(_save_table(found))
    typer.echo("")
    choice = typer.prompt("Select a save to import (number)", type=int, default=1)
    if not 1 <= choice <= len(found):
        typer.echo(f"Error: {choice} is not one of the listed saves.", err=True)
        raise typer.Exit(code=1)
    return found[choice - 1]


# --- parse / import -----------------------------------------------------------------------

@app.command()
def parse(
    save: str = typer.Argument(..., help="Path to an FC25 ManagerCareer save file."),
    meta_xml: Optional[str] = typer.Option(
        None, "--meta-xml", help="Path to fifa_ng_db-meta.xml (or set MMB_META_XML)."
    ),
    names_csv: Optional[str] = typer.Option(
        None, "--names-csv",
        help="Optional CSV (player_id, full_name), used for display names only when the game "
        "database isn't available (or set MMB_NAMES_CSV).",
    ),
    game_db: Optional[str] = typer.Option(
        None, "--game-db",
        help="Path to the shipped fifa_ng_db.db, which holds the name tables. Defaults to the "
        "file sitting next to --meta-xml (or set MMB_GAME_DB).",
    ),
) -> None:
    """Parse an FC25 save and report what was extracted, without saving anything.

    A dry-run/diagnostic command: identifies player records and builds normalized Player
    objects but never touches ~/.manager-buddy. Use `mmb import` to persist the result into a
    reusable career profile.
    """
    career = _run_extract(save, meta_xml, names_csv, game_db)

    typer.echo("Save parsed successfully.")
    typer.echo("")
    _print_player_summary(career)


@app.command(name="import")
def import_save(
    save: Optional[str] = typer.Argument(
        None,
        help="Save to import, by its in-game name (as shown by `mmb saves`), its file name, or "
        "a full path. Omit it to pick from a list of detected saves.",
    ),
    meta_xml: Optional[str] = typer.Option(
        None, "--meta-xml", help="Path to fifa_ng_db-meta.xml (or set MMB_META_XML)."
    ),
    names_csv: Optional[str] = typer.Option(
        None, "--names-csv",
        help="Optional CSV (player_id, full_name), used for display names only when the game "
        "database isn't available (or set MMB_NAMES_CSV).",
    ),
    game_db: Optional[str] = typer.Option(
        None, "--game-db",
        help="Path to the shipped fifa_ng_db.db, which holds the name tables. Defaults to the "
        "file sitting next to --meta-xml (or set MMB_GAME_DB).",
    ),
    profile: Optional[str] = typer.Option(
        None, "--profile",
        help="Career profile name to store this import under (default: derived from the save file name). "
        "Importing an existing profile name replaces its stored data.",
    ),
    dir: List[str] = typer.Option(
        [], "--dir", help="Extra folder to scan when picking a save (repeatable).",
    ),
) -> None:
    """Detect, parse, and import an FC25 save into a local career profile.

    With no save argument this lists the saves found on this machine and asks which one to
    import. Persists into ~/.manager-buddy/saves/<profile>/ (SQLite + metadata.json) so later
    commands never re-parse the original save file, and makes the imported career the active
    one for `mmb search`.
    """
    if save is None:
        picked = _pick_save(dir)
        save_path = str(picked.path)
        typer.echo("")
        typer.echo(f"Importing {picked.label} ...")
    else:
        found = find_save(save, dir)
        if found is None:
            typer.echo(
                f"Error: no save found at or named {save!r}. Run `mmb saves` to see what MMB "
                "can find -- the Save column is what to pass here (quote it if it has spaces).",
                err=True,
            )
            raise typer.Exit(code=1)
        save_path = str(found.path)

    typer.echo("Parsing the save. This takes a few seconds; later commands read the imported")
    typer.echo("copy instead and are instant.")
    typer.echo("")

    career = _run_extract(save_path, meta_xml, names_csv, game_db)
    save_name = read_save_name(Path(save_path))
    profile_name = profile or default_profile_name(save_path, save_name)
    save_career(profile_name, career, save_name)
    set_active_career(profile_name)

    typer.echo("Save detected")
    typer.echo("FC25 career identified")
    typer.echo("Database parsed")
    typer.echo("")
    _print_player_summary(career)
    typer.echo("")
    typer.echo(f"Save imported successfully as career profile '{profile_name}'.")
    typer.echo(f"Stored at: {profile_dir(profile_name)}")
    typer.echo(f"Active career is now '{profile_name}'. Try: mmb search --pos ST --min-pot 85")


# --- career selection ---------------------------------------------------------------------

@app.command()
def careers(
    profile: Optional[str] = typer.Argument(
        None, help="Show details for one imported career profile instead of listing all."
    ),
) -> None:
    """List locally imported career profiles, or show one profile's details.

    Reads only the persisted SQLite data (Milestone 2 storage) -- never re-parses the original
    save file, so this works even if the save has since moved or been deleted.
    """
    if profile is None:
        profiles = list_careers()
        if not profiles:
            typer.echo("No imported careers yet. Run `mmb import <save>` to import one.")
            return
        active = get_active_career()
        for meta in profiles:
            marker = "*" if meta.profile == active else " "
            typer.echo(
                f"{marker} {meta.label}  [{meta.profile}]  players={meta.player_count:,}  "
                f"teams={meta.team_count:,}  imported={meta.imported_at[:10]}"
            )
        if active:
            typer.echo("")
            typer.echo("* = active career. [name in brackets] is the profile: what "
                        "`mmb use` and `--profile` take.")
        return

    try:
        career = load_career(profile)
    except CareerNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    meta = next((m for m in list_careers() if m.profile == profile), None)
    if meta is not None and meta.save_name:
        typer.echo(f"Save: {meta.save_name}")
    typer.echo(f"Career profile: {profile}")
    if career.as_of_date:
        typer.echo(f"Current date in this career (estimated): {career.as_of_date}")
    typer.echo("")
    _print_player_summary(career)


@app.command()
def use(
    profile: Optional[str] = typer.Argument(
        None, help="Career profile to make active. Omit it to pick from a list."
    ),
) -> None:
    """Choose which imported career the other commands work on.

    Saved in ~/.manager-buddy/config/, so `mmb search` and friends don't need --profile every
    time (they still accept it, and it wins when given).
    """
    profiles = list_careers()
    if not profiles:
        typer.echo("No imported careers yet. Run `mmb import` to import one.")
        raise typer.Exit(code=1)

    if profile is None:
        for index, meta in enumerate(profiles, start=1):
            typer.echo(f"{index}. {meta.profile}  ({meta.player_count:,} players)")
        typer.echo("")
        choice = typer.prompt("Select a career (number)", type=int, default=1)
        if not 1 <= choice <= len(profiles):
            typer.echo(f"Error: {choice} is not one of the listed careers.", err=True)
            raise typer.Exit(code=1)
        profile = profiles[choice - 1].profile

    try:
        set_active_career(profile)
    except CareerNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Active career is now '{profile}'.")


# --- attributes ---------------------------------------------------------------------------

@app.command()
def playstyles() -> None:
    """List every PlayStyle MMB can filter on, by category.

    These are decoded from the save's own bitmask, not a hard-coded guess -- see
    playstyles.py for how the mapping was established and how well it validates.
    """
    table = Table(title="PLAYSTYLES", title_justify="left", header_style="bold")
    table.add_column("Category")
    table.add_column("PlayStyles")
    for category in PS.CATEGORY_ORDER:
        styles = [name for name, cat in PS.PLAYSTYLES.values() if cat == category]
        table.add_row(category, ", ".join(styles))
    console.print(table)
    typer.echo("")
    typer.echo(f"{len(PS.ALL_PLAYSTYLES)} PlayStyles. Filter with:")
    typer.echo('  mmb search --playstyle "Power Header" --playstyle Aerial')
    typer.echo('  mmb search --playstyle-plus "Finesse Shot"      (only the gold version)')
    typer.echo("")
    typer.echo(f"Legacy FIFA traits, stored separately and not shown in game: "
                f"{', '.join(PS.ALL_TRAITS)}")
    typer.echo("Filter those with --trait. Two PlayStyles are listed under the reference")
    typer.echo("dataset's wording; your game may label them Whipped Pass and Rush Out, and")
    typer.echo("both spellings work in --playstyle.")


@app.command()
def attributes(
    profile: Optional[str] = typer.Option(None, "--profile", help="Career profile to inspect."),
) -> None:
    """Show the attributes this career actually exposes, and how to filter on them.

    Discovered from the imported data rather than hard-coded (spec Section 22), so an
    attribute only appears here if the parser really produced it for this save.
    """
    name, career = _load(profile)
    present = set()
    for player in career.players:
        present.update(player.attributes)

    grouped = discover(present)
    if not grouped:
        typer.echo(f"Career '{name}' has no parsed attributes.")
        return

    typer.echo(f"Filterable attributes in career '{name}':")
    for category, infos in grouped.items():
        typer.echo("")
        typer.echo(category)
        for info in infos:
            typer.echo(f"  {info.label:<22} {info.field}")
    typer.echo("")
    typer.echo("Filter with either name, e.g.:")
    typer.echo('  mmb search --attr "vision>=80" --attr "Short Passing >= 78"')


# --- search (spec Sections 3/5/6/8/21) ----------------------------------------------------

def _build_filters(
    pos: List[str], include_secondary: bool, min_age: Optional[int], max_age: Optional[int],
    min_ovr: Optional[int], max_ovr: Optional[int],
    min_pot: Optional[int], max_pot: Optional[int], min_growth: Optional[int],
    min_height: Optional[int], max_height: Optional[int], min_weight: Optional[int],
    max_weight: Optional[int], attr: List[str], foot: Optional[str], free_agents: bool,
    club: Optional[int], name: Optional[str], playstyle: List[str] = (),
    playstyle_plus: List[str] = (), trait: List[str] = (),
) -> List[F.PlayerFilter]:
    built: List[F.PlayerFilter] = []
    if pos:
        built.append(F.PositionFilter(tuple(pos), include_secondary=include_secondary))
    if min_age is not None or max_age is not None:
        built.append(F.AgeFilter(minimum=min_age, maximum=max_age))
    if min_ovr is not None or max_ovr is not None:
        built.append(F.OverallFilter(minimum=min_ovr, maximum=max_ovr))
    if min_pot is not None or max_pot is not None:
        built.append(F.PotentialFilter(minimum=min_pot, maximum=max_pot))
    if min_growth is not None:
        built.append(F.GrowthFilter(minimum=min_growth))
    if min_height is not None or max_height is not None:
        built.append(F.HeightFilter(minimum=min_height, maximum=max_height))
    if min_weight is not None or max_weight is not None:
        built.append(F.WeightFilter(minimum=min_weight, maximum=max_weight))
    for expression in attr:
        built.append(F.parse_attribute_filter(expression))
    if foot:
        built.append(F.FootFilter(foot))
    if free_agents:
        built.append(F.FreeAgentFilter())
    if club is not None:
        built.append(F.ClubFilter(club))
    if name:
        built.append(F.NameFilter(name))
    if playstyle:
        built.append(F.PlayStyleFilter(_resolve_playstyles(playstyle)))
    if playstyle_plus:
        built.append(F.PlayStyleFilter(_resolve_playstyles(playstyle_plus), plus_only=True))
    if trait:
        built.append(F.TraitFilter(_resolve_traits(trait)))
    return built


def _resolve_playstyles(requested: List[str]) -> tuple:
    """Turn what the user typed into canonical PlayStyle names, or fail with the list. A
    typo'd PlayStyle would otherwise silently match nobody, which reads exactly like "no
    player has it"."""
    out = []
    for text in requested:
        canonical = PS.resolve(text)
        if canonical is None:
            raise typer.BadParameter(
                f"{text!r} isn't a PlayStyle. Run `mmb playstyles` to see all "
                f"{len(PS.ALL_PLAYSTYLES)}."
            )
        out.append(canonical)
    return tuple(out)


def _resolve_traits(requested: List[str]) -> tuple:
    lookup = {t.lower(): t for t in PS.ALL_TRAITS}
    out = []
    for text in requested:
        canonical = lookup.get(" ".join(text.split()).lower())
        if canonical is None:
            raise typer.BadParameter(
                f"{text!r} isn't a trait. Known traits: {', '.join(PS.ALL_TRAITS)}."
            )
        out.append(canonical)
    return tuple(out)


def _results_table(players: List[Player], attr_columns: List[str]) -> Table:
    table = Table(title="SCOUTING RESULTS", title_justify="left", header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("ID", justify="right")
    table.add_column("Player")
    table.add_column("Pos")
    table.add_column("Age", justify="right")
    table.add_column("OVR", justify="right")
    table.add_column("POT", justify="right")
    table.add_column("+", justify="right")
    table.add_column("Foot")
    table.add_column("Club", justify="right")
    for field in attr_columns:
        table.add_column(label_for(field), justify="right")

    for index, player in enumerate(players, start=1):
        growth = (
            str(player.potential - player.overall)
            if player.potential is not None and player.overall is not None
            else "-"
        )
        club = "free agent" if player.is_free_agent else (str(player.club_id) if player.club_id else "-")
        row = [
            str(index),
            str(player.id),
            _display_name(player),
            player.position or "?",
            str(player.age) if player.age is not None else "-",
            str(player.overall) if player.overall is not None else "-",
            str(player.potential) if player.potential is not None else "-",
            growth,
            player.preferred_foot or "-",
            club,
        ]
        row.extend(str(player.attributes.get(f, "-")) for f in attr_columns)
        table.add_row(*row)
    return table


@app.command()
def search(
    profile: Optional[str] = typer.Option(None, "--profile", help="Career profile to search."),
    pos: List[str] = typer.Option(
        [], "--pos", help="Position to match, e.g. ST (repeatable; any of them matches). "
        "Side-specific variants count, so CM matches RCM/LCM.",
    ),
    include_secondary: bool = typer.Option(
        False, "--include-secondary",
        help="Also match secondary positions. Widens the net, but these decode less reliably "
        "than the primary position.",
    ),
    min_age: Optional[int] = typer.Option(None, "--min-age", help="Minimum age, in the career's own season."),
    max_age: Optional[int] = typer.Option(None, "--max-age", help="Maximum age, in the career's own season."),
    min_ovr: Optional[int] = typer.Option(None, "--min-ovr", help="Minimum current overall."),
    max_ovr: Optional[int] = typer.Option(None, "--max-ovr", help="Maximum current overall."),
    min_pot: Optional[int] = typer.Option(None, "--min-pot", help="Minimum potential."),
    max_pot: Optional[int] = typer.Option(None, "--max-pot", help="Maximum potential."),
    min_growth: Optional[int] = typer.Option(
        None, "--min-growth", help="Minimum remaining growth (potential minus current overall).",
    ),
    min_height: Optional[int] = typer.Option(None, "--min-height", help="Minimum height in cm."),
    max_height: Optional[int] = typer.Option(None, "--max-height", help="Maximum height in cm."),
    min_weight: Optional[int] = typer.Option(None, "--min-weight", help="Minimum weight in kg."),
    max_weight: Optional[int] = typer.Option(None, "--max-weight", help="Maximum weight in kg."),
    attr: List[str] = typer.Option(
        [], "--attr",
        help='Attribute filter, e.g. "vision>=80", "Short Passing >= 78", "finishing=75-85" (repeatable).',
    ),
    foot: Optional[str] = typer.Option(None, "--foot", help="Preferred foot: Left or Right."),
    free_agents: bool = typer.Option(False, "--free-agents", help="Only players with no club."),
    club: Optional[int] = typer.Option(None, "--club", help="Only players at this club id."),
    name: Optional[str] = typer.Option(None, "--name", help="Only players whose name contains this text."),
    playstyle: List[str] = typer.Option(
        [], "--playstyle",
        help="Only players with this PlayStyle (repeatable; all of them must match). "
        "See `mmb playstyles`.",
    ),
    playstyle_plus: List[str] = typer.Option(
        [], "--playstyle-plus",
        help="Only players who have this PlayStyle as a PlayStyle+ (the gold one). Rare: "
        "about 1 player in 140 has any PlayStyle+ at all.",
    ),
    trait: List[str] = typer.Option(
        [], "--trait",
        help="Legacy FIFA trait (Leadership, Injury Prone, Solid Player, Team Player). Not a "
        "PlayStyle - the game stores these separately and no longer displays them.",
    ),
    sort: str = typer.Option(
        "potential", "--sort", help="Sort by potential, overall, growth, id, or any attribute name.",
    ),
    ascending: bool = typer.Option(False, "--ascending", help="Sort low to high instead of high to low."),
    limit: int = typer.Option(25, "--limit", help="How many players to show (0 for all)."),
    include_implausible: bool = typer.Option(
        False, "--include-implausible",
        help="Also show rows that don't look like real players (see the note in the results).",
    ),
) -> None:
    """Search an imported career for players matching your filters.

    All filters are combined with AND.
    A player whose value for a filtered field is unknown does not match:
    the save doesn't expose every field for every player, and a filter
    should return players confirmed to meet it, not players who might.

    Examples:

      mmb search --pos ST --min-pot 85 --max-ovr 75

      mmb search --pos CAM --min-ovr 70 --attr "vision>=80" --attr "Short Passing >= 78"
    """
    career_name, career = _load(profile)

    # The plausibility screen is applied first and reported separately from the user's own
    # filters: it is MMB's judgement about the save data, not something the user asked for, so
    # it stays visible and switchable rather than quietly shrinking the search space.
    pool = career.players if include_implausible else F.apply(career.players, [F.PlausiblePlayerFilter()])
    screened_out = career.player_count - len(pool)

    try:
        active_filters = _build_filters(
            pos, include_secondary, min_age, max_age, min_ovr, max_ovr, min_pot, max_pot,
            min_growth, min_height, max_height, min_weight, max_weight,
            attr, free_agents=free_agents, club=club, foot=foot, name=name,
            playstyle=playstyle, playstyle_plus=playstyle_plus, trait=trait,
        )
        matched = F.apply(pool, active_filters)
        ordered = F.sort_players(matched, key=sort, descending=not ascending)
    except F.FilterError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Career: {career_name}   Players searched: {len(pool):,}"
               + (f"   Season date: {career.as_of_date}" if career.as_of_date else ""))
    if screened_out:
        typer.echo(
            f"Skipped {screened_out:,} of {career.player_count:,} rows that don't look like real "
            "players (no readable overall, or one below 40 - see README). --include-implausible keeps them."
        )
    if active_filters:
        typer.echo("Filters: " + "; ".join(f.describe() for f in active_filters))
    else:
        typer.echo("Filters: none (showing the whole career)")
    typer.echo("")

    if not ordered:
        typer.echo("No players matched.")
        typer.echo("")
        # A --name search against a career imported without the game database can never match,
        # and that looks identical to "nobody is called that" unless it's called out.
        if name and not any(p.name for p in pool):
            typer.echo("This career has no player names stored, so --name can't match anything.")
            typer.echo("Names come from the game's own fifa_ng_db.db; re-import with --game-db")
            typer.echo("pointing at it (see README), or search by position and ratings instead.")
            return
        typer.echo("A filtered field the save doesn't expose for a player excludes that player,")
        typer.echo("so try loosening one filter at a time to see which one is doing the work.")
        return

    shown = ordered if limit <= 0 else ordered[:limit]
    # One column per attribute filtered on, in the order given; two bounds on the same
    # attribute (--attr "vision>=80" --attr "vision<=90") are still one column.
    attr_columns = list(dict.fromkeys(
        f.attribute for f in active_filters if isinstance(f, F.AttributeFilter)
    ))
    console.print(_results_table(shown, attr_columns))
    typer.echo("")
    typer.echo(f"Matched {len(ordered):,} players; showing {len(shown):,}, sorted by {sort}.")
    typer.echo("Full detail for one of them: mmb player <ID>")


# --- natural language (spec Section 29) ---------------------------------------------------

def _render_results(players: List[Player], attr_columns: List[str], total: int,
                    sort_by: str) -> None:
    console.print(_results_table(players, attr_columns))
    typer.echo("")
    typer.echo(f"Matched {total:,} players; showing {len(players):,}, sorted by {sort_by}.")
    typer.echo("Full detail for one of them: mmb player <ID>")


@app.command()
def ask(
    question: str = typer.Argument(..., help="What you're looking for, in plain English."),
    profile: Optional[str] = typer.Option(None, "--profile", help="Career profile to search."),
    provider: Optional[str] = typer.Option(
        None, "--provider",
        help=f"anthropic or google. Default: whichever key is set (or set {nl.PROVIDER_ENV}).",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", help=f"Model to translate with (or set {nl.MODEL_ENV})."
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key",
        help="API key. Otherwise read from ANTHROPIC_API_KEY, or GEMINI_API_KEY / "
             "GOOGLE_API_KEY for Google AI Studio.",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Override how many players to show."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the query the model wrote, without running it."
    ),
    include_implausible: bool = typer.Option(
        False, "--include-implausible", help="Skip the data-quality screen (see `mmb search`)."
    ),
) -> None:
    """Search your career by describing what you want, in plain English.

    Claude translates the request into MMB's own filters and the normal filter engine runs
    them over every player. The model never sees player data and never picks the results, so
    nothing in the table is invented: it writes the query, your save answers it. The
    interpreted query is printed first, so you can check the reading and fall back to
    `mmb search` to adjust any one filter.

    Works with either an Anthropic key (ANTHROPIC_API_KEY) or a Google AI Studio key
    (GEMINI_API_KEY or GOOGLE_API_KEY) -- whichever is set is used, and --provider forces one.

    Examples:

      mmb ask "target man striker, tall and strong, potential 85+"

      mmb ask "left-footed centre back who can pass, ready to start now"

      mmb ask "cheapest wonderkid wingers with pace"
    """
    career_name, career = _load(profile)
    pool = career.players if include_implausible else F.apply(
        career.players, [F.PlausiblePlayerFilter()]
    )
    if not pool:
        typer.echo(f"Career '{career_name}' has no scoutable players.", err=True)
        raise typer.Exit(code=1)

    facts = nl.CareerFacts.from_career(career, pool)
    try:
        interpreted = nl.interpret(question, facts, model=model, api_key=api_key,
                                   provider=provider)
    except nl.NLError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Career: {career_name}   Players searched: {len(pool):,}"
               + (f"   Season date: {career.as_of_date}" if career.as_of_date else ""))
    if interpreted.reading:
        typer.echo(f"Read as: {interpreted.reading}")
    if interpreted.filters:
        typer.echo("Filters: " + "; ".join(f.describe() for f in interpreted.filters))
    else:
        typer.echo("Filters: none -- the request didn't narrow anything down.")
    typer.echo(f"Sorted by: {interpreted.sort_by}"
               f"{' (low to high)' if interpreted.ascending else ''}")
    if interpreted.model:
        typer.echo(f"Translated by: {interpreted.model}")

    if interpreted.unsupported:
        typer.echo("")
        typer.echo("Not applied (MMB can't filter on these yet):")
        for item in interpreted.unsupported:
            reason = nl.explain_gap(item)
            typer.echo(f"  - {item}" + (f" -- {reason}" if reason else ""))

    if dry_run:
        typer.echo("")
        typer.echo("Dry run: nothing was searched. Run without --dry-run to see players.")
        return

    typer.echo("")
    matched = F.apply(pool, interpreted.filters)
    ordered = F.sort_players(matched, key=interpreted.sort_by,
                             descending=not interpreted.ascending)
    if not ordered:
        typer.echo("No players matched that.")
        typer.echo("")
        typer.echo("Every filter above is ANDed, and a player whose value for one of them is")
        typer.echo("unknown is excluded. Try asking for less, or run `mmb search` with just the")
        typer.echo("one or two filters that matter most.")
        return

    shown_limit = limit if limit is not None else interpreted.limit
    shown = ordered if shown_limit <= 0 else ordered[:shown_limit]
    attr_columns = list(dict.fromkeys(
        f.attribute for f in interpreted.filters if isinstance(f, F.AttributeFilter)
    ))
    _render_results(shown, attr_columns, len(ordered), interpreted.sort_by)

@app.command()
def models(
    provider: Optional[str] = typer.Option(
        None, "--provider", help="anthropic or google. Default: whichever key is set."
    ),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="API key to ask with."),
) -> None:
    """List the models your API key can use for `mmb ask`, best first.

    Model names change on the provider's schedule, not MMB's, so this asks your key rather
    than relying on a name baked into this tool.
    """
    try:
        chosen, names = nl.list_models(provider, api_key)
    except nl.NLError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if not names:
        typer.echo(f"{nl.PROVIDER_LABELS[chosen]} returned no usable models for this key.")
        return
    typer.echo(f"{nl.PROVIDER_LABELS[chosen]} models available to this key:")
    for index, name in enumerate(names):
        marker = "  <- used by default" if index == 0 else ""
        typer.echo(f"  {name}{marker}")
    typer.echo("")
    typer.echo("Pick one with: mmb ask \"...\" --model <name>   (or set MMB_MODEL)")


@app.command()
def player(
    player_id: int = typer.Argument(..., help="Player ID, as shown in `mmb search` results."),
    profile: Optional[str] = typer.Option(None, "--profile", help="Career profile to look in."),
) -> None:
    """Show everything the save gave up about one player.

    Only fields actually present in the parsed data are shown (spec Section 4) -- an absent
    field means the save didn't expose it, not that the player lacks it.
    """
    career_name, career = _load(profile)
    found = next((p for p in career.players if p.id == player_id), None)
    if found is None:
        typer.echo(f"Error: no player with id {player_id} in career '{career_name}'.", err=True)
        raise typer.Exit(code=1)
    _print_player_detail(found, career)


def _print_player_detail(found: Player, career: ParsedCareer) -> None:
    typer.echo("PLAYER")
    typer.echo("")
    typer.echo(_display_name(found))
    typer.echo("")
    rows = [
        ("ID", found.id),
        ("Age", found.age),
        ("Date of birth", found.birth_date),
        ("Overall", found.overall),
        ("Potential", found.potential),
        ("Position", found.position),
        ("Other positions", ", ".join(found.other_positions) or None),
        ("Preferred foot", found.preferred_foot),
        ("Weak foot", found.weak_foot),
        ("Skill moves", found.skill_moves),
        ("Height (cm)", found.height_cm),
        ("Weight (kg)", found.weight_kg),
        ("Nationality id", found.nationality_id),
        ("Club", "free agent" if found.is_free_agent else found.club_id),
    ]
    for label, value in rows:
        if value is not None:
            typer.echo(f"{label + ':':<18}{value}")
    if found.age is not None and career.as_of_date:
        typer.echo("")
        typer.echo(f"Age is as of {career.as_of_date}, this career's current date "
                   "(`mmb date YYYY-MM-DD` corrects it).")
    if found.position and not found.position_confirmed:
        typer.echo("")
        typer.echo("Note: this position code is one of the rarer ones and is inferred, not")
        typer.echo("empirically confirmed (see HANDOFF.md) - worth double-checking in game.")

    if not found.name:
        typer.echo("")
        typer.echo("No name: names are decoded from the game's own name tables, which live in")
        typer.echo("fifa_ng_db.db next to your fifa_ng_db-meta.xml. If that file isn't there,")
        typer.echo("pass --game-db (or set MMB_GAME_DB) and re-import. Look them up by ID until then.")

    if found.playstyles:
        typer.echo("")
        typer.echo("PLAYSTYLES")
        plus = set(found.playstyles_plus)
        for category, styles in PS.by_category(frozenset(found.playstyles)):
            shown = [f"{s} +" if s in plus else s for s in styles]
            typer.echo(f"  {category:<14}{', '.join(shown)}")
        if plus:
            typer.echo("")
            typer.echo("  + marks a PlayStyle+ (the gold one).")
    if found.traits:
        typer.echo("")
        typer.echo(f"TRAITS (legacy, not shown in game): {', '.join(found.traits)}")

    grouped = discover(found.attributes)
    if grouped:
        typer.echo("")
        typer.echo("ATTRIBUTES")
        for category, infos in grouped.items():
            typer.echo("")
            typer.echo(category)
            for info in infos:
                typer.echo(f"  {info.label:<22}{found.attributes[info.field]}")


# --- date -----------------------------------------------------------------------------------

def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise typer.BadParameter(f"{text!r} is not a date. Use YYYY-MM-DD, e.g. 2027-08-21.")


@app.command(name="date")
def set_date(
    new_date: Optional[str] = typer.Argument(
        None, help="The current date in your career, as YYYY-MM-DD. Omit to see what MMB has."
    ),
    profile: Optional[str] = typer.Option(None, "--profile", help="Career profile to change."),
) -> None:
    """Tell MMB what today's date is inside your career, and it recomputes every age.

    MMB has to estimate this date, because the table holding it doesn't decode reliably -- but
    you can just read it off your game, which is exact. Setting it only touches ages, which are
    derived from stored birth dates: the save is never re-parsed.
    """
    career_name, career = _load(profile)
    if new_date is None:
        if career.as_of_date:
            typer.echo(f"Career '{career_name}' is currently at {career.as_of_date} (estimated).")
        else:
            typer.echo(f"Career '{career_name}' has no date set, so players have no age.")
        typer.echo("")
        typer.echo("Set it from your game's own date with: mmb date YYYY-MM-DD")
        return

    parsed = _parse_date(new_date)
    updated = set_as_of_date(career_name, parsed)
    typer.echo(f"Career '{career_name}' is now dated {parsed.isoformat()}.")
    typer.echo(f"{updated:,} player ages recomputed.")


# --- interactive scouting (spec Section 18: "the interactive UI is the priority") -----------

def _career_rows() -> List[CareerMetadata]:
    return list_careers()


def _file_saved_at(path: str) -> Optional[datetime]:
    """When the game last wrote this save file, or None if it's no longer there."""
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime)
    except OSError:
        return None


def _pick_career_interactively() -> Optional[str]:
    """Show every imported career and every save on disk, and let the user choose. Returns the
    chosen profile name, or None if they backed out.

    Ordered by when the game wrote each save, newest first, and every row shows that date. FC
    keeps one file per career and replaces it on every save -- a new timestamped file appears
    and the previous one is deleted -- so playing on turns an imported career into a row whose
    save file is gone, sitting beside a new unimported file carrying the same in-game name.
    Without the date there is nothing on either row to say which is which, or that the imported
    one is now a session behind."""
    careers = _career_rows()
    found = discover_saves()
    imported_paths = {Path(c.save_path).name.lower() for c in careers}
    active = get_active_career()

    # FC replaces a career's file on every save, so an imported career's own file is usually
    # gone by the time the user has played on -- which is why the comparison falls back to the
    # import time rather than treating a missing file as "no information". The imported data is
    # then a snapshot of a session the user has already moved past, and nothing on the row
    # would otherwise say so.
    pending = [s for s in found if s.name.lower() not in imported_paths]
    newest_unimported = {}
    for save in pending:
        current = newest_unimported.get(save.label)
        if current is None or save.modified > current:
            newest_unimported[save.label] = save.modified

    rows = []
    outdated = False
    for meta in careers:
        saved = _file_saved_at(meta.save_path)
        against = saved or _parse_iso(meta.imported_at)
        newer = newest_unimported.get(meta.label)
        is_outdated = bool(newer and against and newer > against)
        outdated = outdated or is_outdated
        status = "imported" + ("  (active)" if meta.profile == active else "")
        if is_outdated:
            status += "  - OUTDATED"
        rows.append({
            "kind": "career", "value": meta.profile, "label": meta.label,
            "profile": meta.profile,
            "status": status,
            "players": f"{meta.player_count:,}",
            "saved": saved,
            # The save file can be gone (deleted, or moved after import). The career is still
            # perfectly usable, so fall back to when it was imported rather than sorting it to
            # the bottom as though it had no date at all.
            "sort_key": saved or _parse_iso(meta.imported_at),
            "in_game": meta.as_of_date or "unknown",
        })
    for save in pending:
        rows.append({
            "kind": "save", "value": save, "label": save.label, "profile": "-",
            "status": f"not imported ({save.game})", "players": "-",
            "saved": save.modified, "sort_key": save.modified, "in_game": "-",
        })

    rows.sort(key=lambda r: r["sort_key"] or datetime.min, reverse=True)

    # With a Saved column, two rows sharing an in-game name are already distinguishable by
    # date, so only qualify with the file name when the dates match too.
    stamps = [f"{r['label']}|{_short(r['saved'])}" for r in rows]
    ambiguous = {s for s, kept in zip(stamps, _disambiguate(stamps)) if kept is None}

    table = Table(title="YOUR SAVES", title_justify="left", header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Save")
    table.add_column("Saved")
    table.add_column("Profile")
    table.add_column("Status")
    table.add_column("Players", justify="right")
    table.add_column("In-game date")

    options: List[tuple] = []  # (kind, value) where kind is "career" or "save"
    for row, stamp in zip(rows, stamps):
        options.append((row["kind"], row["value"]))
        label = row["label"]
        if stamp in ambiguous:
            name = (row["value"].name if row["kind"] == "save"
                    else Path(next(c.save_path for c in careers
                                    if c.profile == row["value"])).name)
            label = f"{label}  ({name})"
        table.add_row(
            str(len(options)), label, _short(row["saved"]), row["profile"],
            row["status"], row["players"], row["in_game"],
        )

    if not options:
        _no_saves_message([])
        return None

    console.print(table)
    typer.echo("")
    typer.echo("\"not imported\" means MMB hasn't read that save file yet - pick it and it will,")
    typer.echo("which takes a few seconds. \"Saved\" is when your game last wrote the file.")
    if outdated:
        typer.echo("")
        typer.echo("OUTDATED: you've saved that career again since MMB read it, so its players are")
        typer.echo("from the older session. Pick the newer save above to bring it up to date.")
    typer.echo("")
    choice = typer.prompt("Which save? (number)", type=int, default=1)
    if not 1 <= choice <= len(options):
        typer.echo(f"Error: {choice} is not one of the listed saves.", err=True)
        return None

    kind, value = options[choice - 1]
    if kind == "career":
        set_active_career(value)
        return value

    typer.echo("")
    typer.echo(f"{value.label} hasn't been imported yet. Parsing it takes a few seconds;")
    typer.echo("after that this career opens instantly.")
    if not typer.confirm("Import it now?", default=True):
        return None
    profile_name = default_profile_name(str(value.path), value.save_name)
    career = _run_extract(str(value.path), None, None)
    save_career(profile_name, career, value.save_name)
    set_active_career(profile_name)
    typer.echo(f"Imported as '{profile_name}'.")
    return profile_name


def _confirm_date(profile: str, career: ParsedCareer) -> ParsedCareer:
    """Ask the user for their career's current date, defaulting to MMB's estimate. This is the
    one number MMB cannot read reliably out of the save, and the user can see it on their own
    screen -- so ask rather than guess, every time a session starts."""
    current = career.as_of_date or ""
    typer.echo("")
    typer.echo("Ages are counted from your career's current date.")
    if current:
        typer.echo(f"MMB estimated {current} from your transfer history - correct it if it's off.")
    answer = typer.prompt("Date in your career (YYYY-MM-DD)", default=current or "").strip()
    if not answer or answer == current:
        return career
    try:
        parsed = date.fromisoformat(answer)
    except ValueError:
        typer.echo(f"{answer!r} isn't a date (expected YYYY-MM-DD) - keeping {current or 'no date'}.")
        return career
    updated = set_as_of_date(profile, parsed)
    typer.echo(f"Dated {parsed.isoformat()}; {updated:,} ages updated.")
    return load_career(profile)


def _guided_filters() -> List[F.PlayerFilter]:
    """Fallback when there's no API key: a short questionnaire covering what people actually
    filter on first. Everything is optional -- a blank answer means 'don't care'."""
    built: List[F.PlayerFilter] = []
    positions = typer.prompt("Position(s), comma separated (blank for any)", default="").strip()
    if positions:
        built.append(F.PositionFilter(tuple(p.strip() for p in positions.split(",") if p.strip())))
    for label, filter_cls in (("Max age", F.AgeFilter), ("Min potential", F.PotentialFilter),
                              ("Min overall", F.OverallFilter), ("Min height in cm", F.HeightFilter)):
        answer = typer.prompt(f"{label} (blank to skip)", default="").strip()
        if not answer:
            continue
        try:
            value = int(answer)
        except ValueError:
            typer.echo(f"  {answer!r} isn't a number - skipping.")
            continue
        if label.startswith("Max"):
            built.append(filter_cls(maximum=value))
        else:
            built.append(filter_cls(minimum=value))
    return built


def _show_results(players: List[Player], attr_columns: List[str], sort_by: str) -> None:
    console.print(_results_table(players[:25], attr_columns))
    typer.echo("")
    typer.echo(f"Matched {len(players):,} players; showing {min(len(players), 25):,}, "
               f"sorted by {sort_by}.")


@app.command()
def scout(
    profile: Optional[str] = typer.Option(
        None, "--profile", help="Skip the save picker and use this career."
    ),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="API key for plain-English search."),
    model: Optional[str] = typer.Option(None, "--model", help="Model for plain-English search."),
    provider: Optional[str] = typer.Option(None, "--provider", help="anthropic or google."),
) -> None:
    """Scout interactively: pick a save, confirm its date, then describe who you want.

    This is the guided version of `mmb saves` + `mmb use` + `mmb date` + `mmb ask`. Every step
    is also available as its own command for scripting.
    """
    try:
        _run_scout(profile=profile, api_key=api_key, model=model, provider=provider)
    except (typer.Abort, KeyboardInterrupt, EOFError):
        # Ctrl-C, Ctrl-D, or a closed stdin. Leaving quietly beats a traceback.
        typer.echo("")
        typer.echo("Bye.")


def _run_scout(profile: Optional[str] = None, api_key: Optional[str] = None,
               model: Optional[str] = None, provider: Optional[str] = None) -> None:
    """The interactive session itself, separate from the Typer command so the no-arguments
    entry point can start it with real values -- `ctx.invoke` would hand the function Typer's
    OptionInfo placeholders instead of None."""
    typer.echo("MANAGER MODE BUDDY")
    typer.echo("")

    if profile:
        career_name = profile
    else:
        chosen = _pick_career_interactively()
        if chosen is None:
            raise typer.Exit(code=1)
        career_name = chosen

    try:
        career = load_career(career_name)
    except CareerNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    career = _confirm_date(career_name, career)
    pool = F.apply(career.players, [F.PlausiblePlayerFilter()])
    typer.echo("")
    typer.echo(f"Searching {Path(career.save_path).name} - {len(pool):,} scoutable players"
               + (f", dated {career.as_of_date}." if career.as_of_date else "."))

    have_key = any(os.environ.get(name) for names in nl.KEY_ENVS.values() for name in names)
    if not (have_key or api_key):
        typer.echo("")
        typer.echo("No API key set, so I'll ask a few questions instead of taking a sentence.")
        typer.echo("(Set GEMINI_API_KEY or ANTHROPIC_API_KEY for plain-English search.)")

    facts = nl.CareerFacts.from_career(career, pool)
    while True:
        typer.echo("")
        if have_key or api_key:
            question = typer.prompt(
                "What kind of player are you looking for? (blank to quit)", default="",
                show_default=False).strip()
            if not question:
                return
            try:
                interpreted = nl.interpret(question, facts, model=model, api_key=api_key,
                                           provider=provider)
            except nl.NLError as exc:
                typer.echo(f"Error: {exc}", err=True)
                continue
            active_filters = interpreted.filters
            sort_by, ascending = interpreted.sort_by, interpreted.ascending
            if interpreted.reading:
                typer.echo(f"Read as: {interpreted.reading}")
            for item in interpreted.unsupported:
                reason = nl.explain_gap(item)
                typer.echo(f"Not applied: {item}" + (f" -- {reason}" if reason else ""))
        else:
            active_filters = _guided_filters()
            sort_by, ascending = "potential", False
            if not active_filters:
                return

        typer.echo("Filters: " + ("; ".join(f.describe() for f in active_filters) or "none"))
        typer.echo("")
        matched = F.sort_players(F.apply(pool, active_filters), key=sort_by, descending=not ascending)
        if not matched:
            typer.echo("No players matched. Every filter is ANDed, and a player whose value for")
            typer.echo("one of them is unknown is excluded -- try asking for less.")
            continue

        attr_columns = list(dict.fromkeys(
            f.attribute for f in active_filters if isinstance(f, F.AttributeFilter)))
        _show_results(matched, attr_columns, sort_by)

        typer.echo("")
        answer = typer.prompt("Player ID for full detail, or blank to search again",
                              default="", show_default=False).strip()
        if answer:
            found = next((p for p in matched if str(p.id) == answer), None)
            if found is None:
                typer.echo(f"{answer} isn't one of these results.")
            else:
                typer.echo("")
                _print_player_detail(found, career)


if __name__ == "__main__":
    app()
