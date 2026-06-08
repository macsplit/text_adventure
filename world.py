"""
World model: grid queries, location descriptions, movement validation.
Millhaven is a 100x100 grid. z=0 is ground level; z=1 upstairs; z=-1 basement.
"""
import sqlite3
import database as db
import areas
import knowledge
from config import GRID_WIDTH, GRID_HEIGHT, DB_PATH

# Randomly-generated world texture (see init_town.make_environmental_objects).
# These are tile-local by design — never surfaced in distance-based scans,
# only in the exact-tile "Here:"/"Present here:" listing.
_TILE_LOCAL_TYPES = frozenset({'environmental', 'forageable'})


def nearest_building_tile(px, py, building_id, z=0):
    """Return (x, y) of the locations tile for building_id nearest to (px, py).

    Uses the actual locations table rather than the stored entrance_x/entrance_y,
    so it is correct even when the stored entrance falls inside a different building.
    Returns None if the building has no mapped tiles.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    tiles = conn.execute(
        "SELECT x, y FROM locations WHERE building_id=? AND z=?",
        (building_id, z)
    ).fetchall()
    conn.close()
    if not tiles:
        return None
    best = min(tiles, key=lambda t: abs(t['x'] - px) + abs(t['y'] - py))
    return (best['x'], best['y'])

DIRECTIONS = {
    'north': (0, -1, 0),
    'n':     (0, -1, 0),
    'south': (0,  1, 0),
    's':     (0,  1, 0),
    'east':  (1,  0, 0),
    'e':     (1,  0, 0),
    'west':  (-1, 0, 0),
    'w':     (-1, 0, 0),
    'up':         (0,  0, 1),
    'u':          (0,  0, 1),
    'upstairs':   (0,  0, 1),
    'down':       (0,  0, -1),
    'd':          (0,  0, -1),
    'downstairs': (0,  0, -1),
    'northeast': (1, -1, 0),
    'ne':        (1, -1, 0),
    'northwest': (-1, -1, 0),
    'nw':        (-1, -1, 0),
    'southeast': (1,  1, 0),
    'se':        (1,  1, 0),
    'southwest': (-1,  1, 0),
    'sw':        (-1,  1, 0),
}

TERRAIN_DESCRIPTIONS = {
    'road':      'a cobbled road',
    'track':     'a dirt track',
    'building':  'the interior of a building',
    'field':     'an open grassy field',
    'wilderness': 'wild uncultivated land',
    'farmland':  'cultivated farmland',
    'farmyard':  'a muddy farmyard',
    'market':    'the open market square',
    'park':      'a small public green',
    'stream':    'the bank of a shallow stream',
    'path':      'a worn footpath',
    'stairs':    'a staircase',
    'cellar':    'a low-ceilinged cellar',
    'upstairs':  'an upper-floor room',
    'ground':    'open ground',
}


def resolve_direction(direction_str):
    return DIRECTIONS.get(direction_str.lower())


def can_move_to(x, y, z=0):
    if x < 0 or x >= GRID_WIDTH or y < 0 or y >= GRID_HEIGHT:
        return False, "That direction leads off the edge of the world."
    loc = db.get_location(x, y, z)
    if loc is None:
        return False, "There is nothing that way — you cannot go there."
    if not loc.get('is_enterable', 1):
        return False, "You cannot enter there."
    return True, None


def can_move_between(x, y, z, nx, ny, nz):
    ok, reason = can_move_to(nx, ny, nz)
    if not ok:
        return ok, reason
    old_loc = db.get_location(x, y, z)
    new_loc = db.get_location(nx, ny, nz)
    boundary = areas.boundary_reason(old_loc, new_loc)
    if boundary:
        return False, boundary
    return True, None


def get_location_summary(x, y, z=0):
    loc = db.get_location(x, y, z)
    if not loc:
        return f"({x},{y}) — unmapped terrain"

    terrain = loc.get('terrain', 'ground')
    name = loc.get('name') or TERRAIN_DESCRIPTIONS.get(terrain, 'a place')

    building_id = loc.get('building_id')
    building_str = ''
    if building_id:
        bld = db.get_building(building_id)
        if bld:
            building_str = f" inside {bld['name']}"

    desc = loc.get('description') or ''
    return f"{name}{building_str}. {desc}".strip()


def describe_surroundings(x, y, z=0, radius=1):
    """Return a short text description of visible surroundings."""
    loc = db.get_location(x, y, z)
    indoor = areas.is_indoors(loc)
    chars = [c for c in db.get_characters_at(x, y, z) if not c.get('is_player')]
    # 'environmental' objects are pure ambient texture — never itemised as
    # if they were tangible things you could pick up; the LLM gets them
    # separately (see engine.build_context) so it may weave them into prose.
    objs = [o for o in db.get_objects_at(x, y, z) if o.get('object_type') != 'environmental']

    nearby_chars = []
    nearby_objs = []

    if indoor:
        area_chars = areas.characters_in_area(x, y, z, max_distance=8)
        for c in area_chars:
            if c['_distance'] == 0:
                continue
            name = knowledge.npc_display_name(c, c['_distance'], same_area=True)
            if c['_distance'] <= 3:
                nearby_chars.append(f"{name} ({_relative_dir(c['x'] - x, c['y'] - y)})")
        area_objs = areas.objects_in_area(x, y, z, max_distance=4)
        for o in area_objs:
            if o['_distance'] == 0:
                continue
            nearby_objs.append(f"{o['name']} ({_relative_dir(o['x'] - x, o['y'] - y)})")

    # Include radius if radius > 0. Indoors, do not bleed across building walls.
    elif radius > 0:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                nearby_loc = db.get_location(nx, ny, z)
                if nearby_loc and nearby_loc.get('building_id'):
                    continue
                for c in db.get_characters_at(nx, ny, z):
                    if c.get('is_player'):
                        continue
                    dir_hint = _relative_dir(dx, dy)
                    nearby_chars.append(f"{knowledge.npc_display_name(c, abs(dx) + abs(dy), same_area=False)} ({dir_hint})")
                for o in db.get_objects_at(nx, ny, z):
                    if o.get('object_type') in _TILE_LOCAL_TYPES:
                        continue
                    dir_hint = _relative_dir(dx, dy)
                    nearby_objs.append(f"{o['name']} ({dir_hint})")

    parts = []

    if chars:
        here = [knowledge.npc_display_name(c, 0, same_area=True) for c in chars]
        parts.append("Here with you: " + ", ".join(here) + ".")

    if nearby_chars:
        parts.append("Nearby: " + ", ".join(nearby_chars[:6]) + ".")

    if objs:
        here_o = [o['name'] for o in objs]
        terrain = (loc.get('terrain') or '') if loc else ''
        label = "Present here" if terrain in ('building', 'upstairs', 'cellar') else "Here"
        parts.append(label + ": " + ", ".join(here_o) + ".")

    if nearby_objs:
        parts.append("Visible nearby: " + ", ".join(nearby_objs[:8]) + ".")

    exits = _describe_exits(x, y, z)
    if exits:
        parts.append("Exits: " + exits + ".")

    # Show nearby named buildings (non-residential) at ground level
    if z == 0:
        _LANDMARK_TYPES = {'inn', 'bakery', 'shop', 'smithy', 'church', 'civic',
                           'medical', 'school', 'post', 'police', 'market', 'farm'}
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        blds = conn.execute("SELECT id, name, building_type FROM buildings").fetchall()
        conn.close()
        nearby_blds = []
        for b in blds:
            if b['building_type'] not in _LANDMARK_TYPES:
                continue
            # Skip the building the player is currently inside
            cur_loc = db.get_location(x, y, z)
            if cur_loc and cur_loc.get('building_id') == b['id']:
                continue
            tile = nearest_building_tile(x, y, b['id'], z)
            if not tile:
                continue
            dx = tile[0] - x
            dy = tile[1] - y
            dist = abs(dx) + abs(dy)
            if 1 <= dist <= 12:
                nearby_blds.append((dist, dict(b), _relative_dir(dx, dy)))
        nearby_blds.sort(key=lambda item: (item[0], item[1]['name']))
        if nearby_blds:
            bld_parts = [
                f"{knowledge.building_display_name(b, dist)} ({direction}, {dist} steps)"
                for dist, b, direction in nearby_blds[:4]
            ]
            label = "Outside nearby" if indoor else "Nearby buildings"
            parts.append(label + ": " + "; ".join(bld_parts) + ".")

    return ' '.join(parts) if parts else "Nothing notable catches your eye."


def _relative_dir(dx, dy):
    if dx == 0 and dy < 0: return "north"
    if dx == 0 and dy > 0: return "south"
    if dx > 0 and dy == 0: return "east"
    if dx < 0 and dy == 0: return "west"
    if dx > 0 and dy < 0: return "northeast"
    if dx < 0 and dy < 0: return "northwest"
    if dx > 0 and dy > 0: return "southeast"
    if dx < 0 and dy > 0: return "southwest"
    return "nearby"


def _describe_exits(x, y, z):
    loc = db.get_location(x, y, z)
    if loc and loc.get('building_id'):
        bld = db.get_building(loc['building_id'])
        exits = areas.building_exit_directions(bld) if z == 0 else []
        if loc.get('terrain') in ('building', 'cellar', 'upstairs', 'stairs'):
            ok_up, _ = can_move_to(x, y, z + 1)
            ok_dn, _ = can_move_to(x, y, z - 1)
            if ok_up:
                exits.append('up')
            if ok_dn:
                exits.append('down')
        return ', '.join(exits)

    cardinal = [('north', 0, -1, 0), ('south', 0, 1, 0),
                ('east', 1, 0, 0), ('west', -1, 0, 0)]
    exits = []
    for name, dx, dy, dz in cardinal:
        ok, _ = can_move_between(x, y, z, x + dx, y + dy, z + dz)
        if ok:
            exits.append(name)
    if loc and loc.get('terrain') in ('building', 'cellar', 'upstairs', 'stairs'):
        ok_up, _ = can_move_to(x, y, z + 1)
        ok_dn, _ = can_move_to(x, y, z - 1)
        if ok_up:
            exits.append('up')
        if ok_dn:
            exits.append('down')
    return ', '.join(exits)
