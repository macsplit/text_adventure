"""
Millhaven — a text adventure driven by a local LLM via Ollama.

Usage:
  python init_town.py    # first time only: generates the world
  python main.py         # play the game
"""

import os
import re
import sys
import textwrap
try:
    import readline  # noqa: F401  (enables arrow-key editing and history in input())
except ImportError:
    pass  # not available on this platform (e.g. some Windows builds)
import database as db
import engine
import llm
from config import DB_PATH, NPC_TICK_INTERVAL, GAME_TITLE, PLAYER_START_X, PLAYER_START_Y


WIDTH = 78  # text wrap width


USE_COLOR = sys.stdout.isatty() and os.environ.get('NO_COLOR') is None


class C:
    reset = '\033[0m'
    dim = '\033[2m'
    bold = '\033[1m'
    red = '\033[31m'
    green = '\033[32m'
    yellow = '\033[33m'
    blue = '\033[34m'
    magenta = '\033[35m'
    cyan = '\033[36m'
    white = '\033[37m'
    bright_black = '\033[90m'


def colour(text, *codes):
    if not USE_COLOR or not codes:
        return text
    return ''.join(codes) + str(text) + C.reset


def stat_colour(value, *, higher_bad=True):
    value = int(value)
    danger = value >= 80 if higher_bad else value <= 20
    caution = value >= 60 if higher_bad else value <= 40
    if danger:
        return C.red
    if caution:
        return C.yellow
    return C.green


def wrap(text, indent=""):
    lines = text.split('\n')
    wrapped = []
    for line in lines:
        if line.strip() == '':
            wrapped.append('')
        else:
            wrapped.extend(textwrap.wrap(line, width=WIDTH, initial_indent=indent,
                                         subsequent_indent=indent))
    return '\n'.join(wrapped)


def print_banner():
    print()
    print(colour("=" * WIDTH, C.cyan, C.bold))
    print(colour(f"  {GAME_TITLE.upper()}".center(WIDTH), C.cyan, C.bold))
    print(colour("  A quiet village. Unfamiliar faces. No particular plot.".center(WIDTH), C.dim))
    print(colour("=" * WIDTH, C.cyan, C.bold))
    print()


def print_separator():
    print(colour("-" * WIDTH, C.bright_black))


class _StreamPrinter:
    """Receives LLM tokens, prints them immediately, strips **markup** on the fly."""

    def __init__(self):
        self.started = False
        self._col = 0
        self._star = False  # buffering a lone '*' to check for '**'

    def feed(self, token):
        # Strip **bold** markers, handling splits across token boundaries.
        out = ''
        for ch in token:
            if ch == '*':
                if self._star:
                    self._star = False  # second '*' — discard both
                else:
                    self._star = True   # first '*' — hold
            else:
                if self._star:
                    out += '*'          # lone '*', not a pair — keep it
                    self._star = False
                out += ch
        if not out:
            return
        if not self.started:
            print()
            self.started = True
        for ch in out:
            if ch == '\n':
                print(flush=True)
                self._col = 0
            else:
                print(ch, end='', flush=True)
                self._col += 1
                if self._col >= WIDTH:
                    print(flush=True)
                    self._col = 0


_GENERIC_LOCATION_NAMES = {
    'road', 'path', 'track', 'field', 'ground', 'market', 'park', 'stream',
    'building', 'cellar', 'upstairs', 'stairs', 'wilderness', 'farmland', 'farmyard',
}


def _node_terms(player):
    """Return mechanical world nodes worth highlighting in the current scene.

    Scoped to what's actually present or nearby — people, first-class objects,
    forageables, buildings and the named location — never a blind whole-world
    name match (which would highlight e.g. "mirror" inside "reflecting like a
    mirror" just because an object called "mirror" exists somewhere on the map).
    Environmental objects (ambient texture only) are deliberately excluded.
    """
    if not USE_COLOR or not player:
        return []
    x, y, z = player['x'], player['y'], player.get('z', 0)
    terms = []
    try:
        conn = db.get_conn()

        char_radius = 12
        rows = conn.execute(
            "SELECT name, x, y FROM characters WHERE z=? AND is_alive=1 AND is_player=0 "
            "AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
            (z, x - char_radius, x + char_radius, y - char_radius, y + char_radius)
        ).fetchall()
        for row in rows:
            if row['name'] and abs(row['x'] - x) + abs(row['y'] - y) <= char_radius:
                terms.append((row['name'], C.yellow + C.bold))

        obj_radius = 3
        rows = conn.execute(
            "SELECT name, x, y FROM objects WHERE z=? AND is_visible=1 AND owner_id IS NULL "
            "AND object_type != 'environmental' "
            "AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
            (z, x - obj_radius, x + obj_radius, y - obj_radius, y + obj_radius)
        ).fetchall()
        for row in rows:
            if row['name'] and abs(row['x'] - x) + abs(row['y'] - y) <= obj_radius:
                terms.append((row['name'], C.magenta + C.bold))

        rows = conn.execute(
            "SELECT name FROM objects WHERE owner_id=? AND is_visible=1 AND object_type != 'environmental'",
            (player['id'],)
        ).fetchall()
        for row in rows:
            if row['name']:
                terms.append((row['name'], C.magenta + C.bold))

        loc = db.get_location(x, y, z)
        building_id = loc.get('building_id') if loc else None
        bld_radius = 12
        seen_bld_ids = set()
        if building_id:
            bld = db.get_building(building_id)
            if bld and bld.get('name'):
                seen_bld_ids.add(bld['id'])
                terms.append((bld['name'], C.cyan + C.bold))
        for b in conn.execute("SELECT id, name, x1, y1, x2, y2 FROM buildings WHERE name IS NOT NULL").fetchall():
            if b['id'] in seen_bld_ids:
                continue
            nx = min(max(x, b['x1']), b['x2'])
            ny = min(max(y, b['y1']), b['y2'])
            if abs(nx - x) + abs(ny - y) <= bld_radius:
                seen_bld_ids.add(b['id'])
                terms.append((b['name'], C.cyan + C.bold))

        conn.close()

        if loc and loc.get('name'):
            name = loc['name']
            if name.lower() not in _GENERIC_LOCATION_NAMES and len(name) >= 4:
                terms.append((name, C.blue + C.bold))
    except Exception:
        return []

    # Prefer longest terms first so "Harker's General Store" wins before "Store".
    deduped = {}
    for term, code in terms:
        term = str(term).strip()
        if len(term) < 3:
            continue
        deduped.setdefault(term.lower(), (term, code))
    return sorted(deduped.values(), key=lambda pair: len(pair[0]), reverse=True)


def _highlight_nodes(line, terms, base_code=C.white):
    if not USE_COLOR or not line or '\033[' in line:
        return line
    highlighted = line
    for term, code in terms:
        pattern = re.compile(rf"(?<![\w'])({re.escape(term)})(?![\w'])", re.IGNORECASE)
        highlighted = pattern.sub(
            lambda m: f"{code}{m.group(1)}{C.reset}{base_code}",
            highlighted,
        )
    return highlighted


# The narrator is asked (see llm.py system prompts) to wrap its first mention
# of any mechanically-present person/object/building/forageable in **asterisks**
# — this is the primary highlight signal, since the LLM knows what it actually
# meant better than any after-the-fact name match could. We strip the markers
# and collect the names *before* wrapping (so a span split across two wrapped
# lines can never cause a spurious cross-span match), then highlight the
# plain text afterward exactly as with the scoped fallback terms.
_MARKUP_RE = re.compile(r'\*\*([^*\n]+?)\*\*')
_TERM_SPACE = '\ue000'


def _extract_markup(text):
    """Strip **markup** the LLM used to flag mechanical mentions; return the
    plain text plus the (deduped, in order) list of names it marked."""
    marked = []
    seen = set()

    def repl(m):
        term = m.group(1).strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            marked.append(term)
        return m.group(1)

    return _MARKUP_RE.sub(repl, text), marked


def _protect_term_wraps(line, terms):
    """Keep multi-word mechanical terms from being split by text wrapping."""
    if not USE_COLOR or not line or '\033[' in line:
        return line
    protected = line
    for term, _ in terms:
        if ' ' not in term:
            continue
        pattern = re.compile(rf"(?<![\w'])({re.escape(term)})(?![\w'])", re.IGNORECASE)
        protected = pattern.sub(lambda m: m.group(1).replace(' ', _TERM_SPACE), protected)
    return protected


def print_output(text, player=None):
    print()
    plain_text, marked = _extract_markup(text)
    scoped_terms = _node_terms(player)
    terms_lookup = {term.lower(): code for term, code in scoped_terms}
    # Names the narrator itself flagged take precedence — but only highlight
    # ones we can confirm are actually present (the model occasionally marks
    # sound effects like "crack"/"rustle" rather than real entities; those
    # just lose their asterisks and print as plain text).
    marked_terms = [(term, terms_lookup[term.lower()])
                    for term in marked if term.lower() in terms_lookup]
    marked_lower = {term.lower() for term, _ in marked_terms}
    fallback_terms = [pair for pair in scoped_terms if pair[0].lower() not in marked_lower]
    terms = marked_terms + fallback_terms

    lines = plain_text.split('\n')
    out_lines = []
    for line in lines:
        if '\033[' in line:
            # ANSI art — print verbatim, no wrapping or colour injection
            out_lines.append(line)
        elif line.strip() == '':
            out_lines.append('')
        else:
            line = _protect_term_wraps(line, terms)
            out_lines.extend(
                textwrap.wrap(line, width=WIDTH) or ['']
            )

    # Wrap the non-ANSI portions in white, but emit ANSI art lines raw
    for line in out_lines:
        line = line.replace(_TERM_SPACE, ' ')
        if '\033[' in line:
            print(line + C.reset)
        else:
            print(C.white + _highlight_nodes(line, terms, C.white) + C.reset if USE_COLOR else line)
    print()


def check_prerequisites():
    """Verify DB exists and Ollama is reachable."""
    if not os.path.exists(DB_PATH):
        print(f"No world database found at '{DB_PATH}'.")
        print("Run:  python init_town.py")
        sys.exit(1)

    # Existing saves may predate additive schema changes.
    db.init_schema()
    db.map_sprites()

    initialized = db.get_state('initialized')
    if initialized != 'true':
        print("World database exists but appears incomplete.")
        print("Run:  python init_town.py")
        sys.exit(1)

    print("Initialising LLM backend...")
    ok, status = llm.init_backend()
    if ok:
        print(colour(f"  {status}", C.green, C.bold))
    else:
        print(colour(f"  {status}", C.red, C.bold))
        choice = input("\nContinue anyway (LLM features will use fallbacks)? [y/N] ")
        if choice.lower() != 'y':
            sys.exit(1)


def create_player():
    """Prompt for player name, create player character in DB."""
    existing = db.get_player()
    if existing:
        if existing['name'] == 'Test Player':
            print_separator()
            print("Welcome back, stranger.")
            print()
            name = input("What is your name? ").strip()
            if name:
                db.update_character(existing['id'], name=name)
                existing = db.get_player()
        else:
            print(colour(f"Welcome back, {existing['name']}.", C.cyan))
        return existing

    print_separator()
    print(colour("You arrive in Millhaven as a stranger.", C.cyan))
    print()
    name = input("What is your name? ").strip()
    if not name:
        name = "Stranger"

    cid = db.insert_character({
        'name': name,
        'age': 30,
        'gender': 'unknown',
        'occupation': 'traveller',
        'personality': 'curious, cautious',
        'x': PLAYER_START_X,
        'y': PLAYER_START_Y,
        'z': 0,
        'health': 100,
        'hunger': 10,
        'thirst': 5,
        'energy': 90,
        'warmth': 60,
        'money': 40,
        'mood': 'uncertain',
        'is_player': 1,
        'backstory': 'A stranger who has just arrived in Millhaven.',
    })
    return db.get_character(cid)


def game_loop(player):
    turn = 0
    print_separator()
    print(f"  Time: {engine._format_time()}   Location: ({player['x']},{player['y']})")
    print_separator()

    # Opening look
    opening = engine.action_look(player, {})
    print_output(opening, player)

    while True:
        # Prompt
        try:
            raw = input(colour(f"\n[{player['name']}]> ", C.green, C.bold)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nFarewell.")
            break

        if not raw:
            continue

        # Quit commands
        if raw.lower() in ('quit', 'exit', 'q', 'exit game', 'bye'):
            print("\nYou leave Millhaven the way you came. Farewell.")
            break

        # Advance game tick
        ticks = int(db.get_state('game_ticks', 0))
        db.set_state('game_ticks', ticks + 1)
        turn += 1

        # Show separator immediately; begin streaming narrative tokens to terminal.
        print_separator()
        streamer = _StreamPrinter() if USE_COLOR else None
        if streamer:
            llm.set_narrative_stream(streamer.feed)
            print('\033[s', end='', flush=True)  # save cursor position

        # Process player action
        result = engine.process_input(raw, player)

        if streamer:
            llm.clear_narrative_stream()

        # Refresh player state from DB (action handlers may have updated it)
        player = db.get_player()

        # If streaming produced visible output, wipe it and reprint with full
        # entity highlighting via print_output().
        if streamer and streamer.started:
            print('\033[u\033[0J', end='', flush=True)

        if result == '__LEAVE_GAME__':
            print(colour(wrap("You continue on your travels, leaving Millhaven behind you. "
                              "You will never return."), C.yellow, C.bold))
            print()
            print(colour("  -- GAME OVER --", C.red, C.bold))
            print()
            break
        print_output(result, player)

        # Biological needs tick
        warnings = engine.tick_needs(player['id'])
        game_over = False
        if warnings:
            for w in warnings:
                if w == '__GAME_OVER__':
                    game_over = True
                elif w.startswith('__THOUGHT__:'):
                    thought = w[len('__THOUGHT__:'):]
                    print(colour(f"  {thought}", C.magenta))
                else:
                    print(colour(f"  ! {w}", C.yellow, C.bold))
            print()
        if game_over:
            print()
            print(colour(wrap("Your body gives out entirely. The world dims and fades. "
                              "You will not rise again."), C.red, C.bold))
            print()
            print(colour("  -- GAME OVER --", C.red, C.bold))
            print()
            break

        # NPC tick
        if turn % NPC_TICK_INTERVAL == 0:
            speech_events = engine.tick_npcs()
            for ev in speech_events:
                name = ev['speaker']
                target = ev['directed_at']
                speech = ev['speech']
                if target == 'self':
                    print(colour(f"  {name} mutters: \"{speech}\"", C.green))
                elif target == 'player':
                    print(colour(f"  {name} says to you: \"{speech}\"", C.green, C.bold))
                else:
                    # Capitalise first letter of target name for display
                    print(colour(f"  {name} says to {target.capitalize()}: \"{speech}\"", C.green))
            if speech_events:
                print()

        # Status line
        p = db.get_character(player['id'])
        print("  "
              + colour(f"Time: {engine._format_time()}", C.cyan)
              + "   "
              + colour(f"Location: ({p['x']},{p['y']},z={p['z']})", C.magenta)
              + "   "
              + colour(f"Money: {p['money']}p", C.yellow))
        print("  "
              + colour(f"Health: {p['health']}/100", stat_colour(p['health'], higher_bad=False))
              + "   "
              + colour(f"Hunger: {p['hunger']}/100", stat_colour(p['hunger']))
              + "   "
              + colour(f"Thirst: {p.get('thirst', 30)}/100", stat_colour(p.get('thirst', 30)))
              + "   "
              + colour(f"Energy: {p['energy']}/100", stat_colour(p['energy'], higher_bad=False)))
        print_separator()


def main():
    print_banner()
    check_prerequisites()
    player = create_player()
    print()
    print(colour("Type 'help' for a list of commands.", C.dim))
    print(colour("Type 'quit' to exit.", C.dim))
    print()
    game_loop(player)


if __name__ == '__main__':
    main()
