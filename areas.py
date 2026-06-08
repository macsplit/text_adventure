"""
Human-scale interaction areas over the fine simulation grid.

The grid remains the source of truth for simulation. These helpers answer
player-facing questions such as "are these two tiles in the same room/building?"
and "would this step leak through a wall into another building?"
"""
import sqlite3

import database as db
from config import DB_PATH

INDOOR_TERRAINS = {'building', 'upstairs', 'cellar', 'stairs'}
OUTDOOR_AREA_TERRAINS = {'market', 'park', 'farmyard'}
OUTSIDE_TERRAINS = {'road', 'path', 'market', 'field', 'track', 'ground', 'park',
                    'farmland', 'farmyard', 'wilderness', 'stream'}
CARDINAL_DELTAS = {
    'north': (0, -1),
    'south': (0, 1),
    'east': (1, 0),
    'west': (-1, 0),
}


def area_key_for_location(loc):
    """Return a coarse interaction-area key for a DB location row."""
    if not loc:
        return None
    building_id = loc.get('building_id')
    if building_id:
        return f"building:{building_id}:z:{loc.get('z', 0)}"
    terrain = loc.get('terrain') or 'ground'
    name = loc.get('name') or ''
    if terrain in OUTDOOR_AREA_TERRAINS and name:
        return f"outdoor:{terrain}:{name.lower()}"
    return f"tile:{loc.get('x')}:{loc.get('y')}:{loc.get('z', 0)}"


def area_key_at(x, y, z=0):
    return area_key_for_location(db.get_location(x, y, z))


def same_area(a_loc, b_loc):
    """True when two locations should behave as one player-facing area."""
    return area_key_for_location(a_loc) == area_key_for_location(b_loc)


def same_area_at(ax, ay, az, bx, by, bz):
    return same_area(db.get_location(ax, ay, az), db.get_location(bx, by, bz))


def is_indoors(loc):
    return bool(loc and (loc.get('building_id') or loc.get('terrain') in INDOOR_TERRAINS))


def boundary_reason(old_loc, new_loc):
    """Return a player-facing reason if movement crosses an invalid building boundary."""
    old_bid = old_loc.get('building_id') if old_loc else None
    new_bid = new_loc.get('building_id') if new_loc else None
    if old_bid and new_bid and old_bid != new_bid:
        old_b = db.get_building(old_bid)
        new_b = db.get_building(new_bid)
        old_name = old_b['name'] if old_b else 'this building'
        new_name = new_b['name'] if new_b else 'the next building'
        return (f"A wall separates {old_name} from {new_name}. "
                f"You will need to go outside and use a proper entrance.")
    return None


def building_exit_tile(building, direction=None):
    """Return (x, y, direction) for a known outside tile beside the entrance."""
    if not building:
        return None
    directions = CARDINAL_DELTAS.items()
    if direction:
        directions = [(name, delta) for name, delta in directions
                      if name.startswith(direction[:1].lower())]
    for name, (dx, dy) in directions:
        nx = building['entrance_x'] + dx
        ny = building['entrance_y'] + dy
        loc = db.get_location(nx, ny, 0)
        if not loc:
            continue
        if loc.get('building_id'):
            continue
        if loc.get('is_enterable', 1) and loc.get('terrain') in OUTSIDE_TERRAINS:
            return nx, ny, name
    return None


def building_exit_directions(building):
    """Return known player-facing exit directions for a building entrance."""
    exits = []
    for name in CARDINAL_DELTAS:
        if building_exit_tile(building, name):
            exits.append(name)
    return exits


def nearby_building_entrance(x, y, z=0, target=None):
    """Return a building whose entrance is at or adjacent to the player."""
    if z != 0:
        return None
    target_l = (target or '').lower().strip()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM buildings").fetchall()
    conn.close()
    candidates = []
    for row in rows:
        bld = dict(row)
        if target_l and target_l not in bld['name'].lower() and target_l not in bld['building_type'].lower():
            continue
        dist = abs(bld['entrance_x'] - x) + abs(bld['entrance_y'] - y)
        if dist <= 1:
            candidates.append((dist, bld['name'], bld))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2] if candidates else None


def building_named(target):
    target_l = (target or '').lower().strip()
    if not target_l:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM buildings WHERE LOWER(name) LIKE ? OR LOWER(building_type) LIKE ?",
        (f"%{target_l}%", f"%{target_l}%"),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def characters_in_area(x, y, z=0, max_distance=8):
    """Return living NPCs in the current interaction area, nearest first."""
    current = db.get_location(x, y, z)
    key = area_key_for_location(current)
    if not key:
        return []

    chars = []
    for c in db.get_all_npcs():
        if c.get('z', 0) != z:
            continue
        loc = db.get_location(c['x'], c['y'], c.get('z', 0))
        if area_key_for_location(loc) != key:
            continue
        dist = abs(c['x'] - x) + abs(c['y'] - y)
        if dist <= max_distance:
            c = dict(c)
            c['_distance'] = dist
            chars.append(c)
    chars.sort(key=lambda c: (c['_distance'], c.get('name') or ''))
    return chars


def objects_in_area(x, y, z=0, max_distance=4):
    """Return visible objects in the current interaction area, nearest first."""
    current = db.get_location(x, y, z)
    key = area_key_for_location(current)
    if not key:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT o.*, l.building_id, l.terrain, l.name AS location_name
        FROM objects o
        JOIN locations l ON o.x=l.x AND o.y=l.y AND o.z=l.z
        WHERE o.owner_id IS NULL AND o.is_visible=1 AND o.z=?
    """, (z,)).fetchall()
    conn.close()

    objs = []
    for row in rows:
        obj = dict(row)
        loc = {
            'x': obj['x'], 'y': obj['y'], 'z': obj['z'],
            'building_id': obj.get('building_id'),
            'terrain': obj.get('terrain'),
            'name': obj.get('location_name'),
        }
        if area_key_for_location(loc) != key:
            continue
        dist = abs(obj['x'] - x) + abs(obj['y'] - y)
        if dist <= max_distance:
            obj['_distance'] = dist
            objs.append(obj)
    objs.sort(key=lambda o: (o['_distance'], o.get('name') or ''))
    return objs
