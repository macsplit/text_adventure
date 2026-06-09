"""
Deterministic local mini-map renderer for Millhaven.

Terrain is shown with ANSI background colours. Foreground symbols indicate
things occupying a tile: player, people, objects, or blocked/unmapped cells.
"""
import os
import sys

import database as db


USE_COLOR = sys.stdout.isatty() and os.environ.get('NO_COLOR') is None


class C:
    reset = '\033[0m'
    bold = '\033[1m'
    black = '\033[30m'
    white = '\033[37m'
    bright_white = '\033[97m'
    yellow = '\033[33m'
    cyan = '\033[36m'
    magenta = '\033[35m'
    bright_black = '\033[90m'


def _fg256(code):
    return f'\033[38;5;{code}m'


def _bg256(code):
    return f'\033[48;5;{code}m'


# Brickish/greyish 256-colour shades used to tint individual buildings so that
# adjacent ones stand apart on the map. Picked from the same warm-brown/grey
# family as the default 'building' terrain colour so the map keeps a
# coherent palette while still letting neighbours read as distinct structures.
_BUILDING_SHADES = [144, 136, 101, 59, 103, 180, 181, 144, 137, 180, 187, 179]
BUILDING_BG = [_bg256(code) for code in _BUILDING_SHADES]


_ADJACENCY_GAP = 2  # buildings within this many tiles are "neighbours" for colouring

_building_colour_cache = None


def _footprint_gap(a, b):
    """Chebyshev gap between two buildings' tile rectangles (0 = touching/overlapping)."""
    dx = max(a['x1'] - b['x2'], b['x1'] - a['x2'], 0)
    dy = max(a['y1'] - b['y2'], b['y1'] - a['y2'], 0)
    return max(dx, dy)


def _compute_building_colours():
    """Assign each building a palette index such that any two buildings close
    enough to appear side-by-side on the map always get different shades.

    Greedy graph colouring over a fixed neighbour relation (footprints within
    _ADJACENCY_GAP tiles), processed in id order — deterministic, so the
    result is identical every time it's computed (and cached for the
    process lifetime so it never shifts between renders)."""
    rows = db.get_conn().execute(
        "SELECT id, x1, y1, x2, y2 FROM buildings ORDER BY id"
    ).fetchall()
    buildings = [dict(r) for r in rows]

    neighbours = {b['id']: set() for b in buildings}
    for i, a in enumerate(buildings):
        for b in buildings[i + 1:]:
            if _footprint_gap(a, b) <= _ADJACENCY_GAP:
                neighbours[a['id']].add(b['id'])
                neighbours[b['id']].add(a['id'])

    colours = {}
    for b in buildings:
        used = {colours[n] for n in neighbours[b['id']] if n in colours}
        for index in range(len(BUILDING_BG)):
            if index not in used:
                colours[b['id']] = index
                break
        else:
            colours[b['id']] = b['id'] % len(BUILDING_BG)
    return colours


def _building_bg(building_id):
    """Stable-per-building shade — same building always renders the same
    colour, and neighbouring buildings are guaranteed distinct shades."""
    global _building_colour_cache
    if building_id is None:
        return TERRAIN_BG['building']
    if _building_colour_cache is None:
        _building_colour_cache = _compute_building_colours()
    index = _building_colour_cache.get(building_id, building_id % len(BUILDING_BG))
    return BUILDING_BG[index]


TERRAIN_BG = {
    'road': _bg256(247),
    'track': _bg256(180),
    'path': _bg256(139),
    'building': _bg256(144),
    'upstairs': _bg256(137),
    'cellar': _bg256(238),
    'field': _bg256(149),
    'farmland': _bg256(107),
    'farmyard': _bg256(95),
    'wilderness': _bg256(107),
    'market': _bg256(186),
    'park': _bg256(107),
    'stream': _bg256(115),
    'stairs': _bg256(178),
    'ground': _bg256(65),
}

TERRAIN_FALLBACK = {
    'road': '─',
    'track': '⋅',
    'path': '·',
    'building': '▣',
    'upstairs': '▤',
    'cellar': '▧',
    'field': '░',
    'farmland': '▒',
    'farmyard': '▪',
    'wilderness': '♣',
    'market': '▪',
    'park': '♧',
    'stream': '≈',
    'stairs': '↕',
    'ground': '░',
}


def _colour_cell(symbol, fg, bg=None, *, bold=False, use_color=True):
    """Render one map cell with optional ANSI foreground/background."""
    symbol = str(symbol)
    text = symbol
    if not use_color:
        return text
    prefix = ''
    if bold:
        prefix += C.bold
    if bg:
        prefix += bg
    if fg:
        prefix += fg
    return f"{prefix}{text}{C.reset}"


def _colour_text(text, fg, *, bold=False, use_color=True):
    if not use_color:
        return str(text)
    prefix = C.bold if bold else ''
    return f"{prefix}{fg}{text}{C.reset}"


def _terrain_bg(loc):
    if loc is None or not loc.get('is_enterable', 1):
        return _bg256(235)
    if loc.get('terrain') == 'building' and loc.get('building_id') is not None:
        return _building_bg(loc['building_id'])
    return TERRAIN_BG.get(loc.get('terrain'), TERRAIN_BG['ground'])


def _terrain_symbol(loc):
    if loc is None or not loc.get('is_enterable', 1):
        return '×'
    return TERRAIN_FALLBACK.get(loc.get('terrain'), TERRAIN_FALLBACK['ground'])


def _base_cell(loc, use_color=True):
    if use_color:
        return _colour_cell(' ', None, _terrain_bg(loc), use_color=True)
    return _terrain_symbol(loc)


def _terrain_legend_cell(terrain, use_color=True):
    if use_color:
        return _colour_cell(' ', None, TERRAIN_BG[terrain], use_color=True)
    return TERRAIN_FALLBACK[terrain]


def _distance_from_player(row, px, py):
    return abs(row['x'] - px) + abs(row['y'] - py)


def render_minimap(player, radius=5, use_color=None, style=None, width_radius=15, height_radius=4):
    """Return a local map centred on the player."""
    del style  # retained for command compatibility; the map now has one style.
    if use_color is None:
        use_color = USE_COLOR

    px, py, pz = player['x'], player['y'], player.get('z', 0)
    if width_radius is None:
        width_radius = radius
    if height_radius is None:
        height_radius = radius

    locations = {
        (loc['x'], loc['y']): loc
        for loc in db.get_locations_in_radius(px, py, pz, max(width_radius, height_radius))
        if px - width_radius <= loc['x'] <= px + width_radius
        and py - height_radius <= loc['y'] <= py + height_radius
    }

    npc_rows = []
    for dy in range(-height_radius, height_radius + 1):
        for dx in range(-width_radius, width_radius + 1):
            x, y = px + dx, py + dy
            for char in db.get_characters_at(x, y, pz):
                if not char.get('is_player'):
                    npc_rows.append(char)
    npc_rows.sort(key=lambda row: (_distance_from_player(row, px, py), row['name']))

    npc_labels = {}
    legend_people = []
    label_num = 1
    for npc in npc_rows:
        pos = (npc['x'], npc['y'])
        if pos in npc_labels:
            continue
        label = str(label_num)
        npc_labels[pos] = label
        label_num += 1
        people_here = [row for row in npc_rows if (row['x'], row['y']) == pos]
        names = []
        for row in people_here[:3]:
            name = _colour_text(row['name'], C.yellow, bold=True, use_color=use_color)
            occ = f", {row['occupation']}" if row.get('occupation') else ''
            names.append(f"{name}{occ}")
        if len(people_here) > 3:
            names.append(f"+{len(people_here) - 3} more")
        label_text = _colour_text(label, C.cyan, bold=True, use_color=use_color)
        legend_people.append(f"{label_text} " + "; ".join(names))
        if len(npc_labels) >= 9:
            break

    object_tiles = {}
    for dy in range(-height_radius, height_radius + 1):
        for dx in range(-width_radius, width_radius + 1):
            x, y = px + dx, py + dy
            objects = [o for o in db.get_objects_at(x, y, pz)
                       if o.get('object_type') not in ('environmental', 'forageable')]
            if objects:
                object_tiles[(x, y)] = objects

    lines = []
    lines.append("Local map")
    map_columns = width_radius * 2 + 1
    north_indent = 2 + (map_columns // 2)
    lines.append(" " * north_indent + "N")
    for y in range(py - height_radius, py + height_radius + 1):
        row_cells = []
        for x in range(px - width_radius, px + width_radius + 1):
            pos = (x, y)
            loc = locations.get(pos)
            bg = _terrain_bg(loc)
            if x == px and y == py:
                cell = _colour_cell('✚', C.bright_white, bg, bold=True, use_color=use_color)
            elif pos in npc_labels:
                cell = _colour_cell(npc_labels[pos], _fg256(58), bg, bold=True, use_color=use_color)
            elif pos in object_tiles:
                symbol = '◈' if len(object_tiles[pos]) > 1 else '◆'
                cell = _colour_cell(symbol, _fg256(59), bg, bold=True, use_color=use_color)
            elif loc is None or not loc.get('is_enterable', 1):
                cell = _colour_cell('×', C.bright_black, bg, bold=True, use_color=use_color)
            else:
                cell = _base_cell(loc, use_color=use_color)
            row_cells.append(cell)
        prefix = "W " if y == py else "  "
        suffix = " E" if y == py else ""
        lines.append(prefix + "".join(row_cells) + suffix)
    lines.append(" " * north_indent + "S")

    legend = [
        _colour_cell('✚', C.bright_white, None, bold=True, use_color=use_color)
        + " " + _colour_text("You", C.bright_white, bold=True, use_color=use_color),
        _colour_text("1-9", C.yellow, bold=True, use_color=use_color)
        + " " + _colour_text("people", C.yellow, bold=True, use_color=use_color),
        _colour_cell('◆', C.magenta, None, bold=True, use_color=use_color)
        + " " + _colour_text("object", C.magenta, bold=True, use_color=use_color),
        _colour_cell('◈', C.magenta, None, bold=True, use_color=use_color)
        + " " + _colour_text("objects", C.magenta, bold=True, use_color=use_color),
    ]
    terrain_legend = [
        _terrain_legend_cell('ground', use_color=use_color) + " ground",
        _terrain_legend_cell('road', use_color=use_color) + " road",
        _terrain_legend_cell('stream', use_color=use_color) + " water",
        _terrain_legend_cell('building', use_color=use_color) + " building",
        _terrain_legend_cell('field', use_color=use_color) + " field",
        _terrain_legend_cell('farmyard', use_color=use_color) + " farmyard",
        _terrain_legend_cell('market', use_color=use_color) + " market",
    ]
    lines.append("Legend: " + "   ".join(legend))
    lines.append("Terrain: " + "   ".join(terrain_legend))
    if legend_people:
        lines.append("People: " + "   ".join(legend_people))
    else:
        lines.append("People: none visible on the map.")
    if pz != 0:
        lines.append(f"Floor: z={pz}")

    return "\n".join(lines)
