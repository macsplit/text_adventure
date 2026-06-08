import sqlite3
import json
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema():
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        z INTEGER NOT NULL DEFAULT 0,
        name TEXT,
        terrain TEXT NOT NULL DEFAULT 'ground',
        building_id INTEGER,
        description TEXT,
        is_enterable INTEGER DEFAULT 1,
        UNIQUE(x, y, z)
    );

    CREATE TABLE IF NOT EXISTS buildings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        building_type TEXT NOT NULL,
        x1 INTEGER, y1 INTEGER,
        x2 INTEGER, y2 INTEGER,
        entrance_x INTEGER, entrance_y INTEGER,
        owner_id INTEGER,
        description TEXT,
        floor_count INTEGER DEFAULT 1,
        has_basement INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        age INTEGER,
        gender TEXT,
        occupation TEXT,
        personality TEXT,
        x INTEGER NOT NULL DEFAULT 50,
        y INTEGER NOT NULL DEFAULT 50,
        z INTEGER NOT NULL DEFAULT 0,
        health INTEGER DEFAULT 100,
        hunger INTEGER DEFAULT 50,
        energy INTEGER DEFAULT 80,
        warmth INTEGER DEFAULT 70,
        money INTEGER DEFAULT 0,
        mood TEXT DEFAULT 'neutral',
        home_x INTEGER,
        home_y INTEGER,
        work_x INTEGER,
        work_y INTEGER,
        inventory TEXT DEFAULT '[]',
        relationships TEXT DEFAULT '{}',
        is_player INTEGER DEFAULT 0,
        is_alive INTEGER DEFAULT 1,
        backstory TEXT,
        current_activity TEXT DEFAULT 'idle'
    );

    CREATE TABLE IF NOT EXISTS objects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        object_type TEXT,
        state TEXT DEFAULT 'normal',
        x INTEGER,
        y INTEGER,
        z INTEGER DEFAULT 0,
        owner_id INTEGER,
        container_id INTEGER,
        is_portable INTEGER DEFAULT 1,
        is_visible INTEGER DEFAULT 1,
        value INTEGER DEFAULT 0,
        weight INTEGER DEFAULT 1,
        properties TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_time INTEGER,
        event_type TEXT,
        description TEXT,
        x INTEGER,
        y INTEGER,
        character_ids TEXT DEFAULT '[]',
        object_ids TEXT DEFAULT '[]'
    );

    CREATE TABLE IF NOT EXISTS game_state (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    conn.commit()

    # Additive migrations — safe to run on an existing DB
    for col, definition in [
        ('thirst',       'INTEGER DEFAULT 30'),
        ('alcohol',      'INTEGER DEFAULT 0'),
        ('stress',       'INTEGER DEFAULT 0'),
        ('posture',      'TEXT DEFAULT "standing"'),
        ('extroversion', 'INTEGER DEFAULT 50'),  # 0=silent/introverted, 100=chatty
        ('speech_memory','TEXT DEFAULT "[]"'),    # JSON: recent overheard/said utterances
        ('sprite_path',  'TEXT DEFAULT NULL'),    # relative path to character sprite PNG
        ('skills',       'TEXT DEFAULT "{}"'),    # JSON: {"baking": 35, "smithing": 0, ...}
    ]:
        try:
            conn.execute(f"ALTER TABLE characters ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass  # column already exists

    for table in ('objects', 'buildings'):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN map_icon TEXT DEFAULT NULL")
            conn.commit()
        except Exception:
            pass  # column already exists

    _ensure_farmyard_layout(conn)
    _ensure_farm_aid_objects(conn)
    conn.close()


_FARMYARD_LAYOUTS = {
    "Finch Farm": {
        "zone": (22, 13, 40, 30),
        "yard": (27, 24, 34, 30),
        "house": (28, 27, 32, 30),
        "entrance": (30, 29),
        "yard_name": "Finch Farmyard",
        "field_name": "Finch Fields",
    },
    "Meadow Farm": {
        "zone": (60, 70, 78, 87),
        "yard": (60, 70, 70, 76),
        "house": (66, 70, 70, 72),
        "entrance": (68, 70),
        "yard_name": "Meadow Farmyard",
        "field_name": "Meadow Fields",
    },
}


def _rect_contains(rect, x, y):
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2


def _ensure_farmyard_layout(conn):
    """Convert oversized farm footprints into outdoor yards plus farmhouse interiors.

    Early versions modelled the whole farm rectangle as a building. That made
    open-air objects such as beehives, apple trees and water butts behave as if
    they were indoors. This migration is idempotent and only touches the two
    generated farm zones.
    """
    for farm_name, layout in _FARMYARD_LAYOUTS.items():
        row = conn.execute(
            "SELECT * FROM buildings WHERE name=? AND building_type='farm'",
            (farm_name,),
        ).fetchone()
        if not row:
            continue

        building_id = row["id"]
        hx1, hy1, hx2, hy2 = layout["house"]
        ex, ey = layout["entrance"]
        conn.execute(
            """
            UPDATE buildings
            SET x1=?, y1=?, x2=?, y2=?, entrance_x=?, entrance_y=?
            WHERE id=?
            """,
            (hx1, hy1, hx2, hy2, ex, ey, building_id),
        )

        zx1, zy1, zx2, zy2 = layout["zone"]
        for y in range(zy1, zy2 + 1):
            for x in range(zx1, zx2 + 1):
                if _rect_contains(layout["house"], x, y):
                    terrain = "building"
                    name = farm_name
                    loc_building_id = building_id
                elif _rect_contains(layout["yard"], x, y):
                    terrain = "farmyard"
                    name = layout["yard_name"]
                    loc_building_id = None
                else:
                    terrain = "farmland"
                    name = layout["field_name"]
                    loc_building_id = None
                conn.execute(
                    """
                    UPDATE locations
                    SET terrain=?, name=?, building_id=?
                    WHERE x=? AND y=? AND z=0
                    """,
                    (terrain, name, loc_building_id, x, y),
                )

        conn.execute(
            """
            DELETE FROM locations
            WHERE building_id=? AND z!=0
              AND NOT (x BETWEEN ? AND ? AND y BETWEEN ? AND ?)
            """,
            (building_id, hx1, hx2, hy1, hy2),
        )

    conn.commit()


def _ensure_farm_aid_objects(conn):
    """Seed a few practical farm aid objects into existing databases."""
    aid_objects = [
        (30, 29, 0, "water bucket", "food",
         "A wooden bucket of clean pump water, kept by the farmhouse door.",
         0, 0, 8, {"drinkable": True, "liquid": "water", "hydration": 45, "immovable": True}),
        (30, 29, 0, "bread heel", "food",
         "The end of a coarse loaf, dry but edible.",
         1, 1, 1, {"edible": True, "nourishment": 18}),
        (31, 29, 0, "milk pail", "food",
         "A small pail of fresh milk from the morning milking.",
         0, 1, 6, {"drinkable": True, "liquid": "milk", "hydration": 25, "nourishment": 12, "immovable": True}),
        (68, 70, 0, "water bucket", "food",
         "A wooden bucket of clean water near the farmhouse entrance.",
         0, 0, 8, {"drinkable": True, "liquid": "water", "hydration": 45, "immovable": True}),
        (68, 70, 0, "oatcake", "food",
         "A plain oatcake, tough but filling.",
         1, 1, 1, {"edible": True, "nourishment": 16}),
    ]
    for x, y, z, name, otype, desc, portable, value, weight, props in aid_objects:
        loc = conn.execute(
            "SELECT id FROM locations WHERE x=? AND y=? AND z=?", (x, y, z)
        ).fetchone()
        if not loc:
            continue
        existing = conn.execute(
            "SELECT id FROM objects WHERE x=? AND y=? AND z=? AND name=? AND is_visible=1",
            (x, y, z, name),
        ).fetchone()
        if existing:
            continue
        conn.execute("""
            INSERT INTO objects
              (name, description, object_type, x, y, z, is_portable, value, weight, properties)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, desc, otype, x, y, z, portable, value, weight, json.dumps(props)))
    conn.commit()


def map_sprites():
    """
    Populate sprite_path for any NPC whose sprite file exists but hasn't been
    set yet.  Safe to call on every startup — only updates NULL entries.
    """
    import os
    sprites_dir = os.path.join(os.path.dirname(__file__), 'sprites')
    if not os.path.isdir(sprites_dir):
        return
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name FROM characters WHERE is_player=0 AND sprite_path IS NULL"
    ).fetchall()
    for row in rows:
        fname = row['name'].lower().strip() + '.png'
        fpath = os.path.join(sprites_dir, fname)
        if os.path.exists(fpath):
            conn.execute(
                "UPDATE characters SET sprite_path=? WHERE id=?",
                (fpath, row['id'])
            )
    conn.commit()
    conn.close()


# --- locations ---

def upsert_location(x, y, z, name, terrain, building_id=None, description=None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO locations (x,y,z,name,terrain,building_id,description)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(x,y,z) DO UPDATE SET
          name=excluded.name, terrain=excluded.terrain,
          building_id=excluded.building_id, description=excluded.description
    """, (x, y, z, name, terrain, building_id, description))
    conn.commit()
    conn.close()


def get_location(x, y, z=0):
    conn = get_conn()
    row = conn.execute("SELECT * FROM locations WHERE x=? AND y=? AND z=?", (x, y, z)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_locations_in_radius(cx, cy, z=0, radius=1):
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM locations
        WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ? AND z=?
    """, (cx-radius, cx+radius, cy-radius, cy+radius, z)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- buildings ---

def insert_building(name, building_type, x1, y1, x2, y2, entrance_x, entrance_y,
                    description=None, floor_count=1, has_basement=0, owner_id=None):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO buildings (name,building_type,x1,y1,x2,y2,entrance_x,entrance_y,
                               description,floor_count,has_basement,owner_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (name, building_type, x1, y1, x2, y2, entrance_x, entrance_y,
          description, floor_count, has_basement, owner_id))
    conn.commit()
    bid = cur.lastrowid
    conn.close()
    return bid


def get_building(building_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM buildings WHERE id=?", (building_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_building_at(x, y):
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM buildings WHERE x1<=? AND x2>=? AND y1<=? AND y2>=?
    """, (x, x, y, y)).fetchone()
    conn.close()
    return dict(row) if row else None


# --- characters ---

def insert_character(data: dict) -> int:
    conn = get_conn()
    data.setdefault('inventory', '[]')
    data.setdefault('relationships', '{}')
    fields = list(data.keys())
    placeholders = ','.join('?' * len(fields))
    cols = ','.join(fields)
    cur = conn.execute(
        f"INSERT INTO characters ({cols}) VALUES ({placeholders})",
        [data[f] for f in fields]
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def get_character(cid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_player():
    conn = get_conn()
    row = conn.execute("SELECT * FROM characters WHERE is_player=1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_npcs():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM characters WHERE is_player=0 AND is_alive=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_characters_at(x, y, z=0):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM characters WHERE x=? AND y=? AND z=? AND is_alive=1", (x, y, z)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_character(cid, **kwargs):
    if not kwargs:
        return
    conn = get_conn()
    sets = ', '.join(f"{k}=?" for k in kwargs)
    conn.execute(f"UPDATE characters SET {sets} WHERE id=?", [*kwargs.values(), cid])
    conn.commit()
    conn.close()


def get_character_inventory(cid):
    c = get_character(cid)
    if not c:
        return []
    return json.loads(c['inventory'])


def add_to_inventory(cid, obj_id):
    inv = get_character_inventory(cid)
    if obj_id not in inv:
        inv.append(obj_id)
    update_character(cid, inventory=json.dumps(inv))


def remove_from_inventory(cid, obj_id):
    inv = get_character_inventory(cid)
    if obj_id in inv:
        inv.remove(obj_id)
    update_character(cid, inventory=json.dumps(inv))


# --- objects ---

def insert_object(data: dict) -> int:
    conn = get_conn()
    data.setdefault('properties', '{}')
    fields = list(data.keys())
    placeholders = ','.join('?' * len(fields))
    cols = ','.join(fields)
    cur = conn.execute(
        f"INSERT INTO objects ({cols}) VALUES ({placeholders})",
        [data[f] for f in fields]
    )
    conn.commit()
    oid = cur.lastrowid
    conn.close()
    return oid


def get_object(oid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM objects WHERE id=?", (oid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_objects_at(x, y, z=0):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM objects WHERE x=? AND y=? AND z=? AND owner_id IS NULL AND is_visible=1",
        (x, y, z)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_objects_owned_by(owner_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM objects WHERE owner_id=?", (owner_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_object(oid, **kwargs):
    if not kwargs:
        return
    conn = get_conn()
    sets = ', '.join(f"{k}=?" for k in kwargs)
    conn.execute(f"UPDATE objects SET {sets} WHERE id=?", [*kwargs.values(), oid])
    conn.commit()
    conn.close()


def find_object_by_name(name, x=None, y=None, z=None):
    conn = get_conn()
    name_lower = f"%{name.lower()}%"
    if x is not None and z is not None:
        row = conn.execute(
            "SELECT * FROM objects WHERE LOWER(name) LIKE ? AND x=? AND y=? AND z=? AND is_visible=1",
            (name_lower, x, y, z)
        ).fetchone()
    elif x is not None:
        row = conn.execute(
            "SELECT * FROM objects WHERE LOWER(name) LIKE ? AND x=? AND y=? AND is_visible=1",
            (name_lower, x, y)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM objects WHERE LOWER(name) LIKE ? AND is_visible=1",
            (name_lower,)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def find_character_by_name(name):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM characters WHERE LOWER(name) LIKE ? AND is_alive=1",
        (f"%{name.lower()}%",)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# --- game state ---

def set_state(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO game_state (key,value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_state(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM game_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


# --- events ---

def log_event(game_time, event_type, description, x=None, y=None, character_ids=None, object_ids=None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO events (game_time, event_type, description, x, y, character_ids, object_ids)
        VALUES (?,?,?,?,?,?,?)
    """, (game_time, event_type, description, x, y,
          json.dumps(character_ids or []), json.dumps(object_ids or [])))
    conn.commit()
    conn.close()


def get_recent_events(limit=10):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]
