"""
Game engine: processes parsed actions, updates state, returns narrative output.
"""
import json
import re
import random
import database as db
import world as w
import llm
import minimap
import areas
import knowledge

# Strips location-contextual phrases from item descriptions shown in inventory
# e.g. "A coin, heads up on the cobbles." → "A coin."
_LOC_PHRASE_RE = re.compile(
    r',?\s*('
    r'heads?\s+up\s+on\s+[^,;.(]+|'
    r'lying\s+(in|on)\s+(the\s+)?[^,;.(]+|'
    r'left\s+(on|in)\s+(the\s+)?[^,;.(]+|'
    r'dropped\s+(on|in)\s+(the\s+)?[^,;.(]+|'
    r'found\s+(on|in)\s+(the\s+)?[^,;.(]+|'
    r'on\s+the\s+(floor|ground|cobbles?|cobblestones?|road|path|table|bar|counter|shelf|sill)[^,;.(]*'
    r')',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# NPC memory helpers
# ---------------------------------------------------------------------------

_NPC_MEMORY_LIMIT = 15


def _npc_memory_add(npc_id, speaker, text, memory_type='heard'):
    """Append an entry to an NPC's speech_memory (capped at _NPC_MEMORY_LIMIT)."""
    c = db.get_character(npc_id)
    if not c:
        return
    memories = json.loads(c.get('speech_memory') or '[]')
    tick = int(db.get_state('game_ticks', 0))
    memories.append({
        'tick': tick,
        'speaker': speaker,
        'text': text[:200],
        'type': memory_type,
    })
    while len(memories) > _NPC_MEMORY_LIMIT:
        memories.pop(0)
    db.update_character(npc_id, speech_memory=json.dumps(memories))


def _npc_memory_get(npc_id, limit=5):
    """Return the most recent speech memory entries for an NPC."""
    c = db.get_character(npc_id)
    if not c:
        return []
    return json.loads(c.get('speech_memory') or '[]')[-limit:]


def _npc_extroversion(npc):
    """Return 0-100 extroversion; higher = more likely to speak spontaneously."""
    val = npc.get('extroversion')
    if val is not None:
        return int(val)
    p = (npc.get('personality') or '').lower()
    if any(w in p for w in ('gregarious', 'chatty', 'outgoing', 'talkative', 'loud', 'friendly')):
        return 75
    if any(w in p for w in ('quiet', 'reserved', 'shy', 'introverted', 'taciturn', 'silent', 'sullen')):
        return 20
    return 50


def _match_npc(target_str, chars):
    """Return first NPC in chars whose name OR occupation matches target_str (case-insensitive)."""
    t = target_str.lower()
    # Occupation synonyms: common generic titles players might use
    _SYNONYMS = {
        'barman': ('innkeeper', 'landlord', 'publican', 'barman', 'bartender'),
        'barmaid': ('innkeeper', 'landlord', 'publican', 'barmaid', 'barlady'),
        'shopkeeper': ('merchant', 'trader', 'shopkeeper', 'grocer', 'draper'),
        'blacksmith': ('blacksmith', 'smith', 'farrier'),
        'priest': ('rector', 'vicar', 'curate', 'priest', 'reverend', 'minister'),
        'doctor': ('doctor', 'physician', 'surgeon', 'apothecary'),
        'farmer': ('farmer', 'farmhand', 'labourer'),
    }
    occupations_to_try = _SYNONYMS.get(t, [t])
    for c in chars:
        if c.get('is_player'):
            continue
        name = (c.get('name') or '').lower()
        occ  = (c.get('occupation') or '').lower()
        if t in name:
            return c
        if any(syn in occ for syn in occupations_to_try):
            return c
    return None

_OUTDOOR_TERRAIN = {'wilderness', 'field', 'stream', 'road', 'track', 'park', 'market',
                    'farmland', 'farmyard'}
_INDOOR_TERRAIN  = {'building', 'upstairs', 'cellar'}

_TERRAIN_FEEL = {
    'road':       "The road is firm underfoot, packed earth and old stone.",
    'track':      "The track is rough and rutted, uneven from cart wheels.",
    'path':       "The path is worn and uneven beneath your feet.",
    'field':      "The ground is soft and yielding, giving slightly with each step.",
    'farmland':   "Furrowed earth gives underfoot, soft and uneven.",
    'farmyard':   "Packed mud, straw, and rutted earth shift underfoot.",
    'wilderness': "The ground is uneven and wild, roots and soft earth shifting underfoot.",
    'park':       "Grass cushions your footsteps.",
    'market':     "Old cobblestones ring beneath your boots.",
    'stream':     "The ground near the water is soft and waterlogged.",
    'building':   "Floorboards shift and creak as you move.",
    'upstairs':   "The wooden boards flex underfoot.",
    'cellar':     "Cold stone flags press through the soles of your boots.",
}

_WEATHER_FEEL = {
    'drizzling': "Light rain falls on your face and hands, cold and relentless.",
    'overcast':  "The air is damp and heavy, a chill that finds the back of your neck.",
    'clear':     None,
    'cloudy':    None,
}

_FOOD_WORDS = ('food', 'bread', 'meal', 'eat', 'hungry', 'starving', 'starve')
_WATER_WORDS = ('water', 'drink', 'thirst', 'thirsty', 'dehydrated')
_HELP_WORDS = ('help', 'aid', 'mercy', 'please', 'collapsed', 'dying')
_PAY_WORDS = ('pay', 'buy', 'money', 'coin', 'penny', 'pence')
_SHELTER_WORDS = ('shelter', 'sleep', 'bed', 'room', 'rest')

_AID_FOOD_ROLES = ('farmer', 'farmhand', 'farmer\'s wife', 'innkeeper', 'baker',
                   'shopkeeper', 'doctor')
_AID_WATER_ROLES = ('farmer', 'farmhand', 'farmer\'s wife', 'innkeeper', 'baker',
                    'shopkeeper', 'doctor', 'washerwoman')

_FOOD_CLUES = {
    'farmer': "There should be food and water at a farm. Try asking plainly for bread or water.",
    'farmhand': "There is usually water at the pump and something plain to eat in the farmhouse.",
    "farmer's wife": "Ask for bread or water, not a vague favour.",
    'innkeeper': "The inn can sell food if you are standing by the bar.",
    'baker': "The bakery sells bread during the day.",
    'shopkeeper': "The shop sells provisions.",
    'doctor': "The doctor can help with collapse or thirst, but not feed the whole village.",
}

_TARGET_PREFIX_RE = re.compile(
    r'^(?:go|walk|move|head|step|run)\s+to(?:ward[s]?)?\s+(?:the\s+|a\s+|an\s+)?',
    re.IGNORECASE,
)

# Work and skills: what trades are practiced where, and how to talk about them.
_TRADES = {
    'bakery':  {'skill': 'baking',      'skilled': True,
                'roles': ('baker', "baker's apprentice"),
                'task_words': ('bake', 'baking', 'knead', 'dough', 'oven', 'loaves', 'loaf'),
                'task_label': 'baking bread', 'base_pay': 8, 'unskilled_pay': 3, 'learn_fee': 6},
    'smithy':  {'skill': 'smithing',    'skilled': True,
                'roles': ('blacksmith', 'smith', 'farrier'),
                'task_words': ('forge', 'smith', 'smithing', 'hammer', 'anvil', 'shoe', 'iron', 'metal'),
                'task_label': 'working the forge', 'base_pay': 10, 'unskilled_pay': 3, 'learn_fee': 8},
    'medical': {'skill': 'healing',     'skilled': True,
                'roles': ('doctor', 'physician', 'surgeon', 'nurse'),
                'task_words': ('nurse', 'nursing', 'patient', 'bandage', 'medicine', 'tend the sick'),
                'task_label': 'helping with patients', 'base_pay': 9, 'unskilled_pay': 2, 'learn_fee': 10},
    'inn':     {'skill': 'serving',     'skilled': False,
                'roles': ('innkeeper', "innkeeper's wife", 'landlord', 'barmaid', 'barman'),
                'task_words': ('serve', 'serving', 'pour', 'tankard', 'tables', 'sweep', 'wash'),
                'task_label': 'serving in the taproom', 'base_pay': 4, 'unskilled_pay': 4, 'learn_fee': None},
    'shop':    {'skill': 'shopkeeping', 'skilled': False,
                'roles': ('shopkeeper', 'merchant', 'trader', 'grocer', 'draper'),
                'task_words': ('stock', 'shelves', 'counter', 'till', 'sweep', 'sell'),
                'task_label': 'minding the counter and stocking shelves', 'base_pay': 4, 'unskilled_pay': 4, 'learn_fee': None},
    'farm':    {'skill': 'farm labour', 'skilled': False,
                'roles': ('farmer', "farmer's wife", 'farm labourer', 'farmhand'),
                'task_words': ('plough', 'plow', 'muck', 'milk', 'feed the animals',
                               'harvest', 'hay', 'tend'),
                'task_label': 'tending the fields and animals', 'base_pay': 5, 'unskilled_pay': 5, 'learn_fee': None},
}

_SKILL_ALIASES = {
    'bread': 'baking', 'bake': 'baking', 'baking': 'baking', 'knead': 'baking',
    'dough': 'baking', 'loaves': 'baking', 'loaf': 'baking', 'oven': 'baking',
    'forge': 'smithing', 'smith': 'smithing', 'smithing': 'smithing',
    'shoe': 'smithing', 'horse': 'smithing', 'iron': 'smithing', 'metal': 'smithing',
    'hammer': 'smithing', 'anvil': 'smithing',
    'heal': 'healing', 'healing': 'healing', 'nurse': 'healing', 'nursing': 'healing',
    'medicine': 'healing', 'bandage': 'healing', 'doctor': 'healing',
    'serve': 'serving', 'serving': 'serving', 'tankard': 'serving', 'bar': 'serving',
    'shopkeeping': 'shopkeeping', 'shopkeeper': 'shopkeeping', 'shop': 'shopkeeping',
    'till': 'shopkeeping', 'counter': 'shopkeeping',
    'farm': 'farm labour', 'farming': 'farm labour', 'plough': 'farm labour',
    'plow': 'farm labour', 'milk': 'farm labour', 'animals': 'farm labour',
}

_TRADE_BY_SKILL = {trade['skill']: (btype, trade) for btype, trade in _TRADES.items()}


def _player_skill(player, skill_name):
    p = db.get_character(player['id'])
    skills = json.loads(p.get('skills') or '{}')
    return skills.get(skill_name, 0)


def _adjust_skill(player, skill_name, delta):
    p = db.get_character(player['id'])
    skills = json.loads(p.get('skills') or '{}')
    new_level = max(0, min(100, skills.get(skill_name, 0) + delta))
    skills[skill_name] = new_level
    db.update_character(player['id'], skills=json.dumps(skills))
    return new_level


def _occupation_matches_trade(npc, building_type):
    trade = _TRADES.get(building_type)
    if not trade:
        return False
    occ = (npc.get('occupation') or '').lower()
    return any(role in occ for role in trade['roles'])

# Words that can open an independent action clause
_ACTION_STARTERS = frozenset({
    'go', 'walk', 'move', 'head', 'run', 'step', 'travel',
    'take', 'pick', 'grab', 'get', 'collect', 'retrieve',
    'drop', 'put', 'place', 'leave', 'set',
    'eat', 'consume', 'bite',
    'drink', 'sip',
    'say', 'speak', 'talk', 'tell', 'ask', 'greet', 'shout', 'whisper',
    'give', 'hand', 'offer', 'pay',
    'buy', 'purchase', 'sell', 'trade',
    'use', 'open', 'close', 'unlock', 'lock',
    'look', 'examine', 'inspect', 'check', 'search', 'study',
    'attack', 'hit', 'strike', 'fight',
    'sleep', 'rest', 'sit', 'stand', 'lie',
    'feed', 'pet', 'stroke', 'catch',
    'read', 'write', 'wear', 'equip', 'remove',
    'wait', 'hide', 'flee',
})

_DIRECTION_ALIASES = {
    'north': 'north', 'n': 'north',
    'south': 'south', 's': 'south',
    'east': 'east', 'e': 'east',
    'west': 'west', 'w': 'west',
    'up': 'up', 'u': 'up',
    'down': 'down', 'd': 'down',
}

_COMPOUND_HARD_RE = re.compile(
    r'\s+and\s+then\s+|\s*,\s*then\s+|\s+then\s+|\s*;\s*',
    re.IGNORECASE,
)
_LEADING_FILLER_RE = re.compile(
    r'^(?:first|also|next|finally|afterwards?|lastly)\s+',
    re.IGNORECASE,
)


def _split_compound_input(text):
    """
    Split a compound player command into individual action parts.
    Returns a list; single-element if no compound structure is found.

    Hard split on 'and then', ', then', ';'.
    Soft split on ' and ' only when the segment after 'and' opens with
    a known action starter — so 'pick up the coin and the bread' stays
    whole (multi-object take) while 'eat the bread and drink the water'
    is split into two separate commands.
    """
    parts = _COMPOUND_HARD_RE.split(text)
    if len(parts) == 1:
        # Soft split: check each 'and' segment
        segments = re.split(r'\s+and\s+', text, flags=re.IGNORECASE)
        if len(segments) > 1:
            merged = [segments[0]]
            for seg in segments[1:]:
                first_word = seg.strip().split()[0].lower() if seg.strip() else ''
                if first_word in _ACTION_STARTERS:
                    merged.append(seg)
                else:
                    merged[-1] = merged[-1] + ' and ' + seg
            parts = merged if len(merged) > 1 else [text]

    cleaned = []
    for p in parts:
        p = _LEADING_FILLER_RE.sub('', p.strip())
        if p:
            cleaned.append(p)
    return cleaned

_ARTICLE_RE = re.compile(r'^(?:the|a|an|my)\s+', re.IGNORECASE)


def _clean_target_name(target):
    return _ARTICLE_RE.sub('', (target or '').strip()).strip(" .?!'\"")


def _split_targets(target):
    """Split simple multi-object targets without attempting full grammar."""
    target = (target or '').replace(',', ' and ')
    parts = [p.strip() for p in re.split(r'\s+and\s+', target) if p.strip()]
    return [_clean_target_name(p) for p in parts]


def _normalize_raw_input(raw_input):
    """Normalize small, safe typos before the LLM parser sees the command."""
    text = raw_input.strip()
    compact = re.sub(r'\s+', ' ', text)
    compact = re.sub(r'^(got|g o)\s+', 'go ', compact, flags=re.IGNORECASE)
    compact = re.sub(r'\s+([?.!])$', r'\1', compact)
    if compact.lower().startswith('go ') and compact.endswith('.'):
        compact = compact[:-1]
    return compact

_TERRAIN_CONDITION = {
    'stream':     'wet',
    'field':      'earthy',
    'farmland':   'earthy',
    'farmyard':   'muddy',
    'wilderness': 'earthy',
    'track':      'dusty',
    'road':       'dusty',
    'market':     'dusty',
    'park':       'grassy',
    'cellar':     'dusty and damp',
    'building':   '',
    'upstairs':   '',
}


def _outdoor_view_from(x, y, z):
    """Return a brief factual string describing what is visible through a window."""
    # Check ground-level adjacent tiles for outdoor terrain
    candidates = [(0, -1), (0, 1), (1, 0), (-1, 0)]
    for dx, dy in candidates:
        loc = db.get_location(x + dx, y + dy, 0)
        if loc and loc.get('terrain') in _OUTDOOR_TERRAIN:
            summary = w.get_location_summary(x + dx, y + dy, 0)
            chars = [c for c in db.get_characters_at(x + dx, y + dy, 0)
                     if not c.get('is_player')]
            char_str = (', '.join(c['name'] for c in chars[:2]) + ' are visible outside.'
                        if chars else '')
            return (f"{summary}. {char_str}").strip().rstrip('.')
    # Upstairs — look out and down at ground level
    if z > 0:
        for dx, dy in candidates + [(0, 0)]:
            loc = db.get_location(x + dx, y + dy, 0)
            if loc and loc.get('terrain') in _OUTDOOR_TERRAIN:
                summary = w.get_location_summary(x + dx, y + dy, 0)
                return f"Below: {summary}"
    return None


def _pickup_condition(terrain, weather):
    """Return a condition string for an object just picked up from this terrain."""
    base = _TERRAIN_CONDITION.get(terrain, '')
    if weather == 'drizzling' and terrain in _OUTDOOR_TERRAIN:
        if base:
            return 'muddy' if 'earth' in base or 'grass' in base else 'damp'
        return 'damp'
    return base


def _obj_condition_str(props):
    """Return a human-readable condition note, or empty string if clean."""
    c = props.get('condition', '')
    return c if c and c != 'clean' else ''


def _get_light_context(x, y, z, loc):
    """Return a short string describing the current lighting conditions."""
    ticks  = int(db.get_state('game_ticks', 0))
    hour   = (6 + ticks // 4) % 24
    weather = get_weather()
    terrain = (loc.get('terrain') or '') if loc else ''
    is_indoor = terrain in _INDOOR_TERRAIN

    if is_indoor:
        objs = db.get_objects_at(x, y, z)
        lit = [o['name'] for o in objs
               if json.loads(o.get('properties') or '{}').get('lit')]

        if terrain == 'cellar':
            return f"very dim, lit only by {', '.join(lit)}" if lit else "near darkness"

        if lit:
            return f"lit by {', '.join(lit)}"

        bld_type = None
        if loc and loc.get('building_id'):
            bld = db.get_building(loc['building_id'])
            if bld:
                bld_type = bld.get('building_type')

        large_windows = {'church', 'inn', 'civic', 'school'}
        dim_inside    = {'smithy'}

        if 8 <= hour < 17:
            if bld_type in large_windows:
                return "daylight streams through large windows"
            if bld_type in dim_inside:
                return "dim, little daylight penetrates inside"
            return "pale daylight through modest windows"
        if 17 <= hour < 20:
            return "dim, the last daylight fading through the windows"
        return "dark inside, no light source"

    # Outdoors
    if hour >= 22 or hour < 5:
        moon = ["no moon, near darkness",
                "a thin crescent moon",
                "a bright full moon",
                "a half moon"][( ticks // 200) % 4]
        if weather in ('overcast', 'drizzling', 'cloudy'):
            return "dark night, cloud smothering any moonlight"
        return f"night lit by {moon}"
    if hour < 7:
        return "the cold grey light of dawn"
    if hour < 8:
        return "early morning light, long shadows stretching west"
    if hour < 17:
        if weather == 'overcast':  return "flat grey overcast light"
        if weather == 'drizzling': return "grey drizzling light, muted and damp"
        if weather == 'clear':     return "clear bright daylight"
        return "soft cloudy daylight"
    if hour < 20:
        return "warm amber evening light, shadows long"
    return "dusk, the light failing fast"


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _mark_seen(key):
    count = int(db.get_state(f'seen:{key}', 0))
    db.set_state(f'seen:{key}', count + 1)
    db.set_state(f'seen_tick:{key}', int(db.get_state('game_ticks', 0)))


def _familiarity(key, player_x=None, player_y=None, loc_x=None, loc_y=None):
    """
    Return familiarity level 0 (new), 1 (familiar), or 2 (well-known).
    Accounts for time elapsed since last seen and distance from player.
    """
    count = int(db.get_state(f'seen:{key}', 0))
    if count == 0:
        return 0

    last_tick  = int(db.get_state(f'seen_tick:{key}', 0))
    now        = int(db.get_state('game_ticks', 0))
    ticks_ago  = now - last_tick

    # Time decay: memory fades after ~1 day (96 ticks) and ~4 days (384 ticks)
    if ticks_ago > 384:
        count = max(0, count - 2)
    elif ticks_ago > 96:
        count = max(0, count - 1)

    # Distance decay: far-away things are harder to recall clearly
    if player_x is not None and loc_x is not None:
        dist = abs(player_x - loc_x) + abs(player_y - (loc_y or 0))
        if dist > 20:
            count = max(0, count - 2)
        elif dist > 8:
            count = max(0, count - 1)

    if count <= 0: return 0
    if count <= 2: return 1
    return 2


def get_confusion(p):
    """Derive confusion/paranoia level (0-100) from current player stats.
    Not stored — computed fresh each call. Higher = more cognitively impaired."""
    c = 0
    stress  = p.get('stress',   0)
    hunger  = p.get('hunger',   50)
    thirst  = p.get('thirst',   30)
    energy  = p.get('energy',   100)
    alcohol = p.get('alcohol',  0)
    if stress  >= 55: c += int((stress  - 55) * 0.90)
    if hunger  >= 65: c += int((hunger  - 65) * 0.55)
    if thirst  >= 70: c += int((thirst  - 70) * 0.70)
    if energy  <= 25: c += int((25 - energy)  * 1.10)
    if alcohol >= 25: c += int((alcohol - 25) * 0.55)
    return min(100, c)


def _player_condition_summary(p):
    """Translate numeric player stats into a vivid physical-state string for the LLM."""
    parts = []

    hunger   = p.get('hunger',   0)
    thirst   = p.get('thirst',   0)
    energy   = p.get('energy',   100)
    alcohol  = p.get('alcohol',  0)
    stress   = p.get('stress',   0)
    exertion = int(db.get_state('player_exertion', 0))

    if hunger >= 80:   parts.append("desperately hungry, stomach cramping, weak")
    elif hunger >= 60: parts.append("quite hungry, difficulty concentrating")
    elif hunger >= 40: parts.append("could eat")
    elif hunger <= 10: parts.append("recently well-fed, pleasantly full")

    if thirst >= 80:   parts.append("severely thirsty, mouth dry and cottony, head throbbing")
    elif thirst >= 60: parts.append("thirsty, throat parched")
    elif thirst <= 10: parts.append("well-hydrated")

    if energy <= 10:   parts.append("utterly exhausted, limbs like lead, eyes heavy")
    elif energy <= 25: parts.append("very tired, sluggish")
    elif energy <= 40: parts.append("tired")
    elif energy >= 90: parts.append("well-rested and alert, senses sharp")

    if alcohol >= 70:  parts.append("quite drunk, world swaying, vision blurred, warmth in chest")
    elif alcohol >= 40: parts.append("tipsy, edges pleasantly softened, sounds seem louder")
    elif alcohol >= 15: parts.append("slightly warm from drink, relaxed")

    if exertion >= 70: parts.append("out of breath, heart pounding, hard to focus")
    elif exertion >= 35: parts.append("slightly breathless from recent exertion")

    if stress >= 80:   parts.append("gripped by fear, heart racing, hyper-alert to every sound and movement")
    elif stress >= 55: parts.append("anxious and unsettled, on edge")
    elif stress >= 30: parts.append("uneasy, a background sense of unease")
    elif stress <= 5 and energy >= 60 and hunger <= 40:
        parts.append("calm and content, senses unhurried and open")

    confusion = get_confusion(p)
    if confusion >= 70:
        parts.append("thoughts fracturing, paranoid — faces seem hostile, sounds seem threatening")
    elif confusion >= 50:
        parts.append("thoughts muddy and unreliable, cannot trust own perceptions")
    elif confusion >= 30:
        parts.append("mind scattered, struggling to think clearly")

    return ", ".join(parts) if parts else "physically comfortable"


def build_context(player):
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    loc_name = w.get_location_summary(x, y, z)
    chars = db.get_characters_at(x, y, z)
    char_names = [c['name'] for c in chars if not c.get('is_player')]
    char_descriptions = [
        f"{c['name']} ({c['occupation']})" if c.get('occupation') else c['name']
        for c in chars if not c.get('is_player')
    ]
    objs = db.get_objects_at(x, y, z)
    # 'environmental' objects are ambient texture, not tangible things —
    # keep them out of "Objects here" (which the LLM treats as interactable
    # props) and offer them separately as optional flavour the narrator may
    # weave in naturalistically (a scent, a glimpse) but must not treat as
    # something the player can pick up or use.
    obj_names = [o['name'] for o in objs if o.get('object_type') != 'environmental']
    ambient_names = [o['name'] for o in objs if o.get('object_type') == 'environmental']
    inv_names = []
    for oid in db.get_character_inventory(player['id']):
        obj = db.get_object(oid)
        if obj:
            inv_names.append(obj['name'])
    recent_events = db.get_recent_events(3)
    recent_str = '; '.join(e['description'] for e in recent_events) if recent_events else 'none'
    p = db.get_character(player['id'])
    time_str = _format_time()
    light_ctx = _get_light_context(x, y, z, loc)
    condition = _player_condition_summary(p)
    _confusion_val = get_confusion(p)
    conf_ctx = (f"Player confusion: "
                + ("severe" if _confusion_val >= 70 else
                   "high"   if _confusion_val >= 50 else
                   "moderate")
                + ". ") if _confusion_val >= 30 else ""

    # For-sale items at commercial locations — helps LLM answer affordability questions
    for_sale_str = ''
    import sqlite3 as _sqlite3
    from config import DB_PATH as _DBPATH
    if loc and loc.get('building_id'):
        bld = db.get_building(loc['building_id'])
        if bld and bld.get('building_type') in ('shop', 'inn', 'bakery', 'market'):
            _conn = _sqlite3.connect(_DBPATH)
            _conn.row_factory = _sqlite3.Row
            _rows = _conn.execute(
                "SELECT o.name, o.value FROM objects o "
                "JOIN locations l ON o.x=l.x AND o.y=l.y AND o.z=l.z "
                "WHERE l.building_id=? AND o.is_visible=1 AND o.owner_id IS NULL",
                (bld['id'],)
            ).fetchall()
            _conn.close()
            sale_items = [f"{r['name']} ({r['value']}p)" for r in _rows
                          if r['value'] and r['value'] > 0]
            if sale_items:
                for_sale_str = f"For sale here: {', '.join(sale_items[:8])}. "
    # Outdoor market stalls: check for sellable items on this tile
    _STALL_NAMES = ('market stall', 'stall', 'market stand', 'vendor stall')
    if not for_sale_str and any(o['name'].lower() in _STALL_NAMES for o in objs):
        _conn = _sqlite3.connect(_DBPATH)
        _conn.row_factory = _sqlite3.Row
        _stall_rows = _conn.execute(
            "SELECT name, value FROM objects WHERE x=? AND y=? AND z=? "
            "AND is_visible=1 AND owner_id IS NULL AND value > 0 "
            "AND (state IS NULL OR state NOT IN ('dropped', 'consumed', 'sold'))",
            (x, y, z)
        ).fetchall()
        _conn.close()
        stall_items = [f"{r['name']} ({r['value']}p)" for r in _stall_rows]
        if stall_items:
            for_sale_str = f"For sale at the market stall: {', '.join(stall_items[:8])}. "
        else:
            for_sale_str = "There is a market stall here but nothing is currently displayed for sale. "

    return (
        f"Location: {loc_name} at ({x},{y},z={z}). "
        f"Time: {time_str}. "
        f"Light: {light_ctx}. "
        f"Weather: {get_weather()}. "
        f"Player condition: {condition}. "
        + conf_ctx
        + f"Player: health={p['health']}/100, hunger={p['hunger']}/100, "
        f"thirst={p.get('thirst', 30)}/100, energy={p['energy']}/100, "
        f"alcohol={p.get('alcohol', 0)}/100, money={p.get('money', 0)}p. "
        f"Inventory: {', '.join(inv_names) or 'none'}. "
        + (f"People here: {', '.join(char_descriptions)}. " if char_descriptions else "")
        + f"Objects here: {', '.join(obj_names) or 'none'}. "
        + (f"Ambient details you may notice and weave into the description "
           f"naturalistically (a glimpse, a scent, brushing past) — purely "
           f"textural, not things the player can pick up, use, or interact "
           f"with mechanically: {', '.join(ambient_names)}. " if ambient_names else "")
        + for_sale_str
        + f"Recent events: {recent_str}."
    ).rstrip()


def _format_time():
    ticks = int(db.get_state('game_ticks', 0))
    hour = (6 + ticks // 4) % 24
    minute = (ticks % 4) * 15
    period = 'am' if hour < 12 else 'pm'
    h12 = hour % 12 or 12
    return f"{h12}:{minute:02d}{period}"


def get_time_of_day():
    ticks = int(db.get_state('game_ticks', 0))
    hour = (6 + ticks // 4) % 24
    if 5 <= hour < 8:   return "dawn"
    if 8 <= hour < 12:  return "morning"
    if 12 <= hour < 14: return "midday"
    if 14 <= hour < 17: return "afternoon"
    if 17 <= hour < 20: return "evening"
    if 20 <= hour < 23: return "night"
    return "late night"


def get_weather():
    ticks = int(db.get_state('game_ticks', 0))
    cycle = (ticks // 40) % 4
    return ["overcast", "drizzling", "clear", "cloudy"][cycle]


def _object_props(obj):
    return json.loads(obj.get('properties') or '{}')


def _shop_stock(obj, building_type):
    if building_type not in ('shop', 'inn', 'bakery', 'market'):
        return False
    props = _object_props(obj)
    if obj.get('state') == 'dropped':
        return False
    return props.get('for_sale') or (obj.get('is_portable') and obj.get('value', 0) > 0)


def _first_step_toward(dx, dy):
    sx = 0 if dx == 0 else (1 if dx > 0 else -1)
    sy = 0 if dy == 0 else (1 if dy > 0 else -1)
    for name, delta in w.DIRECTIONS.items():
        if delta == (sx, sy, 0) and len(name) > 1:
            return name
    return None


def _visible_object_named(target, player, radius=3):
    target = _clean_target_name(target).lower()
    x, y, z = player['x'], player['y'], player['z']
    best = None
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            for obj in db.get_objects_at(x + dx, y + dy, z):
                if target in obj['name'].lower():
                    dist = abs(dx) + abs(dy)
                    if best is None or dist < best[0]:
                        best = (dist, dx, dy, obj)
    return best


def action_go_to(player, target):
    """Move toward a named visible object/person/building, or acknowledge proximity."""
    target = _clean_target_name(target)
    if not target:
        return "Where do you want to go?"

    if target.lower() in ('left', 'right', 'forward', 'back', 'backward'):
        return ("Millhaven does not track which way you are facing yet. "
                "Use north, south, east, west, up, or down.")

    obj_match = _visible_object_named(target, player, radius=3)
    if obj_match:
        dist, dx, dy, obj = obj_match
        if dist == 0:
            return f"You step closer to the {obj['name']}. It is already within reach."
        direction = _first_step_toward(dx, dy)
        if direction:
            return action_move(player, {'direction': direction})

    npc = db.find_character_by_name(target)
    if npc and not npc.get('is_player'):
        dx = npc['x'] - player['x']
        dy = npc['y'] - player['y']
        if npc.get('z', player['z']) != player['z']:
            return f"{npc['name']} is on another floor."
        dist = abs(dx) + abs(dy)
        if dist == 0:
            return f"You are already standing near {npc['name']}."
        direction = _first_step_toward(dx, dy)
        if direction:
            return action_move(player, {'direction': direction})

    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM buildings WHERE LOWER(name) LIKE ?",
        (f"%{target.lower()}%",),
    ).fetchone()
    _TYPE_ALIASES = {
        'inn': 'inn', 'pub': 'inn', 'tavern': 'inn',
        'baker': 'bakery', 'bakery': 'bakery', 'bread': 'bakery',
        'shop': 'shop', 'store': 'shop', 'general store': 'shop',
        'smithy': 'smithy', 'forge': 'smithy', 'blacksmith': 'smithy',
        'church': 'church', 'chapel': 'church',
        'doctor': 'medical', 'surgery': 'medical', 'apothecary': 'medical',
        'school': 'school', 'post': 'post', 'post office': 'post',
        'police': 'police', 'constable': 'police', 'station': 'police',
        'town hall': 'civic', 'hall': 'civic',
        'farm': 'farm',
    }
    # Fallback 1: normalize common title abbreviations, strip punctuation, retry
    _TITLE_ABBREVS = (
        (r'\bdoctor\b', 'dr'), (r'\bstreet\b', 'st'), (r'\bsaint\b', 'st'),
        (r'\bmister\b', 'mr'), (r'\bmistress\b', 'mrs'),
    )
    def _strip_punct(s):
        return re.sub(r"[.'\"\\-]", '', s)

    if not row:
        normalized = target.lower()
        for pattern, abbrev in _TITLE_ABBREVS:
            normalized = re.sub(pattern, abbrev, normalized)
        stripped_target = _strip_punct(normalized)
        all_buildings = conn.execute("SELECT * FROM buildings").fetchall()
        for bld_row in all_buildings:
            if stripped_target in _strip_punct(bld_row['name'].lower()):
                row = bld_row
                break
    # Fallback 2: match building_type via exact whole-target alias
    if not row:
        bld_type = _TYPE_ALIASES.get(target.lower())
        if bld_type:
            row = conn.execute(
                "SELECT * FROM buildings WHERE building_type = ?", (bld_type,)
            ).fetchone()
    # Fallback 3: match building_type via any individual word in the target
    if not row:
        for word in target.lower().split():
            bld_type = _TYPE_ALIASES.get(word)
            if bld_type:
                row = conn.execute(
                    "SELECT * FROM buildings WHERE building_type = ?", (bld_type,)
                ).fetchone()
                if row:
                    break
    conn.close()
    if row:
        bld = dict(row)
        tile = w.nearest_building_tile(player['x'], player['y'], bld['id'], player['z'])
        if tile:
            dx = tile[0] - player['x']
            dy = tile[1] - player['y']
        else:
            dx = bld['entrance_x'] - player['x']
            dy = bld['entrance_y'] - player['y']
        if dx == 0 and dy == 0:
            return f"You are at the entrance to {bld['name']}."
        direction = _first_step_toward(dx, dy)
        if direction:
            dist = abs(dx) + abs(dy)
            move_result = action_move(player, {'direction': direction})
            if dist > 1:
                steps_left = dist - 1
                dest_note = (f"Heading toward {bld['name']} "
                             f"({direction}, {steps_left} more step{'s' if steps_left != 1 else ''}). "
                             f"Keep going {direction}, or say 'keep going'.")
            else:
                dest_note = f"You arrive at {bld['name']}."
            return f"{dest_note}\n{move_result}"

    return f"You do not know how to get to '{target}' from here."


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def action_look(player, parsed):
    # "look at X" should examine X, not re-describe the whole location
    if parsed.get('target'):
        return action_examine(player, parsed)

    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    loc_name = w.get_location_summary(x, y, z)

    careful = parsed.get('careful', False)
    loc_key = f'loc:{x},{y},{z}'
    fam = 0 if careful else _familiarity(loc_key)
    _mark_seen(loc_key)

    chars = db.get_characters_at(x, y, z)
    char_descs = [
        f"{c['name']} ({c['occupation']})" if c.get('occupation') else c['name']
        for c in chars if not c.get('is_player')
    ]
    objs = db.get_objects_at(x, y, z)
    # 'environmental' objects are ambient texture, not tangible interactable
    # things — keep them out of "Objects visible" and offer them separately
    # as optional flavour the narrator may mention naturalistically.
    obj_names = [o['name'] for o in objs if o.get('object_type') != 'environmental']
    ambient_names = [o['name'] for o in objs if o.get('object_type') == 'environmental']
    ambient_hint = (
        f"You may notice (and optionally weave in naturalistically — a glimpse, "
        f"a scent, brushing past — purely textural, not things the player can "
        f"pick up, use, or interact with mechanically): {', '.join(ambient_names)}."
        if ambient_names else ""
    )

    building_name = None
    if loc and loc.get('building_id'):
        bld = db.get_building(loc['building_id'])
        if bld:
            building_name = bld['name']

    desc = llm.generate_location_description(
        location_name=loc_name,
        terrain=loc['terrain'] if loc else 'ground',
        building_name=building_name,
        time_of_day=get_time_of_day(),
        weather=get_weather(),
        light_context=_get_light_context(x, y, z, loc),
        characters_present=char_descs,
        objects_present=obj_names,
        extra_context=ambient_hint,
        familiarity=fam,
    )

    surrounds = w.describe_surroundings(x, y, z, radius=1)
    return f"{desc}\n{surrounds}"


def action_move(player, parsed):
    direction = parsed.get('direction')
    if not direction:
        return "Which direction do you want to go?"

    delta = w.resolve_direction(direction)
    if not delta:
        return f"'{direction}' is not a direction I understand."

    x, y, z = player['x'], player['y'], player['z']
    nx, ny, nz = x + delta[0], y + delta[1], z + delta[2]

    # Edge-of-world: two-step warning before game over
    if delta[2] == 0 and (nx < 0 or nx >= w.GRID_WIDTH or ny < 0 or ny >= w.GRID_HEIGHT):
        pending = player.get('_pending_leave_direction', '')
        if pending == direction:
            db.set_state('game_over', '1')
            return '__LEAVE_GAME__'
        db.set_state('pending_leave_direction', direction)
        return ("If you go in that direction you will leave the environs of Millhaven.")

    ok, reason = w.can_move_between(x, y, z, nx, ny, nz)
    if not ok:
        if delta[2] != 0:
            return ("There is no higher floor here." if delta[2] > 0
                    else "There is no lower floor or basement here.")
        return reason

    old_loc = db.get_location(x, y, z)
    old_zone_name = (old_loc.get('name') or '') if old_loc else ''
    old_building_id = (old_loc.get('building_id')) if old_loc else None

    db.update_character(player['id'], x=nx, y=ny, z=nz, posture='standing')
    player['x'], player['y'], player['z'] = nx, ny, nz

    loc_name = w.get_location_summary(nx, ny, nz)
    new_loc = db.get_location(nx, ny, nz)
    terrain = (new_loc.get('terrain') or '') if new_loc else ''
    new_zone_name = (new_loc.get('name') or '') if new_loc else ''
    new_building_id = (new_loc.get('building_id')) if new_loc else None

    same_zone = areas.same_area(old_loc, new_loc) or (
        old_zone_name and old_zone_name == new_zone_name
        and old_building_id == new_building_id
    )

    loc_key = f'loc:{nx},{ny},{nz}'
    fam = _familiarity(loc_key)
    _mark_seen(loc_key)

    if same_zone:
        situation = (f"The player moves {direction} through {new_zone_name}. "
                     "They are already inside this area — do NOT write arrival language "
                     "('step into', 'enter', 'arrive', 'find yourself in'). "
                     "Describe movement through a familiar space instead.")
    else:
        situation = f"The player moves {direction} and arrives at {loc_name}."
    terrain_feel = _TERRAIN_FEEL.get(terrain)
    if terrain_feel:
        situation += f" {terrain_feel}"
    if terrain in _OUTDOOR_TERRAIN:
        weather_feel = _WEATHER_FEEL.get(get_weather())
        if weather_feel:
            situation += f" {weather_feel}"
    if fam == 1:
        situation += " The player has passed through here once before — one or two plain sentences, no sentimentality."
    elif fam == 2:
        situation += " The player is very familiar with this place — one bare factual sentence only."

    db.set_state('last_direction', direction)

    context = build_context(player)
    narrative = llm.generate_narrative(situation, context)
    surrounds = w.describe_surroundings(nx, ny, nz, radius=1)
    return f"{narrative}\n{surrounds}"


def action_go_direction(player, direction):
    """Human-scale directional movement for player-entered direction commands."""
    direction = (direction or '').lower().strip()
    if direction in ('n', 's', 'e', 'w'):
        direction = {'n': 'north', 's': 'south', 'e': 'east', 'w': 'west'}[direction]

    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    if areas.is_indoors(loc) and direction in ('north', 'south', 'east', 'west'):
        if z != 0:
            return ("You are not on the ground floor. Use stairs first, or "
                    f"'step {direction}' for precise movement on this floor.")
        bld = db.get_building(loc.get('building_id')) if loc and loc.get('building_id') else None
        if not bld:
            return action_move(player, {'direction': direction})
        exit_tile = areas.building_exit_tile(bld, direction)
        if not exit_tile:
            available = areas.building_exit_directions(bld)
            if available:
                return (f"There is no clear {direction} exit from {bld['name']}. "
                        f"Known exit{'s' if len(available) != 1 else ''}: "
                        f"{', '.join(available)}. Use 'step {direction}' for precise movement inside.")
            return (f"There is no clear {direction} exit from {bld['name']}. "
                    f"Use 'step {direction}' for precise movement inside.")
        nx, ny, exit_dir = exit_tile
        db.update_character(player['id'], x=nx, y=ny, z=0, posture='standing')
        player['x'], player['y'], player['z'] = nx, ny, 0
        db.set_state('last_direction', direction)
        return f"You leave {bld['name']} by the {exit_dir} side.\n{w.describe_surroundings(nx, ny, 0, radius=1)}"

    return action_move(player, {'direction': direction})


def action_stand(player, parsed=None):
    p = db.get_character(player['id'])
    posture = p.get('posture') or 'standing'
    if posture == 'standing':
        return "You are already standing."
    db.update_character(player['id'], posture='standing')
    return "You stand up."


def action_map(player, parsed=None):
    style = (parsed or {}).get('style')
    return minimap.render_minimap(player, style=style)


_FLOOR_TARGETS = {'floor', 'ground', 'the floor', 'the ground', 'floor here', 'ground here'}


def _nearby_visible_object(target, player, radius=4):
    """Find a visible object matching target that is nearby but not at hand."""
    target = _clean_target_name(target).lower()
    if not target:
        return None
    x, y, z = player['x'], player['y'], player.get('z', 0)
    loc = db.get_location(x, y, z)
    candidates = []

    if areas.is_indoors(loc):
        objs = areas.objects_in_area(x, y, z, max_distance=radius)
        for obj in objs:
            dist = obj.get('_distance', abs(obj['x'] - x) + abs(obj['y'] - y))
            if dist == 0:
                continue
            if target in obj['name'].lower():
                candidates.append((dist, obj))
    else:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx == 0 and dy == 0:
                    continue
                nearby_loc = db.get_location(x + dx, y + dy, z)
                if nearby_loc and nearby_loc.get('building_id'):
                    continue
                for obj in db.get_objects_at(x + dx, y + dy, z):
                    if target in obj['name'].lower():
                        obj = dict(obj)
                        obj['_distance'] = abs(dx) + abs(dy)
                        candidates.append((obj['_distance'], obj))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].get('name') or ''))
    return candidates[0][1]


def _move_closer_message(obj, player, action_label='interact with'):
    direction = w._relative_dir(obj['x'] - player['x'], obj['y'] - player['y'])
    dist = obj.get('_distance')
    if dist is None:
        dist = abs(obj['x'] - player['x']) + abs(obj['y'] - player['y'])
    dist_text = f", {dist} step{'s' if dist != 1 else ''} away" if dist else ""
    return (f"You can see the {obj['name']} to the {direction}{dist_text}, "
            f"but you need to move closer to {action_label} it.")


def action_examine(player, parsed):
    target = parsed.get('target')
    if not target:
        return action_look(player, parsed)

    # "look at the floor/ground" — narrative description via LLM
    if target.lower().strip() in _FLOOR_TARGETS:
        x, y, z = player['x'], player['y'], player['z']
        loc = db.get_location(x, y, z)
        terrain = loc['terrain'] if loc else 'ground'
        building_type = None
        if loc and loc.get('building_id'):
            bld = db.get_building(loc['building_id'])
            if bld:
                building_type = bld.get('building_type')
        context = build_context(player)
        fam = _familiarity('ground')
        _mark_seen('ground')
        return llm.examine_environment(
            target='ground',
            location_name=w.get_location_summary(x, y, z),
            terrain=terrain,
            time_of_day=get_time_of_day(),
            weather=get_weather(),
            context=context,
            familiarity=fam,
            building_type=building_type,
        )

    # "look out the window" — describe what's visible outside
    if any(kw in target.lower() for kw in ('window', 'outside', 'out the')):
        x, y, z = player['x'], player['y'], player['z']
        outside = _outdoor_view_from(x, y, z)
        loc = db.get_location(x, y, z)
        building_type = None
        if loc and loc.get('building_id'):
            bld = db.get_building(loc['building_id'])
            if bld:
                building_type = bld.get('building_type')
        if outside:
            context = build_context(player)
            return llm.generate_narrative(
                f"The player looks out through the window. Outside: {outside}. "
                f"Weather: {get_weather()}. Time: {get_time_of_day()}.",
                context,
            )
        return "There is no window here to look through."

    careful = parsed.get('careful', False)
    x, y, z = player['x'], player['y'], player['z']

    # Self-examination
    _SELF_TARGETS = ('myself', 'me', 'my hands', 'my body', 'my face',
                     'myself in the mirror', 'my reflection', 'my clothes')
    if target.lower().strip() in _SELF_TARGETS:
        p = db.get_character(player['id'])
        cond = _player_condition_summary(p)
        context = build_context(player)
        return llm.generate_narrative(
            f"The player looks at themselves. Name: {p['name']}. "
            f"Health: {p['health']}/100. Physical state: {cond}.",
            context,
        )

    # Check objects at location
    obj = db.find_object_by_name(target, x=x, y=y, z=z)
    if not obj:
        nearby = _nearby_visible_object(target, player)
        if nearby:
            return _move_closer_message(nearby, player, 'examine')

        # Check inventory
        inv_ids = db.get_character_inventory(player['id'])
        for oid in inv_ids:
            o = db.get_object(oid)
            if o and target.lower() in o['name'].lower():
                obj = o
                break

    if obj:
        obj_key = f'obj:{obj["id"]}'
        fam = 0 if careful else _familiarity(obj_key)
        _mark_seen(obj_key)

        props = json.loads(obj.get('properties', '{}'))
        condition = _obj_condition_str(props)
        in_hand = obj.get('owner_id') == player['id']
        held_note = "The player is holding it in their hand." if in_hand else ""
        condition_note = f"Its condition: {condition}." if condition else ""
        fam_note = (
            "" if fam == 0 else
            " The player has seen this before — one or two sentences." if fam == 1 else
            " The player knows this object well — one sentence only."
        )
        context = build_context(player)
        desc = llm.generate_narrative(
            f"Examining a {obj['name']}: {obj['description']}. "
            f"{condition_note} {held_note}{fam_note}".strip(),
            context,
        )
        return desc

    # Check characters at current tile AND adjacent tiles (radius 1)
    def _examine_npc(c, careful):
        npc_key = f'npc:{c["id"]}'
        fam = 0 if careful else _familiarity(npc_key)
        _mark_seen(npc_key)
        context = build_context(player)
        return llm.examine_npc(
            npc_name=c['name'],
            npc_age=c.get('age', '?'),
            npc_occupation=c.get('occupation', 'villager'),
            npc_personality=c.get('personality', ''),
            npc_mood=c.get('mood', 'neutral'),
            npc_activity=c.get('current_activity', ''),
            context=context,
            familiarity=fam,
        )

    chars = db.get_characters_at(x, y, z)
    for c in chars:
        if c.get('is_player'):
            continue
        if target.lower() in c['name'].lower():
            return _examine_npc(c, careful)

    # Widen NPC search to nearby tiles (radius 4)
    for adj_dy in range(-4, 5):
        for adj_dx in range(-4, 5):
            if adj_dx == 0 and adj_dy == 0:
                continue
            for c in db.get_characters_at(x + adj_dx, y + adj_dy, z):
                if c.get('is_player'):
                    continue
                if target.lower() in c['name'].lower():
                    return _examine_npc(c, careful)

    # Fall back to environmental feature (sky, ground, wall, river, etc.)
    loc = db.get_location(x, y, z)
    building_type = None
    if loc and loc.get('building_id'):
        bld = db.get_building(loc['building_id'])
        if bld:
            building_type = bld.get('building_type')
    env_key = f'env:{x},{y},{z}:{target.lower()[:20]}'
    fam = 0 if careful else _familiarity(env_key,
                                         player_x=x, player_y=y, loc_x=x, loc_y=y)
    _mark_seen(env_key)
    return llm.examine_environment(
        target=target,
        location_name=w.get_location_summary(x, y, z),
        terrain=loc['terrain'] if loc else 'ground',
        time_of_day=get_time_of_day(),
        weather=get_weather(),
        context=build_context(player),
        familiarity=fam,
        building_type=building_type,
    )


def action_take(player, parsed):
    target = parsed.get('target')
    if not target:
        return "What do you want to take?"

    targets = _split_targets(target)
    if len(targets) > 1:
        results = []
        for part in targets:
            results.append(action_take(player, {'target': part}))
        return '\n'.join(results)

    target = _clean_target_name(target)

    x, y, z = player['x'], player['y'], player['z']
    obj = db.find_object_by_name(target, x=x, y=y, z=z)

    # Only block with "already have it" when there is nothing here to take instead
    if not obj:
        for oid in db.get_character_inventory(player['id']):
            o = db.get_object(oid)
            if o and target.lower() in o['name'].lower():
                return f"You already have the {o['name']}."

    if not obj:
        nearby = _nearby_visible_object(target, player)
        if nearby:
            return _move_closer_message(nearby, player, 'take')
        return f"There is no '{target}' here to take."
    if not obj.get('is_portable', 1):
        return f"The {obj['name']} cannot be moved."
    loc = db.get_location(x, y, z)
    if loc and loc.get('building_id'):
        bld = db.get_building(loc['building_id'])
        if bld and _shop_stock(obj, bld['building_type']):
            return f"The {obj['name']} is stock for sale here. Try buying it."
    _theft = False
    if obj.get('owner_id'):
        owner = db.get_character(obj['owner_id'])
        if owner:
            _confusion = get_confusion(player)
            if _confusion < 35:
                return (f"The {obj['name']} belongs to {owner['name']}. "
                        "You think better of it.")
            if _confusion < 60 and random.random() < 0.55:
                return (f"You reach toward the {obj['name']}, then catch yourself. "
                        "Not yours. Even now you know that much.")
            # High confusion — inhibition fails, theft proceeds
            _theft = True
            db.update_character(player['id'],
                                stress=min(100, player.get('stress', 0) + 15))

    # Record what condition the object was in when picked up
    loc = loc or db.get_location(x, y, z)
    terrain = (loc.get('terrain') or '') if loc else ''
    condition = _pickup_condition(terrain, get_weather())
    props = json.loads(obj.get('properties') or '{}')
    if condition:
        props['condition'] = condition
    elif 'condition' not in props:
        props['condition'] = 'clean'

    db.add_to_inventory(player['id'], obj['id'])
    db.update_object(obj['id'], x=None, y=None, z=None,
                     owner_id=player['id'], properties=json.dumps(props))
    db.log_event(int(db.get_state('game_ticks', 0)), 'take', f"Player took {obj['name']}", x, y, [player['id']], [obj['id']])
    if _theft:
        return f"Your hand moves before your conscience does. You take the {obj['name']}."
    return f"You pick up the {obj['name']}."


def _forage_candidates_at(x, y, z):
    """Objects on this exact tile that can be foraged from."""
    out = []
    for o in db.get_objects_at(x, y, z):
        props = json.loads(o.get('properties') or '{}')
        if props.get('forage_yield'):
            out.append((o, props))
    return out


def _matches_forage_target(target, obj, props):
    if not target:
        return True
    t = target.lower().strip()
    name = obj['name'].lower()
    yld = (props.get('forage_yield') or '').lower()
    return (t in name or name in t
            or (yld and (t in yld or yld in t)))


def action_forage(player, parsed):
    target = (parsed.get('target') or '').strip()
    x, y, z = player['x'], player['y'], player['z']

    candidates = _forage_candidates_at(x, y, z)
    if not candidates:
        return "There is nothing here you could forage."

    match = None
    for obj, props in candidates:
        if _matches_forage_target(target, obj, props):
            match = (obj, props)
            break

    if not match:
        available = ", ".join(o['name'] for o, _ in candidates)
        return f"There is nothing like '{target}' here to forage. You could try: {available}."

    obj, props = match
    yield_name = props.get('forage_yield', 'something')
    nourishment = props.get('forage_nourishment')
    hydration = props.get('forage_hydration')
    liquid = props.get('forage_liquid')

    item_props = {}
    if nourishment is not None:
        item_props['edible'] = True
        item_props['nourishment'] = nourishment
    if hydration is not None:
        item_props['drinkable'] = True
        item_props['hydration'] = hydration
        if liquid:
            item_props['liquid'] = liquid

    item_id = db.insert_object({
        'name': yield_name,
        'object_type': 'food',
        'description': f"Foraged from the {obj['name']}.",
        'is_portable': 1,
        'value': 0,
        'weight': 1,
        'owner_id': player['id'],
        'properties': json.dumps(item_props),
    })
    db.add_to_inventory(player['id'], item_id)

    _apply_work_cost(player, ticks_advance=2, hunger_up=1, thirst_up=1, energy_down=1)

    db.log_event(int(db.get_state('game_ticks', 0)), 'forage',
                 f"Player foraged {yield_name} from {obj['name']}", x, y, [player['id']], [item_id])

    if liquid:
        return f"You draw some {yield_name} from the {obj['name']}."
    return f"You gather some {yield_name} from the {obj['name']}."


def action_drop(player, parsed):
    target = parsed.get('target')
    if not target:
        return "What do you want to drop?"

    x, y, z = player['x'], player['y'], player['z']

    if target.lower() in ('everything', 'all', 'it all', 'my stuff'):
        inv_ids = list(db.get_character_inventory(player['id']))
        if not inv_ids:
            return "You have nothing to drop."
        dropped = []
        for oid in inv_ids:
            obj = db.get_object(oid)
            if obj:
                loc = db.get_location(x, y, z)
                terrain = (loc.get('terrain') or '') if loc else ''
                new_condition = _pickup_condition(terrain, get_weather())
                props = json.loads(obj.get('properties') or '{}')
                if new_condition:
                    props['condition'] = new_condition
                db.remove_from_inventory(player['id'], oid)
                db.update_object(oid, x=x, y=y, z=z, owner_id=None,
                                 state='dropped', properties=json.dumps(props))
                dropped.append(obj['name'])
        return "You set down: " + ', '.join(dropped) + "."

    inv_ids = db.get_character_inventory(player['id'])
    for oid in inv_ids:
        obj = db.get_object(oid)
        if obj and target.lower() in obj['name'].lower():
            x, y, z = player['x'], player['y'], player['z']
            loc = db.get_location(x, y, z)
            terrain = (loc.get('terrain') or '') if loc else ''
            new_condition = _pickup_condition(terrain, get_weather())
            props = json.loads(obj.get('properties') or '{}')
            if new_condition:
                props['condition'] = new_condition
            db.remove_from_inventory(player['id'], oid)
            db.update_object(oid, x=x, y=y, z=z, owner_id=None,
                             state='dropped', properties=json.dumps(props))
            db.log_event(int(db.get_state('game_ticks', 0)), 'drop', f"Player dropped {obj['name']}", x, y, [player['id']], [oid])
            return f"You set down the {obj['name']}."

    return f"You don't have a '{target}'."


_GENERIC_ADDRESS = ('anyone', 'everyone', 'someone', 'anybody', 'everybody',
                    'people', 'crowd', 'all', 'them', 'the others')


def action_speak(player, parsed):
    # Keep speech and target separate — don't let an empty speech fall through to target
    _raw_speech = parsed.get('speech') or ''
    target_name = (parsed.get('target') or '').strip()
    speech = _raw_speech.strip()

    # Parser sometimes includes "to [name]" in speech — strip it
    if target_name and speech.lower().endswith(f' to {target_name.lower()}'):
        speech = speech[:-(len(target_name) + 4)].strip()

    # "talk to anyone/everyone/someone" (or bare "talk" with no target) → list present NPCs
    target_lower = target_name.lower()
    is_generic_target = (not target_lower or target_lower in _GENERIC_ADDRESS
                         or speech.lower() in _GENERIC_ADDRESS)
    if (not target_name and not speech) or (is_generic_target and not speech):
        x, y, z = player['x'], player['y'], player['z']
        loc = db.get_location(x, y, z)
        if areas.is_indoors(loc):
            others = areas.characters_in_area(x, y, z, max_distance=8)
        else:
            others = [c for c in db.get_characters_at(x, y, z)
                      if c['id'] != player['id']]
        if not others:
            return "There is nobody here to talk to."
        names = ', '.join(knowledge.npc_display_name(c, c.get('_distance', 0), True)
                          for c in others[:4])
        return (f"Who would you like to speak to? "
                f"{'Here' if len(others) == 1 else 'Present'}: {names}.")
    # Specific named target with no words — greet them
    if not speech and target_name and not is_generic_target:
        speech = "Hello."

    x, y, z = player['x'], player['y'], player['z']
    # Indoors, speech is room/building-scale rather than one-grid-cell scale.
    loc = db.get_location(x, y, z)
    if areas.is_indoors(loc):
        chars_here = areas.characters_in_area(x, y, z, max_distance=8)
        chars_here.append(db.get_character(player['id']))
    else:
        chars_here = list(db.get_characters_at(x, y, z))
        for adj_dy, adj_dx in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            for c in db.get_characters_at(x + adj_dx, y + adj_dy, z):
                if not any(e['id'] == c['id'] for e in chars_here):
                    chars_here.append(c)
    others = [c for c in chars_here if c['id'] != player['id']]

    # If a specific person is named, find them by name OR occupation
    if target_name:
        named_here = _match_npc(target_name, others)
        if not named_here:
            # Widen search to cells 2-3 tiles away
            for sdy in range(-3, 4):
                for sdx in range(-3, 4):
                    if abs(sdx) <= 1 and abs(sdy) <= 1:
                        continue
                    nearby = [c for c in db.get_characters_at(x + sdx, y + sdy, z)
                               if not c.get('is_player')]
                    found = _match_npc(target_name, nearby)
                    if found:
                        return (f'You call out to {found["name"]}, '
                                f'but they are too far away to hear you clearly.')
            return f'There is nobody called {target_name} here.'
        others = [named_here]

    you_say = f'You say: "{speech}"'

    if not others:
        return f'{you_say}\nYour words hang in the empty air.'

    # Direction-asking gets a deterministic, grid-accurate answer rather than
    # an LLM-improvised one — a villager can be relied on to know the way.
    direction_bld = _direction_request_target(speech)
    if direction_bld:
        npc = others[0]
        knowledge.mark_seen('npc', npc['id'], 'spoken')
        return f'{you_say}\n{npc["name"]} says: "{_spoken_route(player, direction_bld)}"'

    # Store the player's words in each responding NPC's memory
    player_name = db.get_character(player['id'])['name']
    responses = []
    context = build_context(player)
    sprite_parts = []
    for npc in others[:3]:
        met_key = f'met:{npc["id"]}'
        if db.get_state(met_key) != '1':
            db.set_state(met_key, '1')
            knowledge.mark_seen('npc', npc['id'], 'met')
            import sprites as _sprites
            art = _sprites.render_npc_sprite(npc['name'])
            if art:
                sprite_parts.append(art)
        else:
            knowledge.mark_seen('npc', npc['id'], 'spoken')
        history = _npc_memory_get(npc['id'])
        _npc_memory_add(npc['id'], player_name, speech, 'player_said')
        reply = llm.generate_npc_response(
            npc_name=npc['name'],
            npc_personality=npc['personality'],
            npc_mood=npc['mood'],
            player_speech=speech,
            context=context,
            conversation_history=history,
        )
        _npc_memory_add(npc['id'], npc['name'], reply, 'self_said')
        responses.append(f'{npc["name"]} says: "{reply}"')

    result = you_say + '\n' + '\n'.join(responses)
    if sprite_parts:
        result = '\n'.join(sprite_parts) + '\n' + result
    return result


def _contains_any(text, words):
    text = (text or '').lower()
    return any(word in text for word in words)


# Patterns recognising "where is X" / "how do I get to X" style questions, so
# we can answer with a route grounded in the actual grid rather than letting
# the LLM invent streets, turns, and landmarks that don't exist in Millhaven.
_DIRECTION_PHRASES = [
    r"where(?:'s| is| are)\s+(?:the\s+)?(.+)",
    r"how (?:do|can|might) i get to\s+(?:the\s+)?(.+)",
    r"how to get to\s+(?:the\s+)?(.+)",
    r"which way (?:is it )?to\s+(?:the\s+)?(.+)",
    r"what(?:'s| is) the (?:way|best way|quickest way) to\s+(?:the\s+)?(.+)",
    r"(?:directions?|the way|route|path)\s+to\s+(?:the\s+)?(.+)",
    r"(?:direct|point|guide|take|show)\s+me\s+(?:the way\s+)?to\s+(?:the\s+)?(.+)",
]
_DIRECTION_RE = [re.compile(p, re.I) for p in _DIRECTION_PHRASES]
_PLACE_STOPWORDS = {'the', 'a', 'an', 'to', 'from', 'here', 'please', 'nearest',
                    'local', 'closest', 'is', 'it', 'me', 'can', 'you'}


def _direction_request_target(speech):
    """If `speech` is asking the way to a place, return the matching building
    row (or None). Resolution is purely against the `buildings` table, so the
    result can never name a place that doesn't actually exist on the map."""
    text = (speech or '').lower().strip().rstrip('?.! ')
    if not text:
        return None
    phrase = None
    for rx in _DIRECTION_RE:
        m = rx.search(text)
        if m:
            phrase = m.group(1)
            break
    if not phrase:
        return None
    words = [w_ for w_ in re.findall(r"[a-z']+", phrase) if w_ not in _PLACE_STOPWORDS]
    if not words:
        return None
    bld = areas.building_named(' '.join(words))
    if bld:
        return bld
    # The phrase may include extra words that don't match anything ("mill road
    # bakery" when the place is just called "The Old Bakery") — try shrinking
    # from the front until a real building name/type is matched.
    for i in range(len(words) - 1, -1, -1):
        bld = areas.building_named(' '.join(words[i:]))
        if bld:
            return bld
    return None


def _spoken_route(player, bld):
    """Plain, grid-accurate description of how to walk from the player to a
    building's entrance — compass direction, rough distance, and (if useful)
    the name of the road/landmark just outside its door. Built entirely from
    real coordinates so it always matches what the player will actually see."""
    px, py, pz = player['x'], player['y'], player['z']
    ex, ey = bld['entrance_x'], bld['entrance_y']
    if pz == 0 and abs(ex - px) <= 1 and abs(ey - py) <= 1:
        return f"You're right outside it now — that's {bld['name']}, just here."

    dx, dy = ex - px, ey - py
    direction = w._relative_dir(dx, dy)
    dist = max(abs(dx), abs(dy))

    landmark = None
    exit_tile = areas.building_exit_tile(bld)
    if exit_tile:
        loc = db.get_location(exit_tile[0], exit_tile[1], 0)
        name = loc.get('name') if loc else None
        if name and name != bld['name']:
            landmark = name

    if dist <= 4:
        lead = f"It's just {direction} of here"
    elif dist <= 12:
        lead = f"Head {direction} for a few minutes"
    else:
        lead = f"It's a fair walk — head {direction}"

    if landmark:
        return f"{lead}, by {landmark}, and you'll come to {bld['name']}."
    return f"{lead} and you'll come to {bld['name']}."


def _emergency_intent(text, player):
    p = db.get_character(player['id'])
    text_l = (text or '').lower()
    flags = {
        'food': _contains_any(text_l, _FOOD_WORDS),
        'water': _contains_any(text_l, _WATER_WORDS),
        'help': _contains_any(text_l, _HELP_WORDS),
        'pay': _contains_any(text_l, _PAY_WORDS),
        'shelter': _contains_any(text_l, _SHELTER_WORDS),
    }
    distressed = (
        p['hunger'] >= 75 or p.get('thirst', 30) >= 75 or p['energy'] <= 15
        or db.get_state('passed_out') == '1'
    )
    if flags['help'] and distressed:
        flags['food'] = flags['food'] or p['hunger'] >= 70
        flags['water'] = flags['water'] or p.get('thirst', 30) >= 70
    return flags if any(flags.values()) else None


def _aid_people_near_player(player):
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    if areas.is_indoors(loc):
        people = areas.characters_in_area(x, y, z, max_distance=10)
    else:
        people = []
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                for c in db.get_characters_at(x + dx, y + dy, z):
                    if c.get('is_player'):
                        continue
                    c = dict(c)
                    c['_distance'] = abs(dx) + abs(dy)
                    people.append(c)
    seen = set()
    unique = []
    for p in people:
        if p['id'] in seen:
            continue
        seen.add(p['id'])
        unique.append(p)
    unique.sort(key=lambda c: (c.get('_distance', 0), c.get('name') or ''))
    return unique


def _npc_role_text(npc):
    return f"{npc.get('occupation') or ''} {npc.get('personality') or ''}".lower()


def _can_give_food(npc):
    role = _npc_role_text(npc)
    return any(token in role for token in _AID_FOOD_ROLES)


def _can_give_water(npc):
    role = _npc_role_text(npc)
    return any(token in role for token in _AID_WATER_ROLES)


def _aid_clue(npc, flags):
    role = _npc_role_text(npc)
    for key, clue in _FOOD_CLUES.items():
        if key in role:
            return clue
    if flags.get('food') or flags.get('water'):
        return "Try the inn, bakery, shop, farm, or any visible water source."
    return "They do not seem able to help with that."


def _apply_need_aid(player, hunger_delta=0, thirst_delta=0, energy_delta=0, stress_delta=0, cost=0):
    p = db.get_character(player['id'])
    money = max(0, p.get('money', 0) - cost)
    hunger = max(0, p['hunger'] - hunger_delta)
    thirst = max(0, p.get('thirst', 30) - thirst_delta)
    energy = min(100, p['energy'] + energy_delta)
    stress = max(0, p.get('stress', 0) - stress_delta)
    db.update_character(player['id'], hunger=hunger, thirst=thirst, energy=energy,
                        stress=stress, money=money, posture='standing' if energy > 0 else p.get('posture'))
    player['hunger'] = hunger
    player['thirst'] = thirst
    player['energy'] = energy
    player['money'] = money
    if energy > 0:
        db.set_state('passed_out', '0')


def _handle_emergency_aid(player, flags, npc=None, speech=''):
    people = _aid_people_near_player(player)
    if npc:
        people = [npc] + [p for p in people if p['id'] != npc['id']]
    if not people:
        return "You call for help, but no one close enough responds."

    helper = None
    for candidate in people:
        if ((flags.get('food') and _can_give_food(candidate))
                or (flags.get('water') and _can_give_water(candidate))
                or (flags.get('help') and (_can_give_food(candidate) or _can_give_water(candidate)))):
            helper = candidate
            break
    if helper is None:
        helper = people[0]

    knowledge.mark_seen('npc', helper['id'], 'met')
    role = _npc_role_text(helper)
    merchant = any(token in role for token in ('innkeeper', 'baker', 'shopkeeper'))
    can_food = _can_give_food(helper)
    can_water = _can_give_water(helper)
    p = db.get_character(player['id'])

    hunger_delta = 0
    thirst_delta = 0
    energy_delta = 0
    stress_delta = 5
    cost = 0
    offered = []

    if flags.get('water') or (flags.get('help') and p.get('thirst', 30) >= 75):
        if can_water:
            thirst_delta = 40
            energy_delta += 6
            offered.append("water")
    if flags.get('food') or (flags.get('help') and p['hunger'] >= 75):
        if can_food:
            hunger_delta = 30
            energy_delta += 5
            offered.append("bread")

    if offered:
        if merchant and flags.get('pay') and p.get('money', 0) >= 2:
            cost = 2
        _apply_need_aid(player, hunger_delta=hunger_delta, thirst_delta=thirst_delta,
                        energy_delta=energy_delta, stress_delta=stress_delta, cost=cost)
        paid = f" You pay {cost}p." if cost else ""
        if len(offered) == 2:
            item_text = "water and a piece of bread"
        else:
            item_text = "some " + offered[0] if offered[0] == "water" else "a piece of bread"
        if db.get_state('passed_out') == '0' and p['energy'] == 0:
            recovery = " You recover enough strength to sit up."
        else:
            recovery = ""
        return f'{helper["name"]} helps you with {item_text}.{paid}{recovery}'

    clue = _aid_clue(helper, flags)
    return f'{helper["name"]} cannot help directly. {clue}'


def _look_for_needs(player, want_food=True, want_water=True):
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    if areas.is_indoors(loc):
        objs = areas.objects_in_area(x, y, z, max_distance=6)
    else:
        objs = []
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                for obj in db.get_objects_at(x + dx, y + dy, z):
                    obj = dict(obj)
                    obj['_distance'] = abs(dx) + abs(dy)
                    obj['_dir'] = _first_step_toward(dx, dy) if dx or dy else 'here'
                    objs.append(obj)
    food = []
    water = []
    for obj in objs:
        props = json.loads(obj.get('properties') or '{}')
        dist = obj.get('_distance', abs(obj['x'] - x) + abs(obj['y'] - y))
        direction = obj.get('_dir') or w._relative_dir(obj['x'] - x, obj['y'] - y)
        suffix = "here" if dist == 0 else f"{direction}, {dist} step{'s' if dist != 1 else ''}"
        if want_food and props.get('edible'):
            food.append(f"{obj['name']} ({suffix})")
        if want_water and (props.get('drinkable') or props.get('liquid') or props.get('water')):
            water.append(f"{obj['name']} ({suffix})")

    people = _aid_people_near_player(player)
    helpers = []
    for npc in people[:6]:
        if ((want_food and _can_give_food(npc)) or (want_water and _can_give_water(npc))):
            helpers.append(knowledge.npc_display_name(npc, npc.get('_distance', 0), same_area=areas.is_indoors(loc)))

    parts = []
    if food:
        parts.append("Food you can account for: " + "; ".join(food[:6]) + ".")
    if water:
        parts.append("Drinkable water or liquid: " + "; ".join(water[:6]) + ".")
    if helpers:
        parts.append("People who may be able to help: " + ", ".join(helpers[:4]) + ".")
    if parts:
        return " ".join(parts)

    if loc and loc.get('building_id'):
        bld = db.get_building(loc['building_id'])
        if bld and bld.get('building_type') == 'farm':
            return "You see no food laid out, but this is a working farm. Ask the household for bread or water, or look for the pump outside."
    return "You do not see any obvious food or drink here."


def action_ask(player, parsed):
    target = (parsed.get('target') or '').strip()
    speech = parsed.get('speech') or parsed.get('interpretation') or 'something'

    x, y, z = player['x'], player['y'], player['z']
    # Indoors, use the interaction area rather than exact tile adjacency.
    loc = db.get_location(x, y, z)
    if areas.is_indoors(loc):
        chars_here = areas.characters_in_area(x, y, z, max_distance=8)
    else:
        chars_here = list(db.get_characters_at(x, y, z))
        for adj_dy, adj_dx in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            for c in db.get_characters_at(x + adj_dx, y + adj_dy, z):
                if not any(e['id'] == c['id'] for e in chars_here):
                    chars_here.append(c)
    chars_here = [c for c in chars_here if not c.get('is_player')]

    npc = _match_npc(target, chars_here) if target else (chars_here[0] if chars_here else None)
    flags = _emergency_intent(f"{target} {speech}", player)

    if not npc:
        if flags:
            return _handle_emergency_aid(player, flags, None, speech)
        if target:
            # Check cells 2-3 tiles away
            for sdy in range(-3, 4):
                for sdx in range(-3, 4):
                    if abs(sdx) <= 1 and abs(sdy) <= 1:
                        continue
                    nearby = [c for c in db.get_characters_at(x + sdx, y + sdy, z)
                               if not c.get('is_player')]
                    found = _match_npc(target, nearby)
                    if found:
                        return (f'You call out to {found["name"]}, '
                                f'but they are too far away to hear you.')
        return "There is nobody here to ask."

    if flags:
        return _handle_emergency_aid(player, flags, npc, speech)

    direction_bld = _direction_request_target(speech)
    if direction_bld:
        knowledge.mark_seen('npc', npc['id'], 'spoken')
        return (f'You ask about {speech}.\n'
                f'{npc["name"]} says: "{_spoken_route(player, direction_bld)}"')

    player_name = db.get_character(player['id'])['name']
    met_key = f'met:{npc["id"]}'
    sprite_prefix = ''
    if db.get_state(met_key) != '1':
        db.set_state(met_key, '1')
        knowledge.mark_seen('npc', npc['id'], 'met')
        import sprites as _sprites
        art = _sprites.render_npc_sprite(npc['name'])
        if art:
            sprite_prefix = art + '\n'
    else:
        knowledge.mark_seen('npc', npc['id'], 'spoken')
    history = _npc_memory_get(npc['id'])
    _npc_memory_add(npc['id'], player_name, speech, 'player_said')
    context = build_context(player)
    reply = llm.generate_npc_response(
        npc_name=npc['name'],
        npc_personality=npc['personality'],
        npc_mood=npc['mood'],
        player_speech=speech,
        context=context,
        conversation_history=history,
    )
    _npc_memory_add(npc['id'], npc['name'], reply, 'self_said')
    return sprite_prefix + f'You ask about {speech}.\n{npc["name"]} says: "{reply}"'


def action_give(player, parsed):
    target_raw = (parsed.get('target') or '').strip()
    x, y, z = player['x'], player['y'], player['z']

    # Parse "give <obj> to <recipient>" — split on " to "
    obj_name = target_raw
    recipient_hint = ''
    tl = target_raw.lower()
    if ' to ' in tl:
        split_idx = tl.index(' to ')
        obj_name = target_raw[:split_idx].strip()
        recipient_hint = target_raw[split_idx + 4:].strip()

    # If no split found, check whether target matches any inventory item.
    # If it doesn't, treat it as a recipient name (e.g. "pay tom").
    inv_ids = db.get_character_inventory(player['id'])
    if obj_name and not recipient_hint:
        item_match = any(
            obj_name.lower() in (db.get_object(oid) or {}).get('name', '').lower()
            for oid in inv_ids
        )
        if not item_match:
            recipient_hint = obj_name
            obj_name = ''

    # Find recipient: same tile + immediately adjacent tiles
    close_chars = list(db.get_characters_at(x, y, z))
    for adj_dy, adj_dx in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
        for c in db.get_characters_at(x + adj_dx, y + adj_dy, z):
            if not any(e['id'] == c['id'] for e in close_chars):
                close_chars.append(c)
    others = [c for c in close_chars if not c.get('is_player')]

    recipient = (_match_npc(recipient_hint, others) if recipient_hint
                 else (others[0] if others else None))

    if not recipient:
        if recipient_hint:
            for sdy in range(-3, 4):
                for sdx in range(-3, 4):
                    if abs(sdx) <= 1 and abs(sdy) <= 1:
                        continue
                    nearby = [c for c in db.get_characters_at(x + sdx, y + sdy, z)
                               if not c.get('is_player')]
                    found = _match_npc(recipient_hint, nearby)
                    if found:
                        return (f"You need to be closer to {found['name']} "
                                f"to give them something.")
        return "There is nobody here to give anything to."

    if not inv_ids:
        return "You have nothing to give."

    obj = None
    for oid in inv_ids:
        o = db.get_object(oid)
        if o and (not obj_name or obj_name.lower() in o['name'].lower()):
            obj = o
            break

    if not obj:
        if obj_name:
            return f"You don't have '{obj_name}'."
        return "You have nothing to give."

    db.remove_from_inventory(player['id'], obj['id'])
    db.add_to_inventory(recipient['id'], obj['id'])
    db.update_object(obj['id'], owner_id=recipient['id'])

    context = build_context(player)
    narrative = llm.generate_narrative(
        f"Player gives the {obj['name']} to {recipient['name']}.",
        context
    )
    return narrative


def action_buy(player, parsed):
    target = parsed.get('target') or ''
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    building_id = loc.get('building_id') if loc else None

    if not building_id:
        return "There is no shop here."

    bld = db.get_building(building_id)
    if not bld or bld['building_type'] not in ('shop', 'inn', 'bakery', 'market'):
        return f"{bld['name'] if bld else 'This place'} does not sell things."

    conn = __import__('sqlite3').connect(__import__('config').DB_PATH)
    conn.row_factory = __import__('sqlite3').Row
    rows = conn.execute("""
        SELECT o.* FROM objects o
        JOIN locations l ON o.x=l.x AND o.y=l.y AND o.z=l.z
        WHERE l.building_id=? AND o.is_visible=1 AND o.owner_id IS NULL
    """, (building_id,)).fetchall()
    conn.close()

    matches = []
    for row in rows:
        obj = dict(row)
        if not _shop_stock(obj, bld['building_type']):
            continue
        if target and target.lower() not in obj['name'].lower():
            continue
        matches.append(obj)

    if not matches:
        # List what is actually for sale rather than a bare failure
        available = [o['name'] for o in [dict(r) for r in rows]
                     if _shop_stock(dict(r) if not isinstance(o, dict) else o, bld['building_type'])]
        # Recompute cleanly
        available = []
        for row in rows:
            o = dict(row)
            if _shop_stock(o, bld['building_type']):
                available.append(f"{o['name']} ({o.get('value', 0)}p)")
        if available:
            return (f"There is no '{target}' for sale here. "
                    f"Available: {', '.join(available[:8])}.")
        return f"There is nothing for sale here at the moment."

    obj = matches[0]
    price = obj.get('value', 0)
    money = player.get('money', 0)

    if money < price:
        return f"The {obj['name']} costs {price}p. You only have {money}p."

    db.update_character(player['id'], money=money - price)
    player['money'] = money - price
    db.add_to_inventory(player['id'], obj['id'])
    db.update_object(obj['id'], owner_id=player['id'], x=None, y=None, z=None)
    db.log_event(int(db.get_state('game_ticks', 0)), 'buy', f"Player bought {obj['name']} for {price}p", x, y, [player['id']], [obj['id']])
    return f"You buy the {obj['name']} for {price}p. You have {player['money']}p remaining."


def action_sell(player, parsed):
    target = parsed.get('target') or ''
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    building_id = loc.get('building_id') if loc else None

    if not building_id:
        return "There is nobody here to buy anything."

    bld = db.get_building(building_id)
    if not bld or bld['building_type'] not in ('shop', 'inn', 'bakery', 'market'):
        return f"{bld['name'] if bld else 'This place'} does not buy things."

    inv_ids = db.get_character_inventory(player['id'])
    obj = None
    for oid in inv_ids:
        candidate = db.get_object(oid)
        if candidate and (not target or target.lower() in candidate['name'].lower()):
            obj = candidate
            break

    if not obj:
        return f"You don't have '{target}'."

    sale_value = max(1, obj.get('value', 0) // 2 or 1)
    money = player.get('money', 0) + sale_value
    db.remove_from_inventory(player['id'], obj['id'])
    db.update_character(player['id'], money=money)
    player['money'] = money
    db.update_object(obj['id'], owner_id=None, x=None, y=None, z=None, is_visible=0, state='sold')
    db.log_event(int(db.get_state('game_ticks', 0)), 'sell', f"Player sold {obj['name']} for {sale_value}p", x, y, [player['id']], [obj['id']])
    return f"You sell the {obj['name']} for {sale_value}p. You now have {player['money']}p."


def _people_present(player):
    """NPCs sharing the player's room/area (indoors) or tile (outdoors)."""
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    if areas.is_indoors(loc):
        return [c for c in areas.characters_in_area(x, y, z, max_distance=8)
                if not c.get('is_player')]
    return [c for c in db.get_characters_at(x, y, z) if not c.get('is_player')]


def _apply_work_cost(player, ticks_advance=16, hunger_up=12, thirst_up=10, energy_down=18):
    ticks = int(db.get_state('game_ticks', 0))
    db.set_state('game_ticks', ticks + ticks_advance)
    p = db.get_character(player['id'])
    new_hunger = min(100, p['hunger'] + hunger_up)
    new_thirst = min(100, p.get('thirst', 30) + thirst_up)
    new_energy = max(0, p['energy'] - energy_down)
    db.update_character(player['id'], hunger=new_hunger, thirst=new_thirst, energy=new_energy)
    player['hunger'] = new_hunger
    player['thirst'] = new_thirst
    player['energy'] = new_energy


def _pay_player(player, amount):
    money = player.get('money', 0) + amount
    db.update_character(player['id'], money=money)
    player['money'] = money
    return money


def action_work(player, parsed):
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    building_id = loc.get('building_id') if loc else None
    if not building_id:
        return "There is no work to be done out here. Try asking inside a shop, inn, farm, or workshop."

    bld = db.get_building(building_id)
    trade = _TRADES.get(bld['building_type']) if bld else None
    if not trade:
        return f"{bld['name'] if bld else 'This place'} has no work for you."

    employer = next((c for c in _people_present(player)
                     if _occupation_matches_trade(c, bld['building_type'])), None)
    if not employer:
        return "Nobody here is able to put you to work right now."

    skill_name = trade['skill']
    level = _player_skill(player, skill_name)
    task = trade['task_label']

    if not trade['skilled']:
        pay = trade['base_pay'] + random.randint(-1, 1)
        pay = max(1, pay)
        _apply_work_cost(player)
        _pay_player(player, pay)
        db.log_event(int(db.get_state('game_ticks', 0)), 'work',
                     f"Player worked for {employer['name']} ({task})", x, y, [player['id'], employer['id']], [])
        return (f"You spend a few hours {task} alongside {employer['name']}. "
                f"It's plain, honest work — anyone could do it. "
                f"{employer['name']} pays you {pay}p for your trouble. You now have {player['money']}p.")

    # Skilled trade
    if level >= 50:
        bonus = random.randint(0, 3)
        pay = trade['base_pay'] + bonus
        _apply_work_cost(player)
        _pay_player(player, pay)
        gain = _adjust_skill(player, skill_name, random.randint(0, 1))
        db.log_event(int(db.get_state('game_ticks', 0)), 'work',
                     f"Player worked for {employer['name']} ({task}, expert)", x, y, [player['id'], employer['id']], [])
        return (f"You set to {task} with practiced confidence. {employer['name']} barely "
                f"needs to glance your way. You're paid {pay}p in full. You now have {player['money']}p. "
                f"({skill_name.capitalize()} skill: {gain}/100)")

    if level >= 20:
        pay = trade['base_pay']
        _apply_work_cost(player)
        _pay_player(player, pay)
        gain = _adjust_skill(player, skill_name, random.randint(1, 2))
        db.log_event(int(db.get_state('game_ticks', 0)), 'work',
                     f"Player worked for {employer['name']} ({task}, competent)", x, y, [player['id'], employer['id']], [])
        return (f"You make a steady job of {task}. It isn't masterful, but it's solid, "
                f"workmanlike effort. {employer['name']} pays you {pay}p. You now have {player['money']}p. "
                f"({skill_name.capitalize()} skill: {gain}/100)")

    # Haphazard — unskilled attempt at a skilled trade
    roll = random.random()
    _apply_work_cost(player)
    if roll < 0.30:
        gain = _adjust_skill(player, skill_name, random.randint(1, 2))
        db.log_event(int(db.get_state('game_ticks', 0)), 'work',
                     f"Player fumbled work for {employer['name']} ({task})", x, y, [player['id'], employer['id']], [])
        return (f"You give {task} an honest go, but without the knack for it the result is a "
                f"mess. {employer['name']} sighs and waves it away — you earn nothing for your "
                f"trouble. ({skill_name.capitalize()} skill: {gain}/100)")
    elif roll < 0.70:
        pay = trade['unskilled_pay']
        _pay_player(player, pay)
        gain = _adjust_skill(player, skill_name, random.randint(2, 3))
        db.log_event(int(db.get_state('game_ticks', 0)), 'work',
                     f"Player did rough work for {employer['name']} ({task})", x, y, [player['id'], employer['id']], [])
        return (f"You fumble through {task} — some of it usable, some of it not. "
                f"{employer['name']} pays you a token {pay}p for the part that's worth keeping. "
                f"You now have {player['money']}p. ({skill_name.capitalize()} skill: {gain}/100)")
    else:
        pay = trade['base_pay']
        _pay_player(player, pay)
        gain = _adjust_skill(player, skill_name, random.randint(2, 3))
        db.log_event(int(db.get_state('game_ticks', 0)), 'work',
                     f"Player got lucky with work for {employer['name']} ({task})", x, y, [player['id'], employer['id']], [])
        return (f"By more luck than skill, {task} comes out right today. {employer['name']} "
                f"looks pleasantly surprised and pays you the full {pay}p. You now have "
                f"{player['money']}p. ({skill_name.capitalize()} skill: {gain}/100)")


def action_learn(player, skill_phrase, npc_name=None):
    phrase = (skill_phrase or '').lower().strip()
    skill_name = None
    for word, skill in _SKILL_ALIASES.items():
        if word in phrase:
            skill_name = skill
            break
    if not skill_name:
        return "Nobody in Millhaven teaches that."

    btype, trade = _TRADE_BY_SKILL[skill_name]
    if trade['learn_fee'] is None:
        return ("Anyone can pick that up just by doing it — there's nothing "
                "anyone could formally teach you.")

    people = _people_present(player)
    if npc_name:
        npc_name = re.sub(r'^(the|a|an)\s+', '', npc_name.strip(), flags=re.IGNORECASE)
        npc = _match_npc(npc_name, people)
        if not npc:
            return f"There is nobody called {npc_name} here."
        if not _occupation_matches_trade(npc, btype):
            return f"{npc['name']} knows nothing of {trade['skill']}."
    else:
        npc = next((c for c in people if _occupation_matches_trade(c, btype)), None)
        if not npc:
            return "There's nobody here who could teach you that."

    level = _player_skill(player, skill_name)
    if level >= 60:
        return (f'{npc["name"]} shakes their head. "There\'s nothing more I can teach you '
                f'about {skill_name} — you\'ve as much sense for it as I have now. '
                f'The rest comes from doing it."')

    fee = trade['learn_fee']
    money = player.get('money', 0)
    if money < fee:
        return (f'{npc["name"]} would be glad to teach you the ways of {trade["task_label"]}, '
                f'but says it would cost you {fee}p — for the materials you would waste '
                f'while learning. You only have {money}p.')

    _pay_player(player, -fee)
    ticks = int(db.get_state('game_ticks', 0))
    db.set_state('game_ticks', ticks + 8)
    gain = max(4, (60 - level) // 4)
    new_level = _adjust_skill(player, skill_name, gain)
    db.log_event(int(db.get_state('game_ticks', 0)), 'learn',
                 f"Player learned {skill_name} from {npc['name']}", player['x'], player['y'], [player['id'], npc['id']], [])
    return (f'{npc["name"]} spends a couple of hours showing you the proper way of '
            f'{trade["task_label"]}, and takes {fee}p for their trouble. '
            f'You feel you understand {skill_name} a good deal better now. '
            f'({skill_name.capitalize()} skill: {new_level}/100)')


_SIT_WORDS   = ('chair', 'stool', 'bench', 'seat', 'pew', 'settee', 'sofa', 'couch', 'down')
_OPEN_WORDS  = ('door', 'gate', 'hatch', 'trapdoor', 'window', 'shutter', 'lock', 'latch')
_KNOCK_WORDS = ('counter', 'bar', 'door', 'table', 'wall')


def action_use(player, parsed):
    target = parsed.get('target') or ''
    if not target:
        # "sit down" with no object — check for sit intent via raw interpretation
        interp = (parsed.get('interpretation') or '').lower()
        if 'sit' in interp:
            x, y, z = player['x'], player['y'], player['z']
            for obj in db.get_objects_at(x, y, z):
                props = json.loads(obj.get('properties') or '{}')
                if (props.get('sittable') or obj.get('object_type') in ('chair', 'bench', 'seat')
                        or any(word in obj['name'].lower() for word in ('chair', 'bench', 'seat', 'stool'))):
                    db.update_character(player['id'], posture='sitting')
                    return f"You sit down on the {obj['name']}."
            return "There is nowhere obvious to sit here."
        return "What do you want to use?"

    x, y, z = player['x'], player['y'], player['z']
    target_lower = target.lower()
    use_on = None
    for sep in (' on ', ' with '):
        if sep in target_lower:
            left, right = target_lower.split(sep, 1)
            target_lower = left.strip()
            use_on = right.strip()
            break

    obj = None
    inv_ids = db.get_character_inventory(player['id'])
    for oid in inv_ids:
        candidate = db.get_object(oid)
        if candidate and target_lower in candidate['name'].lower():
            obj = candidate
            break

    if not obj:
        obj = db.find_object_by_name(target_lower, x=x, y=y, z=z)

    if not obj:
        nearby = _nearby_visible_object(target_lower, player)
        if nearby:
            action_label = 'sit on' if ('sit' in (parsed.get('interpretation') or '').lower()) else 'use'
            return _move_closer_message(nearby, player, action_label)
        tl = target_lower.strip()
        # "sit" / "sit down" / "sit on the chair"
        if tl in ('down',) or 'sit' in (parsed.get('interpretation') or '').lower():
            return "There is nowhere obvious to sit here."
        # "open the door/gate" — if exits exist, way is clear; otherwise no door
        if any(kw in tl for kw in _OPEN_WORDS):
            exits = w._describe_exits(player['x'], player['y'], player['z'])
            if exits:
                return f"The way is unobstructed. Exits: {exits}."
            return "There is no door or gate to open here."
        # "knock on counter/bar/door"
        if any(kw in tl for kw in _KNOCK_WORDS):
            x, y, z = player['x'], player['y'], player['z']
            chars = [c for c in db.get_characters_at(x, y, z) if not c.get('is_player')]
            if chars:
                return (f"You knock. {chars[0]['name']} looks up.")
            return "You knock, but there is no one to hear you."
        # Generic: generate a short narrative rather than a raw error
        context = build_context(player)
        return llm.generate_narrative(
            f"The player tries to interact with '{target}' but there is nothing of that kind here.",
            context,
        )

    props = json.loads(obj.get('properties') or '{}')
    if ('sit' in target_lower or 'sit' in (parsed.get('interpretation') or '').lower()
            or props.get('sittable') or obj.get('object_type') in ('chair', 'bench', 'seat')):
        if not (props.get('sittable') or obj.get('object_type') in ('chair', 'bench', 'seat')
                or any(word in obj['name'].lower() for word in ('chair', 'bench', 'seat', 'stool'))):
            return f"The {obj['name']} is not something you can comfortably sit on."
        db.update_character(player['id'], posture='sitting')
        return f"You sit down on the {obj['name']}."

    if props.get('edible'):
        return action_eat(player, {'target': obj['name']})

    if props.get('readable'):
        context = build_context(player)
        return llm.generate_narrative(
            f"You read the {obj['name']}. {obj.get('description') or ''}",
            context,
        )

    if props.get('key') and use_on:
        for nearby in db.get_objects_at(x, y, z):
            if use_on not in nearby['name'].lower():
                continue
            near_props = json.loads(nearby.get('properties') or '{}')
            if near_props.get('locked'):
                near_props['locked'] = False
                if near_props.get('door'):
                    near_props['open'] = True
                db.update_object(nearby['id'], properties=json.dumps(near_props))
                db.log_event(int(db.get_state('game_ticks', 0)), 'use', f"Player unlocked {nearby['name']} with {obj['name']}", x, y, [player['id']], [obj['id'], nearby['id']])
                return f"You unlock the {nearby['name']} with the {obj['name']}."
        return f"The {obj['name']} does not fit anything obvious here."

    if obj.get('object_type') == 'animal' or props.get('alive'):
        animal_name = obj['name']
        if props.get('flies'):
            return f"The {animal_name} flutters just out of reach as you approach."
        return f"The {animal_name} eyes you warily and backs away."

    context = build_context(player)
    return llm.generate_narrative(
        f"You use the {obj['name']}" + (f" on the {use_on}" if use_on else "") + ".",
        context,
    )


def action_attack(player, parsed):
    target = parsed.get('target') or ''
    if not target:
        return "Who do you want to attack?"

    x, y, z = player['x'], player['y'], player['z']
    target_lower = target.lower()
    chars = db.get_characters_at(x, y, z)
    npc = next((c for c in chars if c['id'] != player['id'] and target_lower in c['name'].lower()), None)

    inv_ids = db.get_character_inventory(player['id'])
    weapon = None
    damage = 8
    for oid in inv_ids:
        obj = db.get_object(oid)
        if not obj:
            continue
        props = json.loads(obj.get('properties') or '{}')
        if props.get('weapon'):
            if weapon is None or obj.get('value', 0) > weapon.get('value', 0):
                weapon = obj
    if weapon:
        damage = max(damage, 18)

    if npc:
        # Inhibition: a clear-headed person does not attack an innocent stranger unprovoked.
        # Confusion erodes this. Hostile/angry NPCs are fair targets regardless.
        _confusion = get_confusion(player)
        if npc.get('mood', 'neutral') not in ('hostile', 'angry', 'aggressive'):
            if _confusion < 35:
                return (f"You move toward {npc['name']}, but something stays your hand. "
                        "Whatever dark impulse moved through you, it passes.")
            if _confusion < 60 and random.random() < 0.60:
                return (f"Your hand moves toward {npc['name']}, then stills. "
                        "Your mind is clouded — but not that far gone. Not yet.")

        new_health = max(0, npc.get('health', 100) - damage)
        db.update_character(
            npc['id'],
            health=new_health,
            mood='angry' if new_health > 0 else 'defeated',
        )
        if new_health <= 0:
            db.update_character(npc['id'], is_alive=0, current_activity='incapacitated')
        p = db.get_character(player['id'])
        db.update_character(player['id'], energy=max(0, p['energy'] - 3),
                            stress=min(100, p.get('stress', 0) + 40))
        db.set_state('player_exertion', min(100, int(db.get_state('player_exertion', 0)) + 35))
        db.log_event(int(db.get_state('game_ticks', 0)), 'attack', f"Player attacked {npc['name']} for {damage} damage", x, y, [player['id'], npc['id']], [])
        weapon_note = f" with the {weapon['name']}" if weapon else " with your fists"
        if new_health <= 0:
            return f"You strike {npc['name']}{weapon_note}. They collapse and stop moving."
        return f"You strike {npc['name']}{weapon_note}, leaving them badly hurt."

    obj = db.find_object_by_name(target_lower, x=x, y=y, z=z)
    if obj:
        props = json.loads(obj.get('properties') or '{}')
        if props.get('alive') or obj.get('object_type') == 'animal':
            db.update_object(obj['id'], state='dead', is_visible=0, x=None, y=None, z=None)
            db.log_event(int(db.get_state('game_ticks', 0)), 'attack', f"Player killed {obj['name']}", x, y, [player['id']], [obj['id']])
            return f"You attack the {obj['name']}. It does not survive."
        return f"You hit the {obj['name']}, but it barely responds."

    nearby = _nearby_visible_object(target_lower, player)
    if nearby:
        return _move_closer_message(nearby, player, 'attack')
    return f"There is no '{target}' here to attack."


def _sense_location_args(player):
    """Gather the common arguments needed for sensory LLM calls."""
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    building_name = None
    building_type = None
    if loc and loc.get('building_id'):
        bld = db.get_building(loc['building_id'])
        if bld:
            building_name = bld['name']
            building_type = bld.get('building_type')
    chars = [c for c in db.get_characters_at(x, y, z) if not c.get('is_player')]
    objs = db.get_objects_at(x, y, z)
    return dict(
        location_name=w.get_location_summary(x, y, z),
        terrain=loc['terrain'] if loc else 'ground',
        building_name=building_name,
        building_type=building_type,
        time_of_day=get_time_of_day(),
        weather=get_weather(),
        chars_present=[c['name'] for c in chars],
        objs_present=[o['name'] for o in objs],
        context=build_context(player),
    )


def action_listen(player, parsed):
    return llm.generate_sense(sense='listen', target=parsed.get('target'),
                               **_sense_location_args(player))


def action_smell(player, parsed):
    return llm.generate_sense(sense='smell', target=parsed.get('target'),
                               **_sense_location_args(player))


def action_feel(player, parsed):
    target = parsed.get('target')
    x, y, z = player['x'], player['y'], player['z']

    if target:
        # Check inventory first
        for oid in db.get_character_inventory(player['id']):
            obj = db.get_object(oid)
            if obj and target.lower() in obj['name'].lower():
                props = json.loads(obj.get('properties') or '{}')
                condition = _obj_condition_str(props)
                target = (f"{obj['name']} (held in hand): {obj['description']}"
                          + (f", currently {condition}" if condition else ""))
                break
        else:
            # Check objects at location
            obj = db.find_object_by_name(target, x=x, y=y, z=z)
            if obj:
                props = json.loads(obj.get('properties') or '{}')
                condition = _obj_condition_str(props)
                target = (f"{obj['name']}: {obj['description']}"
                          + (f", currently {condition}" if condition else ""))
            else:
                nearby = _nearby_visible_object(target, player)
                if nearby:
                    return _move_closer_message(nearby, player, 'touch')

    return llm.generate_sense(sense='feel', target=target,
                               **_sense_location_args(player))


def action_inventory(player, parsed):
    inv_ids = db.get_character_inventory(player['id'])
    if not inv_ids:
        return "You are carrying nothing."
    items = []
    for oid in inv_ids:
        obj = db.get_object(oid)
        if obj:
            props = json.loads(obj.get('properties') or '{}')
            condition = _obj_condition_str(props)
            # Strip location-contextual phrases from description (BUG-06)
            desc = _LOC_PHRASE_RE.sub('', obj['description'] or '').strip().rstrip('.,')
            if condition:
                desc = f"{desc} (currently {condition})"
            items.append(f"  - {obj['name']}: {desc}")
    return "You are carrying:\n" + '\n'.join(items)


def action_status(player, parsed):
    p = db.get_character(player['id'])
    money = p.get('money', 0)
    cond = _player_condition_summary(p)
    return (
        f"Name:      {p['name']}\n"
        f"Health:    {p['health']}/100\n"
        f"Hunger:    {p['hunger']}/100 (higher = more hungry)\n"
        f"Thirst:    {p.get('thirst', 30)}/100\n"
        f"Energy:    {p['energy']}/100\n"
        f"Warmth:    {p['warmth']}/100\n"
        f"Alcohol:   {p.get('alcohol', 0)}/100\n"
        f"Stress:    {p.get('stress', 0)}/100\n"
        f"Posture:   {p.get('posture') or 'standing'}\n"
        f"Money:     {money}p\n"
        f"Condition: {cond}\n"
        f"Location:  ({p['x']},{p['y']},z={p['z']})\n"
        f"Time:      {_format_time()}"
    )


def action_eat(player, parsed):
    target = parsed.get('target')
    inv_ids = db.get_character_inventory(player['id'])
    food_obj = None
    from_environment = False

    for oid in inv_ids:
        obj = db.get_object(oid)
        if obj:
            props = json.loads(obj.get('properties', '{}'))
            if props.get('edible') and (not target or target.lower() in obj['name'].lower()):
                food_obj = obj
                break

    if not food_obj:
        if target:
            # Check inventory for name match regardless of edible flag
            for oid in inv_ids:
                o = db.get_object(oid)
                if o and target.lower() in o['name'].lower():
                    return f"The {o['name']} is not something you can eat."
            # Check if the named thing even exists nearby before blaming inventory
            x, y, z = player['x'], player['y'], player['z']
            env_obj = db.find_object_by_name(target, x=x, y=y, z=z)
            if env_obj:
                props_env = json.loads(env_obj.get('properties', '{}'))
                if not props_env.get('edible'):
                    return f"You cannot eat the {env_obj['name']}."
                food_obj = env_obj
                from_environment = True
            if not env_obj:
                nearby = _nearby_visible_object(target, player)
                if nearby:
                    return _move_closer_message(nearby, player, 'eat')
                return f"There is no '{target}' here."
        if not food_obj:
            return "You have nothing edible in your inventory."

    props = json.loads(food_obj.get('properties', '{}'))
    nourishment = props.get('nourishment', 20)
    new_hunger = max(0, player['hunger'] - nourishment)

    p = db.get_character(player['id'])
    is_drink = bool(props.get('drinkable') or props.get('liquid') or props.get('alcohol'))
    thirst_reduction = props.get('hydration', 25 if is_drink else 5)
    new_thirst = max(0, p.get('thirst', 30) - thirst_reduction)

    alc_gain = props.get('alcohol_strength', 30) if props.get('alcohol') else 0
    new_alcohol = min(100, p.get('alcohol', 0) + alc_gain)

    # Eating/drinking while passed out restores a little energy (survival minimum)
    p2 = db.get_character(player['id'])
    energy_gain = 0
    if p2['energy'] == 0:
        energy_gain = 8 if props.get('drinkable') or props.get('liquid') else 5
    db.update_character(player['id'], hunger=new_hunger, thirst=new_thirst, alcohol=new_alcohol,
                        energy=min(100, p2['energy'] + energy_gain))
    if energy_gain:
        db.set_state('passed_out', '0')
    if not from_environment:
        if props.get('holdsliquid'):
            # Reusable container — empty it, keep it in inventory
            empty_props = {k: v for k, v in props.items()
                           if k not in ('liquid', 'drinkable', 'hydration')}
            db.update_object(food_obj['id'], properties=json.dumps(empty_props))
        else:
            db.remove_from_inventory(player['id'], food_obj['id'])
            db.update_object(food_obj['id'], state='consumed', is_visible=0)
    db.log_event(int(db.get_state('game_ticks', 0)), 'eat', f"Player ate {food_obj['name']}", player['x'], player['y'], [player['id']], [food_obj['id']])

    player['hunger'] = new_hunger
    taste = llm.generate_sense(
        sense='taste',
        target=f"{food_obj['name']}: {food_obj.get('description', '')}",
        **_sense_location_args(player),
    )
    verb = "drink" if props.get('drinkable') or props.get('alcohol') or props.get('liquid') else "eat"
    return f"You {verb} the {food_obj['name']}.\n{taste}"


def action_fill(player, parsed):
    target = _clean_target_name(parsed.get('target') or '')
    x, y, z = player['x'], player['y'], player['z']

    container = None
    for oid in db.get_character_inventory(player['id']):
        obj = db.get_object(oid)
        if not obj:
            continue
        props = json.loads(obj.get('properties') or '{}')
        if props.get('holdsliquid') and (not target or target in obj['name'].lower()):
            container = obj
            break

    if not container:
        return f"You have no '{target}' to fill." if target else "You have nothing to fill."

    c_props = json.loads(container.get('properties') or '{}')
    if c_props.get('liquid'):
        return f"The {container['name']} is already full."

    # Look for a water source on this tile or immediately adjacent
    loc = db.get_location(x, y, z)
    water_source = None
    if loc and loc.get('terrain') == 'stream':
        water_source = 'the stream'
    if not water_source:
        tiles = [(x, y, z)] + [(x + dx, y + dy, z) for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]]
        for cx, cy, cz in tiles:
            if not water_source:
                adj_loc = db.get_location(cx, cy, cz)
                if adj_loc and adj_loc.get('terrain') == 'stream':
                    water_source = 'the stream'
            for obj in db.get_objects_at(cx, cy, cz):
                props = json.loads(obj.get('properties') or '{}')
                if props.get('liquid') == 'water' or props.get('water'):
                    water_source = f'the {obj["name"]}'
                    break
            if water_source:
                break

    if not water_source:
        return "There is no water source here to fill from."

    c_props['liquid'] = 'water'
    c_props['drinkable'] = True
    c_props['hydration'] = 50
    db.update_object(container['id'], properties=json.dumps(c_props))
    db.log_event(int(db.get_state('game_ticks', 0)), 'fill',
                 f"Player filled {container['name']} from {water_source}",
                 x, y, [player['id']], [container['id']])
    return f"You fill the {container['name']} from {water_source}."


def action_drink(player, parsed):
    target = parsed.get('target')
    x, y, z = player['x'], player['y'], player['z']
    p = db.get_character(player['id'])

    # First try inventory for explicitly safe drinkables.
    # Only warn about unsafe liquid items if the player names them directly.
    inv_ids = db.get_character_inventory(player['id'])
    unsafe_match = None
    for oid in inv_ids:
        obj = db.get_object(oid)
        if not obj:
            continue
        props = json.loads(obj.get('properties', '{}'))
        name_matches = not target or _clean_target_name(target).lower() in obj['name'].lower()
        if not name_matches:
            continue
        is_safe_drink = props.get('drinkable') or props.get('alcohol')
        is_liquid_like = props.get('liquid') or props.get('water')
        if is_safe_drink or (props.get('edible') and is_liquid_like):
            return action_eat(player, {'target': obj['name']})
        # Remember the unsafe match but don't return yet — fall through to
        # environmental sources first (water trough, well, etc.)
        if is_liquid_like and not unsafe_match:
            unsafe_match = obj['name']

    # Drink directly from a stream tile
    _stream_words = ('stream', 'water', 'river', 'brook', 'creek')
    loc = db.get_location(x, y, z)
    if loc and loc.get('terrain') == 'stream':
        if not target or any(w in target.lower() for w in _stream_words):
            hydration = 45
            new_thirst = max(0, p.get('thirst', 30) - hydration)
            energy_gain = 8 if p['energy'] == 0 else 0
            db.update_character(player['id'], thirst=new_thirst,
                                energy=min(100, p['energy'] + energy_gain))
            if energy_gain:
                db.set_state('passed_out', '0')
            player['hunger'] = p.get('hunger', 50)
            taste = llm.generate_sense(
                sense='taste',
                target='cold stream water',
                **_sense_location_args(player),
            )
            return f"You kneel at the stream's edge and drink.\n{taste}"

    # Try containers/sources at exact location (water pump, trough, bucket)
    for obj in db.get_objects_at(x, y, z):
        props = json.loads(obj.get('properties', '{}'))
        liquid = props.get('liquid') or (props.get('water') and 'water')
        if not liquid:
            continue
        if target and target.lower() not in obj['name'].lower():
            continue
        hydration = 50
        new_thirst = max(0, p.get('thirst', 30) - hydration)
        energy_gain = 8 if p['energy'] == 0 else 0
        db.update_character(player['id'], thirst=new_thirst,
                            energy=min(100, p['energy'] + energy_gain))
        if energy_gain:
            db.set_state('passed_out', '0')
        player['hunger'] = p.get('hunger', 50)
        taste = llm.generate_sense(
            sense='taste',
            target=f"{liquid} from {obj['name']}",
            **_sense_location_args(player),
        )
        return f"You drink from the {obj['name']}.\n{taste}"

    # Also check immediately adjacent tiles (puddles, streams one step away)
    for adj_dy, adj_dx in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
        for obj in db.get_objects_at(x + adj_dx, y + adj_dy, z):
            props = json.loads(obj.get('properties', '{}'))
            liquid = props.get('liquid') or (props.get('water') and 'water')
            if not liquid:
                continue
            if target and target.lower() not in obj['name'].lower():
                continue
            hydration = 40
            new_thirst = max(0, p.get('thirst', 30) - hydration)
            energy_gain = 8 if p['energy'] == 0 else 0
            db.update_character(player['id'], thirst=new_thirst,
                                energy=min(100, p['energy'] + energy_gain))
            if energy_gain:
                db.set_state('passed_out', '0')
            player['hunger'] = p.get('hunger', 50)
            taste = llm.generate_sense(
                sense='taste',
                target=f"{liquid} from {obj['name']}",
                **_sense_location_args(player),
            )
            return f"You crouch down and drink from the {obj['name']}.\n{taste}"

    # Named target specified but nothing matching was drinkable anywhere
    if target and not unsafe_match:
        # Hint if water is available nearby
        for obj in db.get_objects_at(x, y, z):
            props = json.loads(obj.get('properties', '{}'))
            if props.get('liquid') or props.get('water'):
                return (f"There is no {target} here to drink. "
                        f"There is a {obj['name']} nearby if you need water.")
        nearby = _nearby_visible_object(target, player)
        if nearby:
            return _move_closer_message(nearby, player, 'drink from')
        return f"There is no {target} here to drink."
    if unsafe_match:
        return f"You have the {unsafe_match}, but it is not safe to drink."
    # Check for empty fillable containers to give a useful hint
    for oid in db.get_character_inventory(player['id']):
        obj = db.get_object(oid)
        if obj:
            props = json.loads(obj.get('properties') or '{}')
            if props.get('holdsliquid') and not props.get('liquid'):
                if not target or target in obj['name'].lower():
                    return f"Your {obj['name']} is empty. Fill it from a water source first."
    return "There is nothing here to drink."


def action_sleep(player, parsed):
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    if not loc:
        return "This is not a place to sleep."

    terrain = loc.get('terrain', '')
    if terrain not in ('building', 'upstairs', 'cellar'):
        # Check if there's a bed/bedroll nearby
        objs = db.get_objects_at(x, y, z)
        has_bed = any(o['object_type'] in ('bed', 'bedroll') for o in objs)
        if not has_bed:
            return "You cannot sleep here — you need somewhere sheltered."

    p = db.get_character(player['id'])
    ticks = int(db.get_state('game_ticks', 0))
    db.set_state('game_ticks', ticks + 32)  # 8 hours
    new_energy = min(100, p['energy'] + 60)
    # Hunger accumulates at half the normal rate while sleeping (16 vs 32 turns)
    new_hunger = min(100, p['hunger'] + 16)
    # Thirst accumulates much slower when at rest; small pre-sleep drink assumed
    new_thirst = min(100, max(0, p.get('thirst', 30) + 8 - 20))  # net -12
    new_stress  = max(0, p.get('stress', 0) - 30)
    db.update_character(player['id'], energy=new_energy, hunger=new_hunger,
                        thirst=new_thirst, stress=new_stress)
    db.set_state('passed_out', '0')
    db.log_event(int(db.get_state('game_ticks', 0)), 'sleep', 'Player slept and recovered', x, y, [player['id']], [])
    return "You find a spot to rest. Hours pass. You wake feeling somewhat restored."


def action_wait(player, parsed):
    ticks = int(db.get_state('game_ticks', 0))
    db.set_state('game_ticks', ticks + 4)  # 1 hour
    x, y, z = player['x'], player['y'], player['z']
    db.log_event(ticks, 'wait', 'Player waited for an hour', x, y, [player['id']], [])
    context = build_context(player)
    narrative = llm.generate_narrative(
        "The player waits, letting time pass. An hour drifts by.",
        context
    )
    return narrative


def action_think(player, parsed):
    thought = parsed.get('speech') or parsed.get('interpretation') or 'something unclear'
    return f"(You think: {thought})"


_FLOOR_QUESTIONS = (
    'on the floor', 'on the ground', 'lying here', 'been dropped',
    'dropped here', 'on the floor here', 'on the ground here',
    'what objects', 'what items', 'what can i pick up',
)

_NAV_STARTERS = (
    'where is', 'where are', 'where can i find', 'where do i find',
    'how do i get to', 'how do i find', "where's the", 'wheres the',
    'how far is', 'which way to', 'which way is', 'which direction is',
    'what direction is', 'in which direction',
)

_COMPASS = {
    (0,  -1): 'north', (0,  1): 'south',
    (1,   0): 'east',  (-1, 0): 'west',
    (1,  -1): 'northeast', (-1, -1): 'northwest',
    (1,   1): 'southeast', (-1,  1): 'southwest',
}


def _bearing(dx, dy):
    if dx == 0 and dy == 0:
        return 'here'
    sx = 0 if dx == 0 else (1 if dx > 0 else -1)
    sy = 0 if dy == 0 else (1 if dy > 0 else -1)
    return _COMPASS.get((sx, sy), 'nearby')


def action_query(player, parsed):
    question = (parsed.get('speech') or parsed.get('target')
                or parsed.get('interpretation') or '').strip()
    if not question:
        return "What do you want to know?"

    q = question.lower()

    if q in ('where are the exits', 'what are the exits', 'where can i go',
             'which ways can i go', 'what exits are there', 'exits'):
        exits = w._describe_exits(player['x'], player['y'], player['z'])
        return f"Exits: {exits}." if exits else "There are no obvious exits."

    # "what is on the floor/ground" — list floor objects directly (narrow match only)
    if any(fq in q for fq in _FLOOR_QUESTIONS):
        x, y, z = player['x'], player['y'], player['z']
        objs = db.get_objects_at(x, y, z)
        if not objs:
            return "There is nothing obvious here."
        names = ', '.join(o['name'] for o in objs)
        return f"Here: {names}."

    # Navigation / location questions — look up DB for real position
    if any(q.startswith(nav) or nav in q for nav in _NAV_STARTERS):
        import sqlite3
        from config import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # Extract candidate name from after the nav starter
        remainder = q
        for nav in sorted(_NAV_STARTERS, key=len, reverse=True):
            if nav in remainder:
                remainder = remainder[remainder.index(nav) + len(nav):].strip()
                break
        remainder = remainder.strip('?. ')
        # 1. Try building lookup
        rows = conn.execute(
            "SELECT * FROM buildings WHERE LOWER(name) LIKE ?",
            (f'%{remainder}%',)
        ).fetchall() if remainder else []
        conn.close()
        if rows:
            bld = dict(rows[0])
            tile = w.nearest_building_tile(player['x'], player['y'], bld['id'], player.get('z', 0))
            if tile:
                dx = tile[0] - player['x']
                dy = tile[1] - player['y']
            else:
                dx = bld['entrance_x'] - player['x']
                dy = bld['entrance_y'] - player['y']
            dist = abs(dx) + abs(dy)
            direction = _bearing(dx, dy)
            dist_str = (f"just {dist} steps" if dist <= 3
                        else f"about {dist} steps" if dist <= 15
                        else "some distance")
            return f"{bld['name']} is {dist_str} to the {direction} of here."
        # 2. Try character lookup
        if remainder:
            npc = db.find_character_by_name(remainder)
            if npc and not npc.get('is_player'):
                dx = npc['x'] - player['x']
                dy = npc['y'] - player['y']
                dist = abs(dx) + abs(dy)
                if dist == 0:
                    return f"{npc['name']} is right here with you."
                direction = _bearing(dx, dy)
                dist_str = (f"just {dist} steps" if dist <= 3
                            else f"about {dist} steps" if dist <= 15
                            else "some distance away")
                return f"{npc['name']} is {dist_str} to the {direction}."

    # "what buildings are nearby" — query the DB rather than hallucinate
    _NEARBY_BUILDING_Q = (
        'what buildings', 'which buildings', 'what shops', 'what places',
        'any buildings', 'any shops', 'what is around', "what's around",
        'what is nearby', "what's nearby", 'what can i find near',
        'what is close', "what's close",
    )
    if any(bq in q for bq in _NEARBY_BUILDING_Q):
        import sqlite3
        from config import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        blds = conn.execute("SELECT * FROM buildings").fetchall()
        conn.close()
        nearby = []
        for b in blds:
            b = dict(b)
            ex = b.get('entrance_x') or b.get('x1', player['x'])
            ey = b.get('entrance_y') or b.get('y1', player['y'])
            dx = ex - player['x']
            dy = ey - player['y']
            dist = abs(dx) + abs(dy)
            if dist <= 30:
                nearby.append((dist, b['name'], _bearing(dx, dy)))
        nearby.sort()
        if nearby:
            parts = [f"{name} ({direction}, {dist} steps)"
                     for dist, name, direction in nearby[:8]]
            return "Nearby buildings: " + '; '.join(parts) + "."
        return "There are no buildings visible from here."

    # "what are my skills" — read the player's actual learned/practiced skills
    if q in ('what are my skills', 'skills', 'what skills', 'what skills do i have',
             'do i have skills', 'my skills', 'what skills do i know'):
        p = db.get_character(player['id'])
        skills = json.loads(p.get('skills') or '{}')
        known = {name: lvl for name, lvl in skills.items() if lvl > 0}
        if not known:
            return "You have no particular skills yet — just willing hands and a strong back."
        ordered = sorted(known.items(), key=lambda kv: -kv[1])
        parts = [f"{name} (level {lvl})" for name, lvl in ordered]
        if len(parts) == 1:
            return f"You have picked up some {parts[0]}. Everything else you'd have to learn from scratch."
        return ("You have picked up some " + ", ".join(parts[:-1]) + f", and {parts[-1]}. "
                "Everything else you'd have to learn from scratch.")

    # In-world responses to out-of-world game-mechanic questions
    _META_Q = {
        frozenset(['what is my level', 'level', 'what level am i', 'my level',
                   'what level', 'what level are you']):
            "There are no levels here. Your health, hunger, and energy are all that count.",
        frozenset(['show me the map', 'map', 'show map', 'open map', 'display map',
                   'where is the map', 'is there a map']):
            "There is no map to consult. Ask a local for directions, or explore on foot.",
        frozenset(['how do i save the game', 'save game', 'save', 'save progress',
                   'how do i save', 'can i save']):
            "The world carries on whether you rest or not. Nothing here needs saving.",
        frozenset(['what is my quest', 'quest', 'quest log', 'quests', 'objectives',
                   'what should i do', 'what are my objectives']):
            "Millhaven sets no quests. Your needs and curiosity are your only guide.",
    }
    for phrase_set, response in _META_Q.items():
        if q in phrase_set or any(q == p for p in phrase_set):
            return response

    context = build_context(player)
    return llm.generate_query_response(question, context)


def action_flee(player, parsed):
    x, y, z = player['x'], player['y'], player['z']
    p = db.get_character(player['id'])
    stress = p.get('stress', 0)

    # Locate hostile NPCs within range
    hostile = []
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            for c in db.get_characters_at(x + dx, y + dy, z):
                if not c.get('is_player') and c.get('mood') in ('angry', 'hostile', 'aggressive'):
                    hostile.append((c, abs(dx) + abs(dy)))
    hostile.sort(key=lambda t: t[1])

    if not hostile and stress < 35:
        return "You are not in immediate danger — there is nowhere particular to run."

    # Pick direction away from the nearest threat
    if hostile:
        threat = hostile[0][0]
        tdx = x - threat['x']
        tdy = y - threat['y']
        sx = 0 if tdx == 0 else (1 if tdx > 0 else -1)
        sy = 0 if tdy == 0 else (1 if tdy > 0 else -1)
        candidates = [(sx, sy, 0), (sx, 0, 0), (0, sy, 0), (-sy, sx, 0), (sy, -sx, 0)]
    else:
        candidates = [(0, -1, 0), (0, 1, 0), (1, 0, 0), (-1, 0, 0)]
        random.shuffle(candidates)

    moved = False
    for fdx, fdy, fdz in candidates:
        ok, _ = w.can_move_to(x + fdx, y + fdy, z + fdz)
        if ok:
            db.update_character(player['id'], x=x + fdx, y=y + fdy, z=z + fdz)
            player['x'], player['y'], player['z'] = x + fdx, y + fdy, z + fdz
            db.set_state('player_exertion', min(100, int(db.get_state('player_exertion', 0)) + 55))
            db.update_character(player['id'], stress=min(100, stress + 20))
            moved = True
            break

    context = build_context(player)
    if moved:
        return llm.generate_narrative(
            "The player turns and flees at a run — heart hammering, lungs burning, "
            "putting distance between themselves and the threat as fast as their legs will carry them.",
            context,
        )
    return "You look desperately for an escape but are cornered — there is nowhere to run."


def action_enter(player, parsed):
    target = parsed.get('target') or ''
    x, y, z = player['x'], player['y'], player['z']

    # Look for a building underfoot or at an adjacent entrance.
    bld = db.get_building_at(x, y)
    if not bld and target:
        bld = areas.nearby_building_entrance(x, y, z, target)
        if not bld and areas.building_named(target):
            return f"You are not close enough to enter {target}."
    elif not bld:
        bld = areas.nearby_building_entrance(x, y, z)

    if not bld:
        return "There is nothing to enter here."

    ex, ey = bld['entrance_x'], bld['entrance_y']
    ok, reason = w.can_move_to(ex, ey, 0)
    if not ok:
        return f"You cannot enter {bld['name']}: {reason}"

    db.update_character(player['id'], x=ex, y=ey, z=0)
    player['x'], player['y'], player['z'] = ex, ey, 0
    knowledge.mark_seen('building', bld['id'], 'entered')
    return f"You enter {bld['name']}.\n{w.describe_surroundings(ex, ey, 0, radius=1)}"


def action_exit(player, parsed):
    x, y, z = player['x'], player['y'], player['z']
    loc = db.get_location(x, y, z)
    if not loc or not loc.get('building_id'):
        return "You are not inside a building."

    bld = db.get_building(loc['building_id'])
    direction = (parsed or {}).get('direction') or (parsed or {}).get('target') or ''
    direction = _clean_target_name(direction).lower()
    if direction:
        if direction in ('n', 's', 'e', 'w'):
            direction = _DIRECTION_ALIASES[direction]
        if direction not in ('north', 'south', 'east', 'west'):
            return f"'{direction}' is not an exit direction I understand."

    exit_tile = areas.building_exit_tile(bld, direction or None)
    if exit_tile:
        nx, ny, dir_name = exit_tile
        db.update_character(player['id'], x=nx, y=ny, z=0)
        player['x'], player['y'], player['z'] = nx, ny, 0
        if direction:
            return f"You leave {bld['name']} by the {dir_name} side."
        return f"You step outside {bld['name']}."

    if direction:
        return f"There is no clear {direction} exit from {bld['name']}."

    return f"You cannot find a clear way outside {bld['name']} from here."


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

ACTION_MAP = {
    'look':      action_look,
    'move':      action_move,
    'examine':   action_examine,
    'take':      action_take,
    'drop':      action_drop,
    'speak':     action_speak,
    'ask':       action_ask,
    'give':      action_give,
    'buy':       action_buy,
    'sell':      action_sell,
    'use':       action_use,
    'attack':    action_attack,
    'listen':    action_listen,
    'smell':     action_smell,
    'feel':      action_feel,
    'touch':     action_feel,
    'inventory': action_inventory,
    'status':    action_status,
    'eat':       action_eat,
    'drink':     action_drink,
    'fill':      action_fill,
    'sleep':     action_sleep,
    'wait':      action_wait,
    'think':     action_think,
    'query':     action_query,
    'flee':      action_flee,
    'enter':     action_enter,
    'exit':      action_exit,
    'map':       action_map,
    'help':      lambda p, x: _help_text(),
}


def process_input(raw_input, player):
    """Parse raw player input and execute the resulting action."""
    raw_input = _normalize_raw_input(raw_input)
    q = raw_input.lower().strip()
    q_clean = q.rstrip(' ?!.')

    # Dead players get nothing
    if db.get_state('game_over') == '1':
        return "You are dead."

    # Save then clear the edge-leave pending state — any action other than
    # immediately repeating the same edge-move direction resets the warning.
    player['_pending_leave_direction'] = db.get_state('pending_leave_direction') or ''
    db.set_state('pending_leave_direction', '')

    # Passed-out players can only eat, drink, look around, or check status
    if db.get_state('passed_out') == '1':
        _allowed_while_down = (
            'eat', 'drink', 'status', 'inventory', 'i', 'help', 'me', 'map',
            'look', 'examine', 'open i', 'survey',
            'ask', 'call', 'shout', 'yell', 'say', 'talk',
            'what', 'where', 'who', 'how', 'am i', 'health',
        )
        if not any(q.startswith(w) for w in _allowed_while_down):
            p = db.get_character(player['id'])
            if p['energy'] == 0:
                return ("You are sprawled on the ground, too weak to move. "
                        "Try drinking or eating something to recover.")

    # Work and teaching — intercepted early (before emergency-aid checks) so that
    # phrases like "ask will to teach me to make bread" aren't hijacked by the
    # food-emergency handler just because they mention "bread".
    _WORK_PHRASES = (
        'work', 'do work', 'do some work', 'get to work', 'start work',
        'find work', 'look for work', 'ask for work', 'take a job', 'do a job',
        'earn money', 'earn some money', 'make some money', 'make money',
    )
    _work_for_m = re.match(
        r'^(?:do |get )?(?:some |a bit of )?(?:work|working|labour|labor|chores?|odd jobs?)\s+'
        r'(?:for|at|with)\s+(?:the\s+)?(.+)$', q_clean)
    if q_clean in _WORK_PHRASES:
        return action_work(player, {'target': None})
    if _work_for_m:
        return action_work(player, {'target': _work_for_m.group(1).strip()})
    if ' for ' in q_clean:
        _role_part = q_clean.split(' for ', 1)[1].strip()
        _role_part = re.sub(r'^(the|a|an)\s+', '', _role_part)
        # Only treat as an offer of labour if the sentence OPENS with a trade
        # task verb (e.g. "bake bread for the baker", "shoe a horse for the
        # smith") — otherwise phrases like "ask the doctor for medicine" or
        # "buy bread for the journey" would be misread as work.
        for _trade in _TRADES.values():
            if any(q_clean.startswith(w) for w in _trade['task_words']):
                return action_work(player, {'target': _role_part})

    _TEACH_PATTERNS = (
        re.compile(r'^ask\s+(?P<name>.+?)\s+to\s+teach\s+me\s+(?:to\s+|how\s+to\s+)?(?P<skill>.+)$', re.IGNORECASE),
        re.compile(r"^(?P<name>(?:the\s+)?[a-z][\w']*(?:\s+[a-z][\w']*){0,2}),?\s+"
                   r"(?:can you |will you |please )?teach\s+me\s+(?:to\s+|how\s+to\s+)?(?P<skill>.+)$", re.IGNORECASE),
        re.compile(r'^learn\s+(?:to\s+|how\s+to\s+)?(?P<skill>.+?)\s+from\s+(?P<name>.+)$', re.IGNORECASE),
        re.compile(r'^teach\s+me\s+(?:to\s+|how\s+to\s+)?(?P<skill>.+)$', re.IGNORECASE),
    )
    for _pat in _TEACH_PATTERNS:
        _m = _pat.match(q_clean)
        if _m:
            _gd = _m.groupdict()
            return action_learn(player, _gd.get('skill'), _gd.get('name'))

    pre_emergency = _emergency_intent(q_clean, player)
    pre_emergency_phrases = (
        q_clean.startswith(('ask ', 'beg ', 'plead ', 'call for ', 'shout for ', 'yell for ', 'cry for '))
        and ' then ' not in q_clean
        and ';' not in q_clean
    )
    if pre_emergency and pre_emergency_phrases:
        target = ''
        target_match = re.match(r'^ask\s+(.+?)\s+for\s+', q_clean)
        if target_match:
            target = target_match.group(1)
        npc = _match_npc(target, _aid_people_near_player(player)) if target else None
        return _handle_emergency_aid(player, pre_emergency, npc, q_clean)

    # Detect compound inputs: "eat bread and then drink water", "go north; rest"
    _parts = _split_compound_input(raw_input)
    if len(_parts) > 1:
        first_result = process_input(_parts[0], player)
        rest = _parts[1:]
        if len(rest) == 1:
            hint = f"(You can now: {rest[0]})"
        else:
            hint = "(You can now: " + "; then ".join(rest) + ")"
        return first_result + "\n" + hint

    _KEEP_GOING = {
        'keep going', 'continue', 'carry on', 'go on', 'keep moving',
        'continue walking', 'continue moving', 'keep walking', 'same again',
        'same direction', 'go same way', 'onwards', 'onward',
    }
    if q_clean in _KEEP_GOING:
        last_dir = db.get_state('last_direction')
        if last_dir:
            return action_move(player, {'direction': last_dir})
        return "Which direction do you want to go?"

    if q_clean in ('stand', 'stand up', 'get up', 'rise'):
        return action_stand(player)

    if q_clean in ('sit', 'sit down', 'sit on the floor', 'sit on floor', 'sit on the ground'):
        return action_use(player, {'target': '', 'interpretation': 'sit'})

    # Foraging. "forage"/"gather"/"harvest"/"pluck"/"draw" are unambiguous —
    # always route to action_forage (which gives a clear "nothing here to
    # forage" if the tile has nothing). "pick"/"collect" overlap with generic
    # taking ("pick up the bucket", "collect firewood"), so they only trigger
    # foraging when something on THIS tile actually matches — otherwise they
    # fall through to the normal take/examine handling.
    _forage_always_m = re.match(
        r'^(?:forage(?:\s+(?:for|from|in|at))?|gather|harvest|pluck|draw)'
        r'\s*(?:(?:some|a|an|the)\s+)?(.*)$', q_clean)
    if _forage_always_m:
        return action_forage(player, {'target': _forage_always_m.group(1).strip() or None})

    _forage_maybe_m = re.match(
        r'^(?:collect|pick(?!\s+up))\s*(?:(?:some|a|an|the)\s+)?(.*)$', q_clean)
    if _forage_maybe_m:
        _f_target = _forage_maybe_m.group(1).strip()
        _f_candidates = _forage_candidates_at(player['x'], player['y'], player['z'])
        if _f_candidates and any(_matches_forage_target(_f_target, _o, _p) for _o, _p in _f_candidates):
            return action_forage(player, {'target': _f_target or None})

    _take_m = re.match(
        r'^(?:get|grab|take|pick\s+up|collect|retrieve)\s+(?!(?:me|to|out)\b)(?:the\s+|a\s+|an\s+)?(.+)$',
        q_clean,
    )
    if _take_m:
        _take_target = _take_m.group(1).strip()
        if _take_target:
            return action_take(player, {'target': _take_target})

    if q_clean in ('look for food', 'search for food', 'find food',
                   'look for something to eat', 'search for something to eat'):
        return _look_for_needs(player, want_food=True, want_water=False)

    if q_clean in ('look for water', 'search for water', 'find water',
                   'look for drink', 'search for drink', 'find drink',
                   'look for something to drink', 'search for something to drink'):
        return _look_for_needs(player, want_food=False, want_water=True)

    if q_clean in ('look for food and water', 'search for food and water',
                   'look for food or water', 'search for food or water',
                   'look for food and drink', 'search for food and drink'):
        return _look_for_needs(player, want_food=True, want_water=True)

    raw_emergency = _emergency_intent(q_clean, player)
    emergency_phrases = (
        q_clean.startswith(('call for ', 'shout for ', 'yell for ', 'cry for '))
        or q_clean.startswith(('ask for ', 'beg for ', 'plead for '))
        or re.match(r'^ask\s+\w+.*\s+for\s+', q_clean)
        or q_clean in ('help me', 'please help', 'someone help', 'call for help')
        or (q_clean == 'help' and db.get_state('passed_out') == '1')
    )
    if raw_emergency and emergency_phrases:
        target = ''
        target_match = re.match(r'^ask\s+(.+?)\s+for\s+', q_clean)
        if target_match:
            target = target_match.group(1)
        npc = None
        if target:
            npc = _match_npc(target, _aid_people_near_player(player))
        return _handle_emergency_aid(player, raw_emergency, npc, q_clean)

    precise_step_match = re.match(
        r'^(?:step|move one step)\s+(north|south|east|west|up|down|n|s|e|w|u|d)$',
        q_clean,
    )
    if precise_step_match:
        direction = _DIRECTION_ALIASES[precise_step_match.group(1)]
        return action_move(player, {'direction': direction, 'precise': True})

    intent_dir_match = re.match(
        r'^(?:(?:go|walk|move|head)\s+)?(north|south|east|west|up|down|n|s|e|w|u|d)$',
        q_clean,
    )
    if intent_dir_match:
        direction = _DIRECTION_ALIASES[intent_dir_match.group(1)]
        return action_go_direction(player, direction)

    # "feed X to Y" — intercept before LLM so it doesn't silently become a pet/stroke action
    _feed_m = re.match(r'^feed\s+(?:the\s+|some\s+|a\s+)?(.+?)\s+to\s+(?:the\s+)?(.+)$', q)
    if _feed_m:
        item_hint = _feed_m.group(1).strip()
        recipient_hint = _feed_m.group(2).strip()
        _fx, _fy, _fz = player['x'], player['y'], player['z']
        # Check if recipient is an animal object on the current tile
        for _fobj in db.get_objects_at(_fx, _fy, _fz):
            _fprops = json.loads(_fobj.get('properties') or '{}')
            if (recipient_hint in _fobj['name'].lower()
                    and (_fobj.get('object_type') == 'animal' or _fprops.get('alive'))):
                inv_ids = db.get_character_inventory(player['id'])
                item_obj = next(
                    (db.get_object(oid) for oid in inv_ids
                     if item_hint in (db.get_object(oid) or {}).get('name', '').lower()),
                    None
                )
                if item_obj:
                    return (f"You offer the {item_obj['name']} to the {_fobj['name']}. "
                            f"The {_fobj['name']} sniffs it but shows no interest.")
                return f"You don't have any {item_hint} to offer."
        # Recipient is an NPC — route through give
        return action_give(player, {'target': f'{item_hint} to {recipient_hint}'})

    # Extract explicit drink target before LLM loses it (e.g. "drink the ale" → target='ale')
    _drink_m = re.match(r'^drink\s+(?:the|some|a)\s+(.+)$', q)
    if _drink_m:
        _drink_target = _drink_m.group(1).strip()
        return action_drink(player, {'target': _drink_target})

    if q_clean in ('where are the exits', 'what are the exits', 'where can i go',
                   'which ways can i go', 'what exits are there', 'exits'):
        return action_query(player, {'speech': raw_input})

    map_match = re.match(r'^(?:show(?: me)? )?(?:local )?map(?:\s+\w+(?:\s+\w+)?)?$', q)
    if map_match:
        return action_map(player)

    survey_match = re.match(r'^survey(?: the)?(?: surroundings)?(?:\s+\w+(?:\s+\w+)?)?$', q)
    if survey_match:
        return action_map(player)

    exit_dir_match = re.match(r'^exit\s+(north|south|east|west|n|s|e|w)$', q_clean)
    if exit_dir_match:
        direction = {
            'n': 'north', 's': 'south', 'e': 'east', 'w': 'west',
        }.get(exit_dir_match.group(1), exit_dir_match.group(1))
        return action_exit(player, {'direction': direction})

    if q_clean in ('go left', 'go right', 'go forward', 'go back', 'left', 'right', 'forward', 'back'):
        return ("Millhaven does not track which way you are facing yet. "
                "Use north, south, east, west, up, or down.")

    # "who is here / who is around" — deterministic NPC list, no LLM
    _WHO_HERE = ('who is here', 'who is around', "who's here", 'who else is here',
                 'who is nearby', 'who can i see', 'who is present', 'is anyone here',
                 'anyone here', 'anyone around')
    if q_clean in _WHO_HERE:
        x, y, z = player['x'], player['y'], player['z']
        loc = db.get_location(x, y, z)
        npcs_here = [c for c in db.get_characters_at(x, y, z) if not c.get('is_player')]
        if areas.is_indoors(loc):
            area_npcs = areas.characters_in_area(x, y, z, max_distance=8)
            nearby = [c for c in area_npcs if c.get('_distance', 0) > 0]
        else:
            nearby = []
            for adj_dy in range(-2, 3):
                for adj_dx in range(-2, 3):
                    if adj_dx == 0 and adj_dy == 0:
                        continue
                    for c in db.get_characters_at(x + adj_dx, y + adj_dy, z):
                        if not c.get('is_player') and not any(e['id'] == c['id'] for e in npcs_here):
                            nearby.append(c)
        if not npcs_here and not nearby:
            return "There is nobody here."
        parts = []
        if npcs_here:
            parts.append("Here: " + ", ".join(
                knowledge.npc_display_name(c, 0, same_area=True)
                for c in npcs_here
            ) + ".")
        if nearby:
            label = "In the room" if areas.is_indoors(loc) else "Nearby"
            parts.append(label + ": " + ", ".join(
                knowledge.npc_display_name(c, c.get('_distance', 2), same_area=areas.is_indoors(loc))
                for c in nearby[:4]
            ) + ".")
        return " ".join(parts)

    go_to_match = _TARGET_PREFIX_RE.match(raw_input)
    if go_to_match:
        return action_go_to(player, raw_input[go_to_match.end():])

    # "talk to anyone/everyone/someone" — list present NPCs before LLM parsing
    _talk_generic = ('talk to anyone', 'talk to everyone', 'talk to someone',
                     'speak to anyone', 'speak to everyone', 'speak to someone',
                     'greet everyone', 'greet anyone', 'say hello to everyone',
                     'say hello to anyone', 'say hi to everyone')
    if any(q.startswith(p) for p in _talk_generic):
        return action_speak(player, {'speech': '', 'target': 'anyone'})

    context = build_context(player)
    parsed = llm.parse_command(raw_input, context)
    action = parsed.get('action', 'think')

    # Promote question-phrased inputs that the LLM misclassified as bare 'look'
    if action == 'look' and not parsed.get('target'):
        q = raw_input.lower().strip()
        _question_starts = ('what ', 'where ', 'who ', 'when ', 'why ', 'how ',
                            'is there ', 'can i ', 'are there ')
        if q.endswith('?') or any(q.startswith(w) for w in _question_starts):
            action = 'query'
            parsed['action'] = 'query'
            parsed['speech'] = raw_input

    # Catch flee/run/fly inputs misclassified as movement or attack
    if action in ('move', 'attack'):
        q = raw_input.lower().strip()
        if q in ('run', 'flee', 'escape', 'run away', 'run!', 'flee!') or q.startswith('run away'):
            action = 'flee'
            parsed['action'] = 'flee'
        elif q == 'fly':
            action = 'think'
            parsed['action'] = 'think'
            parsed['speech'] = "You cannot fly."

    handler = ACTION_MAP.get(action)
    return handler(player, parsed) if handler else action_think(player, parsed)


def tick_needs(player_id):
    """Advance player biological needs each turn."""
    p = db.get_character(player_id)
    ticks = int(db.get_state('game_ticks', 0))
    hunger  = min(100, p['hunger'] + 1)
    thirst  = min(100, p.get('thirst', 30) + 1)
    alcohol = max(0,   p.get('alcohol', 0) - 5)
    stress  = max(0,   p.get('stress', 0) - 3)

    # Exploration should not feel like a death march. Baseline fatigue accrues
    # slowly; deprivation still becomes dangerous if ignored.
    energy_drain = 1 if ticks % 2 == 0 else 0
    if thirst >= 90:
        energy_drain += 3   # desperately thirsty: body shutting down
    elif thirst >= 75:
        energy_drain += 1
    if hunger >= 90:
        energy_drain += 2
    elif hunger >= 75:
        energy_drain += 1
    energy = max(0, p['energy'] - energy_drain)

    # Being warm, well-fed, or indoors accelerates calm
    x, y, z = p['x'], p['y'], p['z']
    loc = db.get_location(x, y, z)
    if loc and loc.get('terrain') in ('building', 'upstairs'):
        stress = max(0, stress - 3)
    if hunger <= 20 and thirst <= 20:
        stress = max(0, stress - 2)

    # Check for music/fire objects nearby that soothe
    for obj in db.get_objects_at(x, y, z):
        props = json.loads(obj.get('properties') or '{}')
        if props.get('music') or props.get('fire'):
            stress = max(0, stress - 5)
            break

    db.update_character(player_id, hunger=hunger, thirst=thirst,
                        energy=energy, alcohol=alcohol, stress=stress)

    # Exertion decays quickly
    exertion = int(db.get_state('player_exertion', 0))
    if exertion > 0:
        db.set_state('player_exertion', max(0, exertion - 20))

    # Rain soaks carried items when player is outdoors
    if get_weather() == 'drizzling' and loc and loc.get('terrain') in _OUTDOOR_TERRAIN:
        for oid in db.get_character_inventory(player_id):
            obj = db.get_object(oid)
            if obj:
                iprops = json.loads(obj.get('properties') or '{}')
                if iprops.get('condition') not in ('wet', 'soaked'):
                    iprops['condition'] = 'damp'
                    db.update_object(oid, properties=json.dumps(iprops))

    # Health damage from sustained extremes (secondary to energy collapse)
    health = p['health']
    if thirst >= 95 and energy == 0:
        health = max(0, health - 3)
    elif thirst >= 90:
        health = max(0, health - 1)
    if hunger >= 95 and energy == 0:
        health = max(0, health - 2)
    if health != p['health']:
        db.update_character(player_id, health=health)

    # Pass-out detection: energy at zero
    passed_out = energy == 0
    db.set_state('passed_out', '1' if passed_out else '0')

    warnings = []
    if health <= 0:
        db.set_state('game_over', '1')
        warnings.append("__GAME_OVER__")
        return warnings
    if health <= 20:
        warnings.append("You are dangerously weak.")
    if passed_out:
        warnings.append("You collapse — too weak to stand. Everything goes dark.")
    elif energy <= 10:
        warnings.append("You are on the verge of collapse.")
    elif energy <= 20:
        warnings.append("You are exhausted.")
    if hunger >= 80:
        warnings.append("You are very hungry.")
    if thirst >= 90:
        warnings.append("You are desperately thirsty. Your vision swims.")
    elif thirst >= 60:
        warnings.append("You are thirsty.")
    if stress >= 70:
        warnings.append("Your heart is pounding.")

    # Intrusive thoughts: fire when confusion is high.
    # Also fire when passed out at extreme confusion (hallucination/delirium).
    _p_now = {'hunger': hunger, 'thirst': thirst, 'energy': energy,
              'alcohol': alcohol, 'stress': stress}
    _confusion = get_confusion(_p_now)
    _thought_chance = (0.35 if _confusion >= 70 else
                       0.20 if _confusion >= 50 else
                       0.10 if _confusion >= 35 else 0.0)
    # Stress alone (even at healthy energy) can push intrusive thoughts
    if stress >= 70 and _thought_chance < 0.10:
        _thought_chance = 0.10
    # Allow delirium thoughts even when collapsed, but at reduced rate
    if passed_out and _confusion < 70:
        _thought_chance = 0.0
    if _thought_chance and random.random() < _thought_chance:
        _condition_str = _player_condition_summary(_p_now)
        thought = llm.generate_intrusive_thought(_condition_str, _confusion)
        if thought:
            warnings.append(f"__THOUGHT__:{thought}")

    return warnings


def tick_npcs():
    """Give each NPC a simple autonomous action. Returns list of ambient speech events."""
    npcs = db.get_all_npcs()
    time_of_day = get_time_of_day()
    player = db.get_player()
    ticks = int(db.get_state('game_ticks', 0))
    speech_events = []
    speech_candidates = []

    for npc in npcs:
        _simple_npc_move(npc, time_of_day)
        hunger = min(100, npc['hunger'] + 1)
        energy = max(0, npc['energy'] - 1)
        db.update_character(npc['id'], hunger=hunger, energy=energy)

        # Speech candidacy: extroversion-weighted random check with cooldown
        extroversion = _npc_extroversion(npc)
        last_speech = int(db.get_state(f'npc_last_speech:{npc["id"]}', 0))
        min_gap = max(8, 30 - extroversion // 5)   # introverts stay quieter longer
        if (ticks - last_speech) >= min_gap:
            if random.random() < extroversion / 500.0:
                speech_candidates.append(npc)

    # Limit to 2 speakers per tick to avoid slowdown
    random.shuffle(speech_candidates)
    for npc in speech_candidates[:2]:
        npc = db.get_character(npc['id'])  # fresh data post-move
        nx, ny, nz = npc['x'], npc['y'], npc['z']

        # Gather nearby characters
        nearby_all = []
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                if dx == 0 and dy == 0:
                    continue
                for c in db.get_characters_at(nx + dx, ny + dy, nz):
                    if c['id'] != npc['id']:
                        nearby_all.append(c)

        nearby_npcs   = [c for c in nearby_all if not c.get('is_player')]
        player_nearby = any(c.get('is_player') for c in nearby_all)
        extroversion  = _npc_extroversion(npc)

        # Only speak when someone is within earshot, or NPC is very extroverted
        if not nearby_all and extroversion < 65:
            continue

        memories = _npc_memory_get(npc['id'], limit=5)
        context  = build_context(player)

        event = llm.generate_npc_spontaneous_speech(
            npc_name=npc['name'],
            npc_personality=npc.get('personality', ''),
            npc_mood=npc.get('mood', 'neutral'),
            npc_activity=npc.get('current_activity', ''),
            memories=memories,
            nearby_names=[c['name'] for c in nearby_npcs],
            player_nearby=player_nearby,
            context=context,
        )

        if event:
            speech = event['speech']
            directed_at = event['directed_at']
            db.set_state(f'npc_last_speech:{npc["id"]}', ticks)

            # Store in memory of all nearby NPCs who can overhear
            for c in nearby_npcs:
                _npc_memory_add(c['id'], npc['name'], speech, 'heard')

            # Surface to player only if within hearing distance
            player_dist = abs(player['x'] - nx) + abs(player['y'] - ny)
            if player_dist <= 4 and player['z'] == nz:
                speech_events.append({
                    'speaker':      npc['name'],
                    'directed_at':  directed_at,
                    'speech':       speech,
                })

    return speech_events


def _simple_npc_move(npc, time_of_day=None):
    """Move NPCs toward home/work with light wandering when unassigned."""
    import random
    if time_of_day is None:
        time_of_day = get_time_of_day()

    # Freeze in place for several turns after the player last spoke to this NPC
    ticks = int(db.get_state('game_ticks', 0))
    memories = json.loads(npc.get('speech_memory') or '[]')
    if any(m.get('type') == 'player_said' and (ticks - m.get('tick', 0)) <= 8
           for m in memories[-5:]):
        return

    tx = ty = None
    activity = 'wandering'

    if npc['energy'] <= 20 and npc.get('home_x') is not None:
        tx, ty = npc['home_x'], npc['home_y']
        activity = 'resting'
    elif time_of_day in ('morning', 'midday', 'afternoon') and npc.get('work_x') is not None:
        tx, ty = npc['work_x'], npc['work_y']
        activity = 'working'
    elif time_of_day in ('evening', 'night', 'late night') and npc.get('home_x') is not None:
        tx, ty = npc['home_x'], npc['home_y']
        activity = 'going home'
    elif npc['hunger'] >= 75:
        tx, ty = 50, 50
        activity = 'looking for food'

    if tx is not None:
        dx = 0 if npc['x'] == tx else (1 if npc['x'] < tx else -1)
        dy = 0 if npc['y'] == ty else (1 if npc['y'] < ty else -1)
        nx, ny = npc['x'] + dx, npc['y'] + dy
    else:
        dx, dy = random.choice([(0,1),(0,-1),(1,0),(-1,0),(0,0)])
        nx, ny = npc['x'] + dx, npc['y'] + dy

    ok, _ = w.can_move_to(nx, ny, npc['z'])
    if ok:
        db.update_character(npc['id'], x=nx, y=ny, current_activity=activity)


def _help_text():
    return (
        "=== Millhaven Help ===\n"
        "Type naturally — the game understands plain English.\n\n"
        "Movement:   north/south/east/west/up/down (or n/s/e/w/u/d)\n"
        "Look:       look, look around\n"
        "Map:        map, show map, survey surroundings\n"
        "Examine:    examine <thing>, look at <thing>, look at sky/ground/wall/etc.\n"
        "Listen:     listen, listen to <thing>\n"
        "Smell:      smell, smell <thing>\n"
        "Feel:       feel <thing>, touch <thing>\n"
        "Take:       take <object>, pick up <object>\n"
        "Drop:       drop <object>\n"
        "Speak:      say <words>, or just type what you say in quotes\n"
        "Ask:        ask <person> about <topic>\n"
        "Give:       give <object> to <person>\n"
        "Buy:        buy <item>\n"
        "Sell:       sell <item>\n"
        "Use:        use <item>, use <item> on <thing>\n"
        "Attack:     attack <person or creature>\n"
        "Flee:       run away, flee, escape\n"
        "Eat:        eat <food>\n"
        "Drink:      drink, drink <thing>\n"
        "Sleep:      sleep\n"
        "Wait:       wait\n"
        "Inventory:  i, inventory\n"
        "Status:     status, me\n"
        "Quit:       quit, exit game\n"
    )
