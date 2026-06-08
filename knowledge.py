"""
Lightweight player knowledge helpers.

This intentionally uses game_state keys for now. If knowledge grows more
complex it can move to a normalized table later.
"""
import re

import database as db

_LEVELS = {
    None: 0,
    '': 0,
    'glimpsed': 1,
    'nearby': 2,
    'identified': 3,
    'met': 4,
    'spoken': 5,
    'entered': 5,
    'examined': 5,
}


def _key(kind, entity_id):
    return f"seen:{kind}:{entity_id}"


def mark_seen(kind, entity_id, level):
    current = db.get_state(_key(kind, entity_id), '')
    if _LEVELS.get(level, 0) >= _LEVELS.get(current, 0):
        db.set_state(_key(kind, entity_id), level)


def seen_level(kind, entity_id):
    return db.get_state(_key(kind, entity_id), '')


def _observable_person_label(npc):
    occ = (npc.get('occupation') or '').strip()
    if occ:
        # Prefer a short role over a full backstory-like occupation.
        role = re.split(r'[,()]', occ, maxsplit=1)[0].strip()
        return _with_article(role)
    return "a person"


def _with_article(label):
    article = "an" if label[:1].lower() in "aeiou" else "a"
    return f"{article} {label}"


def npc_display_name(npc, distance=0, same_area=True):
    level = seen_level('npc', npc['id'])
    if level in ('met', 'spoken'):
        return npc['name']
    if same_area and distance <= 1:
        mark_seen('npc', npc['id'], 'nearby')
        return _observable_person_label(npc)
    if level in ('nearby', 'identified'):
        return _observable_person_label(npc)
    return "a person"


def building_display_name(building, distance=0, current=False):
    level = seen_level('building', building['id'])
    if current:
        mark_seen('building', building['id'], 'entered')
        return building['name']
    if level in ('identified', 'entered', 'examined'):
        return building['name']
    if distance <= 2:
        mark_seen('building', building['id'], 'identified')
        return building['name']
    btype = (building.get('building_type') or 'building').replace('_', ' ')
    return _with_article(btype)
