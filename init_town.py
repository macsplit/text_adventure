"""
Town initialization for Millhaven.
Run this once to generate the world: python init_town.py

Town layout (100x100 grid, y increases southward):
  - Wilderness/fields:  edges (x<12 or x>88, y<12 or y>88)
  - Northern farm:      x=22-40, y=13-30
  - Southern farm:      x=60-78, y=70-87
  - Town proper:        roughly x=20-80, y=25-75
  - Main E-W road:      y=50, x=18-82
  - Main N-S road:      x=50, y=18-82
  - Town square:        x=46-54, y=46-54
  - Various buildings around the square and along roads
"""

import json
import os
import random
import database as db
from config import DB_PATH, GRID_WIDTH, GRID_HEIGHT

# -----------------------------------------------------------------------
# Terrain map helpers
# -----------------------------------------------------------------------

def terrain_at(x, y):
    """Return default terrain for a grid cell."""
    # Edges: wilderness
    if x < 12 or x > 88 or y < 12 or y > 88:
        return 'wilderness'

    # Northern farm zone
    if 22 <= x <= 40 and 13 <= y <= 30:
        if 27 <= x <= 34 and 24 <= y <= 30:
            return 'farmyard'
        return 'farmland'

    # Southern farm zone
    if 60 <= x <= 78 and 70 <= y <= 87:
        if 60 <= x <= 70 and 70 <= y <= 76:
            return 'farmyard'
        return 'farmland'

    # Stream: diagonal band from (18,18) to (35,35)
    if abs((x - 18) - (y - 18)) <= 1 and 18 <= x <= 35:
        return 'stream'

    # Main E-W road
    if y == 50 and 18 <= x <= 82:
        return 'road'
    # Main N-S road
    if x == 50 and 18 <= y <= 82:
        return 'road'
    # Secondary roads
    if y == 40 and 25 <= x <= 50:
        return 'road'
    if y == 60 and 50 <= x <= 75:
        return 'road'
    if x == 38 and 35 <= y <= 50:
        return 'road'
    if x == 62 and 50 <= y <= 65:
        return 'road'
    # Lane to north farm
    if x == 30 and 18 <= y <= 40:
        return 'track'
    # Lane to south farm
    if x == 68 and 60 <= y <= 82:
        return 'track'

    # Town square (open market area)
    if 46 <= x <= 54 and 46 <= y <= 54:
        return 'market'

    # Small park
    if 55 <= x <= 60 and 38 <= y <= 43:
        return 'park'

    # Default: open field within town boundary
    return 'field'


def is_building_interior(x, y, buildings):
    for b in buildings:
        if b['x1'] <= x <= b['x2'] and b['y1'] <= y <= b['y2']:
            return b
    return None


# -----------------------------------------------------------------------
# Building definitions
# -----------------------------------------------------------------------

BUILDINGS = [
    # (name, type, x1, y1, x2, y2, entrance_x, entrance_y, floors, basement, description)
    ("The Millhaven Arms",  "inn",        44, 50, 48, 54, 46, 50,
     2, 0, "The village's only inn and public house, its low beams dark with age."),
    ("Harker's General Store", "shop",    51, 51, 55, 54, 51, 51,
     1, 0, "A cluttered shop selling provisions, hardware, and oddments."),
    ("The Old Bakery",      "bakery",     42, 42, 45, 46, 43, 46,
     1, 0, "A warm bakery smelling of fresh bread and woodsmoke."),
    ("Finch & Son Smithy",  "smithy",     37, 56, 41, 60, 39, 56,
     1, 0, "The blacksmith's forge, loud with hammering and the smell of hot iron."),
    ("St. Cuthbert's Church","church",    54, 35, 60, 42, 56, 42,
     1, 0, "A modest stone church with a squat tower, surrounded by a small graveyard."),
    ("Town Hall",           "civic",      51, 45, 55, 48, 53, 48,
     2, 0, "The village's administrative centre, often locked and always cold."),
    ("Dr. Cross's Surgery", "medical",    58, 51, 61, 54, 58, 51,
     1, 0, "A neat, whitewashed house serving as the village doctor's surgery."),
    ("Millhaven School",    "school",     31, 45, 36, 50, 34, 50,
     1, 0, "A single-roomed schoolhouse, unused on evenings and weekends."),
    ("Clara's Needlework",  "shop",       51, 57, 54, 60, 51, 57,
     1, 0, "A tiny shop offering dressmaking, alterations and haberdashery."),
    ("The Post Office",     "post",       45, 57, 48, 60, 46, 57,
     1, 0, "A small post office doubling as a telegraph station."),
    ("Constable's Station", "police",     57, 57, 60, 60, 58, 57,
     1, 0, "The village police station: one cell, one desk, usually empty."),
    # Residential
    ("Webb Cottage",        "home",       20, 46, 24, 50, 22, 50,
     1, 0, "A tidy but weathered cottage at the edge of town."),
    ("Harker House",        "home",       42, 63, 46, 66, 44, 63,
     2, 0, "The innkeeper's family home above the inn yard."),
    ("Carson House",        "home",       64, 54, 68, 57, 64, 55,
     1, 0, "A carpenter's house, pleasantly cluttered with tools and offcuts."),
    ("Holt's House",        "home",       42, 35, 45, 38, 43, 38,
     1, 0, "The baker's house, next door to the bakery."),
    ("Goodwin Rectory",     "home",       70, 41, 74, 46, 70, 44,
     2, 0, "The rector's rambling rectory, smelling of damp and old books."),
    ("Cross House",         "home",       63, 39, 67, 43, 63, 42,
     1, 0, "The doctor's private residence, neatly maintained."),
    ("Stone Cottage",       "home",       28, 55, 32, 58, 30, 55,
     1, 0, "Harriet Stone the schoolteacher's rented cottage."),
    ("Norris Cottage",      "home",       70, 62, 74, 65, 70, 63,
     1, 0, "The constable's terraced cottage, defiantly tidy."),
    ("Marsh's House",       "home",       12, 48, 16, 52, 14, 52,
     1, 0, "Old Henry Marsh's sea-captain's house, full of maritime curios."),
    ("Vane's Rooms",        "home",       59, 63, 62, 66, 59, 63,
     1, 0, "Clara Vane's rooms above her shop."),
    # North farm
    ("Finch Farm",          "farm",       28, 27, 32, 30, 30, 29,
     1, 1, "A working farm on the northern edge of town, muddy and productive."),
    # South farm
    ("Meadow Farm",         "farm",       66, 70, 70, 72, 68, 70,
     1, 0, "A smaller farm in the south fields, known for its apple orchard."),
]


# -----------------------------------------------------------------------
# Character definitions (24 characters)
# -----------------------------------------------------------------------

CHARACTERS = [
    {
        "name": "Thomas Webb", "age": 65, "gender": "male",
        "occupation": "retired farmer",
        "personality": "grumpy, stubborn, but secretly kind-hearted; distrustful of strangers; knows local history",
        "backstory": "Farmed the north fields for forty years before handing them to Arthur Finch. Now tends a small vegetable garden.",
        "home": "Webb Cottage", "work": None,
        "money": 12, "mood": "gruff",
        "x": 22, "y": 49,
    },
    {
        "name": "Agnes Webb", "age": 62, "gender": "female",
        "occupation": "housewife and village gossip",
        "personality": "warm, chatty, nosy; loves other people's business; remembers every piece of local gossip from the last forty years",
        "backstory": "Thomas's wife. Has never left Millhaven except once, for a cousin's wedding in the next county.",
        "home": "Webb Cottage", "work": None,
        "money": 5, "mood": "cheerful",
        "x": 23, "y": 49,
    },
    {
        "name": "James Harker", "age": 40, "gender": "male",
        "occupation": "innkeeper",
        "personality": "loud, jovial, sharp businessman beneath the bonhomie; quietly in debt",
        "backstory": "Took over the inn from his father. Married Ruth ten years ago. Keeps a ledger of who owes him money.",
        "home": "Harker House", "work": "The Millhaven Arms",
        "money": 85, "mood": "sociable",
        "x": 46, "y": 51,
    },
    {
        "name": "Ruth Harker", "age": 38, "gender": "female",
        "occupation": "innkeeper's wife",
        "personality": "efficient, sharp-tongued, quietly exhausted; deeply practical",
        "backstory": "Runs the kitchen and accounts of the inn while James works the bar. Has a temper she keeps on a short leash.",
        "home": "Harker House", "work": "The Millhaven Arms",
        "money": 30, "mood": "tired",
        "x": 46, "y": 52,
    },
    {
        "name": "Old Peter", "age": 78, "gender": "male",
        "occupation": "vagrant, former farmhand",
        "personality": "mysterious, seemingly confused but occasionally startlingly lucid; harmless; surprisingly wise",
        "backstory": "Has wandered Millhaven for decades. Nobody knows his full name or where he came from originally.",
        "home": None, "work": None,
        "money": 0, "mood": "vague",
        "x": 50, "y": 50,
    },
    {
        "name": "Dr. Eleanor Cross", "age": 45, "gender": "female",
        "occupation": "village doctor",
        "personality": "precise, calm, professionally detached but genuinely caring; reads a great deal",
        "backstory": "Came to Millhaven from the city fifteen years ago, meant to stay one year. Never left.",
        "home": "Cross House", "work": "Dr. Cross's Surgery",
        "money": 120, "mood": "focused",
        "x": 59, "y": 52,
    },
    {
        "name": "Rev. Samuel Goodwin", "age": 55, "gender": "male",
        "occupation": "village rector",
        "personality": "pompous, self-important, but genuinely believes in his faith; disapproves of drinking and idleness",
        "backstory": "Has served St. Cuthbert's for twenty years. Writes sermons nobody listens to.",
        "home": "Goodwin Rectory", "work": "St. Cuthbert's Church",
        "money": 40, "mood": "self-righteous",
        "x": 57, "y": 39,
    },
    {
        "name": "Mary Goodwin", "age": 50, "gender": "female",
        "occupation": "rector's wife",
        "personality": "gentle, patient, quietly resigned; keeps a beautiful garden; is kinder than her husband",
        "backstory": "Has been married to Samuel for twenty-five years. Has learned to manage his moods.",
        "home": "Goodwin Rectory", "work": None,
        "money": 15, "mood": "placid",
        "x": 71, "y": 44,
    },
    {
        "name": "Frank Mills", "age": 35, "gender": "male",
        "occupation": "blacksmith",
        "personality": "taciturn, physically powerful, slow to trust but fiercely loyal once he does; not much given to words",
        "backstory": "Inherited the smithy from an uncle. Works from dawn to dusk. Drinks alone at the inn on Fridays.",
        "home": None, "work": "Finch & Son Smithy",
        "money": 55, "mood": "neutral",
        "x": 39, "y": 58,
    },
    {
        "name": "Lily Carson", "age": 28, "gender": "female",
        "occupation": "shopkeeper",
        "personality": "friendly, optimistic, good at reading people; sharp memory for prices and faces",
        "backstory": "Runs the general store with her husband George. Grew up in the next village.",
        "home": "Carson House", "work": "Harker's General Store",
        "money": 70, "mood": "friendly",
        "x": 52, "y": 52,
    },
    {
        "name": "George Carson", "age": 30, "gender": "male",
        "occupation": "carpenter",
        "personality": "steady, methodical, good-natured; quietly proud of his craft; talks about wood",
        "backstory": "Does repairs around the village and occasional furniture work. Madly in love with his wife.",
        "home": "Carson House", "work": None,
        "money": 45, "mood": "content",
        "x": 66, "y": 55,
    },
    {
        "name": "Betty Holt", "age": 60, "gender": "female",
        "occupation": "baker",
        "personality": "maternal, warm, opinionated about bread; up before dawn every day; exhausting company",
        "backstory": "Has run the bakery for thirty years. Her husband died a decade ago. Will feeds himself but not in the way she'd like.",
        "home": "Holt's House", "work": "The Old Bakery",
        "money": 30, "mood": "industrious",
        "x": 43, "y": 44,
    },
    {
        "name": "Will Holt", "age": 22, "gender": "male",
        "occupation": "baker's apprentice (son)",
        "personality": "lazy, easily distracted, good-humoured; not really interested in baking; interested in everything else",
        "backstory": "Betty's son. Apprenticed to the bakery by default. Dreams of going to the city.",
        "home": "Holt's House", "work": "The Old Bakery",
        "money": 3, "mood": "bored",
        "x": 43, "y": 45,
    },
    {
        "name": "Alice Meadows", "age": 16, "gender": "female",
        "occupation": "schoolgirl",
        "personality": "curious, bookish, perceptive beyond her years; notices things adults ignore",
        "backstory": "Top of her class. Wants to become a teacher or possibly a journalist.",
        "home": None, "work": "Millhaven School",
        "money": 1, "mood": "curious",
        "x": 50, "y": 48,
    },
    {
        "name": "Tom Meadows", "age": 14, "gender": "male",
        "occupation": "schoolboy",
        "personality": "mischievous, restless, loyal to his sister; easily bored; good at climbing things",
        "backstory": "Alice's younger brother. Often in trouble. Has a hideout somewhere in the north fields.",
        "home": None, "work": "Millhaven School",
        "money": 2, "mood": "mischievous",
        "x": 51, "y": 49,
    },
    {
        "name": "Harriet Stone", "age": 35, "gender": "female",
        "occupation": "schoolteacher",
        "personality": "strict, fair, privately sad; very well-read; left a more interesting life for this one",
        "backstory": "Was engaged once. He did not come back from abroad. She took the teaching post and stayed.",
        "home": "Stone Cottage", "work": "Millhaven School",
        "money": 25, "mood": "composed",
        "x": 34, "y": 48,
    },
    {
        "name": "Arthur Finch", "age": 45, "gender": "male",
        "occupation": "farmer",
        "personality": "stoic, serious, distrustful of town people; fair but unsmiling; deeply superstitious",
        "backstory": "Runs Finch Farm on the north edge of town. Bought it from old Thomas Webb.",
        "home": "Finch Farm", "work": "Finch Farm",
        "money": 90, "mood": "watchful",
        "x": 31, "y": 22,
    },
    {
        "name": "Maggie Finch", "age": 42, "gender": "female",
        "occupation": "farmer's wife",
        "personality": "practical, no-nonsense, quietly fierce; manages the household accounts with iron discipline",
        "backstory": "Has run the farm household since marrying Arthur twenty years ago. Puts up with no nonsense from anyone.",
        "home": "Finch Farm", "work": "Finch Farm",
        "money": 30, "mood": "businesslike",
        "x": 32, "y": 22,
    },
    {
        "name": "Danny Finch", "age": 18, "gender": "male",
        "occupation": "farm labourer (son)",
        "personality": "rebellious, brooding, secretly reads poetry; wants to leave; hasn't left yet",
        "backstory": "Arthur and Maggie's only son. Resents the farm. Writes in a notebook he hides under a floorboard.",
        "home": "Finch Farm", "work": "Finch Farm",
        "money": 8, "mood": "restless",
        "x": 33, "y": 23,
    },
    {
        "name": "Constable Bob Norris", "age": 48, "gender": "male",
        "occupation": "village constable",
        "personality": "slow-moving, methodical, not unintelligent but deeply unimaginative; dislikes change",
        "backstory": "Has been Millhaven's constable for fifteen years. The most crime he has dealt with was a stolen pig.",
        "home": "Norris Cottage", "work": "Constable's Station",
        "money": 35, "mood": "placid",
        "x": 58, "y": 58,
    },
    {
        "name": "Clara Vane", "age": 25, "gender": "female",
        "occupation": "seamstress",
        "personality": "vain, romantic, dramatic; reads too many novels; surprisingly good at her craft",
        "backstory": "Arrived from the city two years ago after a mysterious falling-out. Refuses to explain.",
        "home": "Vane's Rooms", "work": "Clara's Needlework",
        "money": 20, "mood": "wistful",
        "x": 52, "y": 58,
    },
    {
        "name": "Old Henry Marsh", "age": 70, "gender": "male",
        "occupation": "retired sailor",
        "personality": "talkative, full of improbable stories, hard of hearing; genuinely has been everywhere",
        "backstory": "Sailed merchant ships for forty years. Retired to Millhaven because he inherited a house here.",
        "home": "Marsh's House", "work": None,
        "money": 50, "mood": "nostalgic",
        "x": 14, "y": 50,
    },
    {
        "name": "Susan Price", "age": 32, "gender": "female",
        "occupation": "washerwoman",
        "personality": "earthy, loud, funny; knows everything about everyone; not malicious but irresistible about sharing",
        "backstory": "Takes in washing from several households. Has three children (not in this simulation). Knows more secrets than the doctor.",
        "home": None, "work": None,
        "money": 10, "mood": "gossipy",
        "x": 49, "y": 51,
    },
    {
        "name": "Jack Crow", "age": 27, "gender": "male",
        "occupation": "odd-job man, occasional poacher",
        "personality": "charming, unreliable, quick-witted; not exactly dishonest but not exactly honest either",
        "backstory": "Drifted into Millhaven three years ago and never left. Does odd jobs. Knows where the rabbits run.",
        "home": None, "work": None,
        "money": 7, "mood": "sly",
        "x": 45, "y": 58,
    },
]


# -----------------------------------------------------------------------
# Object definitions (~200 objects at various locations)
# -----------------------------------------------------------------------

def make_objects(building_map):
    """Return list of object dicts. building_map: name -> id"""
    objs = []

    def bldloc(bname, ox, oy):
        """Object inside a named building."""
        bid = building_map.get(bname)
        return {"building_id": bid, "x": ox, "y": oy, "z": 0}

    def road(ox, oy):
        return {"x": ox, "y": oy, "z": 0}

    def farm(ox, oy):
        return {"x": ox, "y": oy, "z": 0}

    # === The Millhaven Arms ===
    inn_items = [
        ("oak bar counter", "furniture", "A long bar of dark oak, scratched and stained.", False, 0, 10, {"immovable": True}),
        ("bar stool", "furniture", "A wooden stool, one leg repaired with wire.", True, 0, 2, {}),
        ("bar stool", "furniture", "A wooden stool, slightly wobbly.", True, 0, 2, {}),
        ("bar stool", "furniture", "A battered wooden stool.", True, 0, 2, {}),
        ("pint of ale", "food", "A full pint of dark local ale, slightly warm.", True, 4, 2, {"edible": True, "nourishment": 5, "alcohol": True}),
        ("pint of ale", "food", "A frothy pint of bitter.", True, 4, 2, {"edible": True, "nourishment": 5, "alcohol": True}),
        ("bread roll", "food", "A thick bread roll, fresh this morning.", True, 3, 1, {"edible": True, "nourishment": 15}),
        ("cold mutton", "food", "Cold roast mutton on a chipped plate.", True, 5, 3, {"edible": True, "nourishment": 25}),
        ("candle", "light", "A tallow candle in a tin holder.", True, 1, 1, {"lit": False}),
        ("candle", "light", "A half-burnt candle.", True, 1, 1, {"lit": False}),
        ("fireplace", "furniture", "A wide stone fireplace with embers still glowing.", False, 0, 5, {"immovable": True, "warm": True}),
        ("wooden table", "furniture", "A heavy oak table surrounded by chairs.", False, 0, 3, {"immovable": True}),
        ("wooden chair", "furniture", "A plain wooden chair.", True, 0, 1, {}),
        ("wooden chair", "furniture", "A plain wooden chair, initials carved in the back.", True, 0, 1, {}),
        ("ledger", "item", "James Harker's accounts ledger, figures in cramped script.", True, 0, 2, {"readable": True}),
        ("room key", "item", "An iron key on a wooden fob marked 'Room 1'.", True, 5, 3, {"key": True}),
        ("room key", "item", "An iron key on a wooden fob marked 'Room 2'.", True, 5, 3, {"key": True}),
        ("notice board", "item", "A board with several pinned papers and notices.", False, 0, 1, {"readable": True, "immovable": True}),
    ]
    for name, otype, desc, portable, val, weight, props in inn_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 46, "y": 51, "z": 0})

    # === General Store ===
    store_items = [
        ("loaf of bread", "food", "A dense wholemeal loaf.", True, 3, 4, {"edible": True, "nourishment": 30}),
        ("loaf of bread", "food", "A round cottage loaf.", True, 3, 4, {"edible": True, "nourishment": 30}),
        ("wedge of cheese", "food", "A yellow wedge of hard cheese.", True, 4, 3, {"edible": True, "nourishment": 20}),
        ("dried sausage", "food", "A knot of cured pork sausage.", True, 5, 2, {"edible": True, "nourishment": 25}),
        ("apple", "food", "A bright red apple.", True, 1, 1, {"edible": True, "nourishment": 10}),
        ("apple", "food", "A slightly bruised apple.", True, 1, 1, {"edible": True, "nourishment": 8}),
        ("canteen", "tool", "A tin canteen for water.", True, 4, 3, {"holdsliquid": True}),
        ("lantern", "light", "An oil lantern with a clean chimney.", True, 8, 4, {"lit": False, "needsoil": True}),
        ("oil flask", "item", "A small flask of lamp oil.", True, 3, 2, {"fuel": True}),
        ("rope", "tool", "Twenty feet of hemp rope.", True, 6, 8, {}),
        ("knife", "tool", "A plain folding clasp knife.", True, 7, 2, {"weapon": True, "tool": True}),
        ("matches", "item", "A box of wax matches.", True, 2, 1, {"ignites": True}),
        ("sack", "container", "A coarse hessian sack.", True, 1, 2, {}),
        ("woollen blanket", "clothing", "A grey woollen blanket, warm but scratchy.", True, 8, 5, {"warmth": 20}),
        ("shop counter", "furniture", "The shop counter with its brass weighing scales.", False, 0, 10, {"immovable": True}),
        ("weighing scales", "tool", "Brass balance scales.", False, 0, 5, {"immovable": True}),
        ("jar of boiled sweets", "food", "A glass jar of coloured hard sweets.", True, 2, 3, {"edible": True, "nourishment": 5}),
    ]
    for name, otype, desc, portable, val, weight, props in store_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 52, "y": 52, "z": 0})

    # === Bakery ===
    bakery_items = [
        ("fresh loaf", "food", "A hot loaf straight from the oven.", True, 3, 5, {"edible": True, "nourishment": 35}),
        ("fresh loaf", "food", "A golden loaf with a cracked crust.", True, 3, 5, {"edible": True, "nourishment": 35}),
        ("bun", "food", "A sticky currant bun.", True, 2, 2, {"edible": True, "nourishment": 12}),
        ("bun", "food", "A plain floury bun.", True, 2, 2, {"edible": True, "nourishment": 12}),
        ("pie", "food", "A hand pie filled with pork and onion.", True, 5, 6, {"edible": True, "nourishment": 40}),
        ("bread paddle", "tool", "A long wooden paddle for handling bread.", False, 0, 5, {"immovable": False}),
        ("stone oven", "furniture", "A vast stone bread oven, radiating heat.", False, 0, 20, {"immovable": True, "warm": True}),
        ("flour sack", "container", "A half-empty sack of white flour.", False, 0, 15, {"immovable": False}),
        ("mixing bowl", "tool", "A large ceramic mixing bowl.", True, 2, 8, {}),
    ]
    for name, otype, desc, portable, val, weight, props in bakery_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 43, "y": 44, "z": 0})

    # === Smithy ===
    smithy_items = [
        ("anvil", "tool", "A heavy iron anvil, pocked with hammer marks.", False, 0, 200, {"immovable": True}),
        ("hammer", "tool", "A heavy blacksmith's hammer.", True, 8, 6, {"weapon": True, "tool": True}),
        ("tongs", "tool", "Long iron tongs for gripping hot metal.", True, 4, 3, {}),
        ("horseshoe", "item", "A new iron horseshoe, still warm.", True, 3, 4, {}),
        ("horseshoe", "item", "A worn horseshoe awaiting repair.", True, 1, 4, {}),
        ("iron nail", "item", "A handful of square-cut nails.", True, 1, 1, {}),
        ("iron nail", "item", "A handful of iron nails.", True, 1, 1, {}),
        ("bellows", "tool", "Large leather bellows for the forge fire.", False, 0, 8, {}),
        ("forge", "furniture", "The main forge, a brick-lined pit with glowing coals.", False, 0, 30, {"immovable": True, "warm": True, "dangerous": True}),
        ("bucket of water", "container", "An iron bucket of water for quenching.", True, 0, 8, {"liquid": "water"}),
        ("file", "tool", "A metal file for finishing ironwork.", True, 3, 1, {}),
    ]
    for name, otype, desc, portable, val, weight, props in smithy_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 39, "y": 58, "z": 0})

    # === Church ===
    church_items = [
        ("pew", "furniture", "A hard wooden pew with a kneeler.", False, 0, 20, {"immovable": True}),
        ("pew", "furniture", "A worn wooden pew.", False, 0, 20, {"immovable": True}),
        ("altar", "furniture", "The simple stone altar, draped in white cloth.", False, 0, 40, {"immovable": True}),
        ("hymn book", "item", "A battered book of hymns.", True, 0, 1, {"readable": True}),
        ("hymn book", "item", "A hymn book with several pages loose.", True, 0, 1, {"readable": True}),
        ("collection plate", "container", "A wooden plate for offerings. It contains a few coins.", True, 0, 1, {"money": 3}),
        ("brass candlestick", "item", "A tall brass candlestick.", True, 5, 4, {}),
        ("brass candlestick", "item", "A matching brass candlestick.", True, 5, 4, {}),
        ("bible", "item", "A large leather-bound Bible on a wooden stand.", False, 0, 8, {"readable": True}),
        ("bell rope", "item", "The thick rope that rings the church bell.", False, 0, 3, {"immovable": True}),
    ]
    for name, otype, desc, portable, val, weight, props in church_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 57, "y": 39, "z": 0})

    # === Town Hall ===
    hall_items = [
        ("mahogany desk", "furniture", "A large mahogany desk with many drawers.", False, 0, 30, {"immovable": True}),
        ("notice board", "item", "A board covered in official notices.", False, 0, 5, {"readable": True, "immovable": True}),
        ("town map", "item", "A framed map of Millhaven and surrounding lands.", False, 0, 8, {"readable": True, "immovable": True}),
        ("ink pot", "item", "A glass ink pot, nearly full.", True, 1, 1, {}),
        ("quill pen", "item", "A goose-quill pen.", True, 0, 0, {}),
        ("ledger", "item", "The official records ledger of Millhaven.", True, 0, 4, {"readable": True}),
        ("chair", "furniture", "A formal wooden chair.", True, 0, 3, {}),
    ]
    for name, otype, desc, portable, val, weight, props in hall_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 53, "y": 47, "z": 0})

    # === Doctor's Surgery ===
    doc_items = [
        ("medicine cabinet", "furniture", "A locked glass cabinet of medicines.", False, 0, 15, {"immovable": True, "locked": True}),
        ("medical bag", "container", "The doctor's black leather bag.", True, 0, 5, {}),
        ("bandage", "medical", "A clean cotton bandage.", True, 2, 1, {"heals": 10}),
        ("bandage", "medical", "A sterile bandage, rolled tight.", True, 2, 1, {"heals": 10}),
        ("tincture bottle", "medical", "A small brown bottle labelled 'Iodine'.", True, 3, 1, {"heals": 5, "stings": True}),
        ("examination table", "furniture", "A leather-padded examination table.", False, 0, 10, {"immovable": True}),
        ("anatomy book", "item", "A medical textbook with detailed illustrations.", True, 0, 6, {"readable": True}),
        ("stethoscope", "medical", "A brass and rubber stethoscope.", True, 10, 2, {}),
        ("scales", "tool", "Baby weighing scales.", False, 0, 8, {}),
    ]
    for name, otype, desc, portable, val, weight, props in doc_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 59, "y": 52, "z": 0})

    # === School ===
    school_items = [
        ("school desk", "furniture", "A child's wooden desk with an inkwell hole.", False, 0, 3, {"immovable": True}),
        ("school desk", "furniture", "A desk with initials scratched into it.", False, 0, 3, {"immovable": True}),
        ("teacher's desk", "furniture", "Harriet Stone's large teacher's desk.", False, 0, 8, {"immovable": True}),
        ("blackboard", "furniture", "A slate blackboard on a wooden frame.", False, 0, 5, {"immovable": True}),
        ("chalk", "item", "A stick of white chalk.", True, 0, 0, {}),
        ("reading primer", "item", "A battered reading primer used by the younger students.", True, 0, 1, {"readable": True}),
        ("arithmetic book", "item", "An arithmetic textbook, marginally annotated.", True, 0, 2, {"readable": True}),
        ("globe", "item", "A small paper-covered globe on a brass stand.", True, 3, 3, {}),
        ("ruler", "tool", "A wooden ruler, thirty centimetres.", True, 1, 1, {}),
        ("cane", "item", "A thin disciplinary cane. Unused for years.", True, 0, 1, {}),
    ]
    for name, otype, desc, portable, val, weight, props in school_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 34, "y": 48, "z": 0})

    # === Seamstress / Clara's shop ===
    sewing_items = [
        ("sewing machine", "tool", "A treadle sewing machine, whirring quietly.", False, 0, 25, {"immovable": True}),
        ("bolt of blue cloth", "item", "A bolt of fine blue wool cloth.", True, 15, 20, {}),
        ("bolt of linen", "item", "Plain undyed linen, rolled tight.", True, 8, 15, {}),
        ("thread spool", "item", "A spool of white thread.", True, 1, 0, {}),
        ("thread spool", "item", "A spool of black thread.", True, 1, 0, {}),
        ("scissors", "tool", "Sharp dressmaking scissors.", True, 5, 2, {"weapon": True}),
        ("thimble", "item", "A silver thimble.", True, 3, 0, {}),
        ("pin cushion", "item", "A red velvet pin cushion bristling with pins.", True, 1, 0, {}),
        ("dress pattern", "item", "A paper dress pattern pinned to card.", True, 0, 1, {"readable": True}),
        ("mirror", "furniture", "A tall dressing mirror in a wooden frame.", False, 0, 10, {"immovable": True}),
    ]
    for name, otype, desc, portable, val, weight, props in sewing_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 52, "y": 58, "z": 0})

    # === Post Office ===
    post_items = [
        ("counter", "furniture", "The post office counter with a grille.", False, 0, 10, {"immovable": True}),
        ("postal scales", "tool", "Brass postal scales for weighing letters.", False, 0, 5, {}),
        ("letter", "item", "A sealed letter addressed to the Rector.", True, 0, 0, {"readable": False}),
        ("letter", "item", "An official-looking envelope stamped with a wax seal.", True, 0, 0, {}),
        ("stamp book", "item", "A book of postage stamps.", True, 2, 0, {}),
        ("postbag", "container", "The morning's incoming post, not yet sorted.", True, 0, 3, {}),
    ]
    for name, otype, desc, portable, val, weight, props in post_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 46, "y": 58, "z": 0})

    # === Constable's Station ===
    police_items = [
        ("desk", "furniture", "Bob Norris's desk, covered in papers.", False, 0, 5, {"immovable": True}),
        ("handcuffs", "item", "A pair of iron handcuffs on a hook.", True, 3, 2, {}),
        ("truncheon", "item", "A wooden police truncheon.", True, 5, 2, {"weapon": True}),
        ("wanted poster", "item", "A faded 'Wanted' poster of a man called Silas Drew.", False, 0, 0, {"readable": True, "immovable": True}),
        ("cell key", "item", "The key to the station's single cell.", True, 0, 1, {"key": True}),
        ("police notebook", "item", "Norris's duty notebook, entries brief and misspelled.", True, 0, 1, {"readable": True}),
    ]
    for name, otype, desc, portable, val, weight, props in police_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 58, "y": 58, "z": 0})

    # === Town square / market area ===
    market_items = [
        ("market stall", "furniture", "A wooden market stall, folded up for now.", False, 0, 10, {"immovable": True}),
        ("water trough", "container", "A stone water trough for horses.", False, 0, 30, {"immovable": True, "liquid": "water"}),
        ("hitching post", "furniture", "An iron hitching post set in the cobbles.", False, 0, 5, {"immovable": True}),
        ("discarded apple core", "item", "A gnawed apple core on the ground.", True, 0, 0, {}),
        ("penny coin", "money", "A copper penny, heads up on the cobbles.", True, 1, 0, {"money": 1}),
        ("lost glove", "clothing", "A single woollen glove, left-handed.", True, 0, 0, {}),
        ("pamphlet", "item", "A religious pamphlet from the Rector, already damp.", True, 0, 0, {"readable": True}),
        ("broken wheel spoke", "item", "A wooden wheel spoke, snapped clean.", True, 0, 2, {}),
        ("dog", "animal", "A scruffy terrier, nosing around for scraps.", True, 0, 5, {"alive": True}),
        ("pigeon", "animal", "A fat town pigeon, unconcerned.", True, 0, 1, {"alive": True, "flies": True}),
    ]
    for name, otype, desc, portable, val, weight, props in market_items:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": 50, "y": 50, "z": 0})

    # === Roads / outdoor objects ===
    road_objects = [
        (18, 50, "milestone", "item", "A weathered stone milestone reading 'To Greystone 7 miles'.", False, 0, 20, {"readable": True, "immovable": True}),
        (50, 18, "signpost", "item", "A wooden signpost pointing north: 'Finch Farm'.", False, 0, 5, {"readable": True, "immovable": True}),
        (50, 82, "signpost", "item", "A weathered signpost pointing south: 'Meadow Farm'.", False, 0, 5, {"readable": True, "immovable": True}),
        (38, 50, "gas lamp", "light", "An iron gas street lamp, unlit in daytime.", False, 0, 15, {"immovable": True, "lit": False}),
        (62, 50, "gas lamp", "light", "A gas street lamp on a cast-iron pole.", False, 0, 15, {"immovable": True, "lit": False}),
        (50, 38, "gas lamp", "light", "A street lamp at the road junction.", False, 0, 15, {"immovable": True, "lit": False}),
        (50, 62, "gas lamp", "light", "A slightly bent gas lamp.", False, 0, 15, {"immovable": True, "lit": False}),
        (20, 49, "stone wall", "furniture", "A low dry-stone wall bordering the road.", False, 0, 40, {"immovable": True}),
        (70, 50, "wooden gate", "furniture", "A five-bar gate hanging slightly open.", True, 3, 8, {"gate": True}),
        (46, 50, "puddle", "item", "A wide muddy puddle from last night's rain.", False, 0, 0, {"immovable": True}),
        (55, 50, "overturned crate", "container", "An empty wooden crate lying on its side.", True, 0, 5, {}),
        (43, 50, "horse dung", "item", "A pile of horse dung, still fresh.", True, 0, 2, {}),
    ]
    for x, y, name, otype, desc, portable, val, weight, props in road_objects:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": x, "y": y, "z": 0})

    # === North farm objects ===
    farm_objects = [
        (30, 29, "water bucket", "food", "A wooden bucket of clean pump water, kept by the farmhouse door.", False, 0, 8, {"drinkable": True, "liquid": "water", "hydration": 45, "immovable": True}),
        (30, 29, "bread heel", "food", "The end of a coarse loaf, dry but edible.", True, 1, 1, {"edible": True, "nourishment": 18}),
        (31, 29, "milk pail", "food", "A small pail of fresh milk from the morning milking.", False, 1, 6, {"drinkable": True, "liquid": "milk", "hydration": 25, "nourishment": 12, "immovable": True}),
        (31, 20, "plough", "tool", "A heavy iron plough, propped against the barn.", False, 0, 60, {"immovable": False}),
        (32, 20, "pitchfork", "tool", "A wooden-handled pitchfork.", True, 5, 4, {"weapon": True, "tool": True}),
        (33, 20, "scythe", "tool", "A long-handled scythe with a curved blade.", True, 8, 5, {"weapon": True, "dangerous": True}),
        (28, 22, "water pump", "tool", "A cast-iron hand pump over a stone trough.", False, 0, 30, {"immovable": True, "water": True}),
        (35, 25, "haystack", "item", "A large dry haystack.", False, 0, 50, {"immovable": True, "flammable": True}),
        (27, 20, "chicken", "animal", "A speckled hen pecking at the ground.", True, 5, 2, {"alive": True, "edible": True}),
        (27, 21, "chicken", "animal", "A brown hen, clucking softly.", True, 5, 2, {"alive": True, "edible": True}),
        (28, 20, "pig", "animal", "A large pink pig in a muddy pen.", False, 15, 80, {"alive": True, "edible": True}),
        (30, 24, "cow", "animal", "A brown and white dairy cow.", False, 30, 100, {"alive": True}),
        (30, 19, "barn door", "furniture", "The wide barn door, weathered and creaking.", False, 0, 20, {"immovable": True, "door": True}),
        (36, 22, "sack of seed", "container", "A heavy sack of grain seed, tightly tied.", True, 4, 12, {}),
        (37, 23, "wheelbarrow", "tool", "A wooden wheelbarrow with an iron wheel.", True, 3, 10, {}),
        (38, 24, "fence post", "item", "A spare fence post, leaning against the wall.", True, 0, 4, {}),
        (25, 18, "milestone", "item", "A mossy stone post marking the farm boundary.", False, 0, 20, {"immovable": True}),
    ]
    for x, y, name, otype, desc, portable, val, weight, props in farm_objects:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": x, "y": y, "z": 0})

    # === South farm objects ===
    sfarm_objects = [
        (68, 70, "water bucket", "food", "A wooden bucket of clean water near the farmhouse entrance.", False, 0, 8, {"drinkable": True, "liquid": "water", "hydration": 45, "immovable": True}),
        (68, 70, "oatcake", "food", "A plain oatcake, tough but filling.", True, 1, 1, {"edible": True, "nourishment": 16}),
        (68, 73, "apple tree", "forageable", "A gnarled old apple tree heavy with fruit.", False, 0, 10, {"immovable": True, "forage_yield": "apple", "forage_nourishment": 8}),
        (69, 74, "apple tree", "forageable", "A young apple tree, still bearing well.", False, 0, 10, {"immovable": True, "forage_yield": "apple", "forage_nourishment": 8}),
        (65, 71, "beehive", "item", "A wooden beehive, buzzing quietly.", False, 0, 5, {"immovable": True, "dangerous": True}),
        (67, 75, "garden fork", "tool", "A heavy garden fork.", True, 4, 5, {"tool": True}),
        (63, 73, "water butt", "container", "A large wooden barrel collecting rainwater.", False, 0, 20, {"immovable": True, "liquid": "water"}),
        (66, 78, "scarecrow", "item", "A straw-stuffed scarecrow in an old coat.", False, 0, 2, {"immovable": True}),
        (72, 76, "rabbit trap", "tool", "A small iron rabbit trap, set and hidden in the grass.", True, 3, 2, {"trap": True, "dangerous": True}),
    ]
    for x, y, name, otype, desc, portable, val, weight, props in sfarm_objects:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": x, "y": y, "z": 0})

    # === Residential / scattered ===
    misc_objects = [
        (22, 48, "garden gate", "furniture", "Webb Cottage's wooden garden gate, painted green.", False, 0, 3, {"door": True}),
        (23, 47, "rose bush", "plant", "An untidy rose bush with late blooms.", False, 0, 2, {"immovable": True, "sharp": True}),
        (22, 50, "vegetable patch", "plant", "Thomas Webb's vegetable patch: cabbages and turnips.", False, 0, 5, {"immovable": True}),
        (55, 40, "park bench", "furniture", "A wrought-iron park bench facing the church.", False, 0, 10, {"immovable": True}),
        (57, 41, "oak tree", "plant", "A spreading oak tree at the park's edge.", False, 0, 20, {"immovable": True, "climbable": True}),
        (57, 35, "fallen branch", "item", "A thick branch broken from the oak in last winter's storm.", True, 0, 8, {}),
        (14, 51, "ship's wheel", "item", "A small decorative ship's wheel in Henry Marsh's garden.", False, 3, 6, {"immovable": True}),
        (13, 50, "garden wall", "furniture", "Henry Marsh's low stone garden wall.", False, 0, 15, {"immovable": True}),
        (45, 43, "flour dusted step", "item", "The bakery doorstep, perpetually dusted with flour.", False, 0, 0, {"immovable": True}),
        (34, 49, "school bell", "item", "A brass bell mounted above the school door.", False, 5, 8, {"immovable": True}),
        (53, 48, "war memorial", "item", "A small stone war memorial listing six local names.", False, 0, 30, {"readable": True, "immovable": True}),
        (70, 44, "rectory gate", "furniture", "The rectory's iron gate with a latch.", False, 0, 5, {"door": True}),
        (45, 58, "Jack's handcart", "tool", "Jack Crow's battered handcart, often parked here.", True, 2, 10, {}),
        (30, 56, "tethered goat", "animal", "A white goat tethered to a stake, eating everything in reach.", True, 10, 20, {"alive": True}),
        (23, 48, "stream bank", "item", "The muddy bank of the stream, reeds growing thickly.", False, 0, 0, {"immovable": True}),
        (24, 47, "stepping stones", "item", "A line of flat stones crossing the stream.", False, 0, 10, {"immovable": True}),
    ]
    for x, y, name, otype, desc, portable, val, weight, props in misc_objects:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": int(portable), "value": val, "weight": weight,
                     "properties": json.dumps(props), "x": x, "y": y, "z": 0})

    # === Homes (beds and basic furniture) ===
    home_beds = [
        (22, 48, "iron bedstead", "bed", "A narrow iron bedstead with a patchwork quilt."),
        (44, 64, "brass bed", "bed", "A brass-framed double bed with heavy curtains."),
        (65, 55, "wooden bed", "bed", "A simple wooden bedframe with a straw mattress."),
        (43, 37, "iron bedstead", "bed", "The baker's iron bedstead, neatly made."),
        (71, 43, "four-poster bed", "bed", "The rector's grand four-poster, too large for the room."),
        (64, 41, "narrow bed", "bed", "The doctor's spare narrow bed."),
        (30, 57, "single bed", "bed", "A narrow single bed with a clean white coverlet."),
        (71, 63, "copper bed", "bed", "A copper-framed bed, well polished."),
        (13, 49, "ship's hammock", "bed", "Old Henry Marsh still sleeps in a sailor's hammock."),
    ]
    for x, y, name, otype, desc in home_beds:
        objs.append({"name": name, "object_type": otype, "description": desc,
                     "is_portable": 0, "value": 0, "weight": 20,
                     "properties": json.dumps({"sleep": True}), "x": x, "y": y, "z": 0})

    return objs


# -----------------------------------------------------------------------
# Environmental & forageable scatter (random world texture)
#
# These are generated once at init time and sprinkled across eligible
# outdoor terrain. They are deliberately tile-local: no map_icon, never
# mentioned at a distance — the engine's display layer (world.py, minimap.py)
# excludes object_type 'environmental'/'forageable' from any radius-based
# scan, so they only ever surface when the player is standing directly on
# their tile. For that reason none of them may carry 'edible'/'drinkable'/
# 'liquid'/'water' directly — only the produce item created by foraging does.
#
# 'forageable' objects are tangible — they appear in the "Here:" listing so
# the player knows there's something to interact with. 'environmental'
# objects are pure ambient texture: engine.build_context/action_look offer
# them to the LLM as optional flavour ("you may notice...") rather than
# itemising them as things the player could pick up or use — see
# engine.build_context for how the split is made.
# -----------------------------------------------------------------------

_SCATTER_TEMPLATES = {
    'field': [
        ("dandelion patch", "A scatter of dandelions, their heads gone to seed.",
         "environmental", {"immovable": True}),
        ("clump of nettles", "A dense clump of stinging nettles.",
         "environmental", {"immovable": True, "sharp": True}),
        ("thistle patch", "A patch of tall purple thistles.",
         "environmental", {"immovable": True, "sharp": True}),
        ("molehill", "A fresh molehill, dark earth heaped on the grass.",
         "environmental", {"immovable": True}),
        ("blackberry bush", "A tangled bush, heavy with ripening blackberries.",
         "forageable", {"immovable": True, "forage_yield": "blackberries", "forage_nourishment": 6}),
        ("pear tree", "A pear tree, its branches bowed with fruit.",
         "forageable", {"immovable": True, "forage_yield": "pear", "forage_nourishment": 7}),
    ],
    'farmland': [
        ("clump of nettles", "A dense clump of nettles along the hedge.",
         "environmental", {"immovable": True, "sharp": True}),
        ("patch of clover", "A low patch of clover, bees drifting lazily over it.",
         "environmental", {"immovable": True}),
        ("rusted harrow", "An abandoned harrow, half-swallowed by weeds.",
         "environmental", {"immovable": True}),
        ("blackberry bramble", "A bramble thicket along the hedge line, dark with berries.",
         "forageable", {"immovable": True, "forage_yield": "blackberries", "forage_nourishment": 6}),
        ("apple tree", "An old apple tree growing wild at the field's edge.",
         "forageable", {"immovable": True, "forage_yield": "apple", "forage_nourishment": 8}),
    ],
    'wilderness': [
        ("gorse bush", "A spiky gorse bush, bright with yellow flowers.",
         "environmental", {"immovable": True, "sharp": True}),
        ("cluster of toadstools", "A cluster of pale toadstools at the foot of a tree.",
         "environmental", {"immovable": True}),
        ("tumble of rocks", "A tumble of weather-worn rocks.",
         "environmental", {"immovable": True}),
        ("wasps' nest", "A papery grey nest wedged low in the branches.",
         "environmental", {"immovable": True, "dangerous": True}),
        ("pile of debris", "A heap of broken branches and matted leaves.",
         "environmental", {"immovable": True}),
        ("blackberry bush", "A wild bramble, dark with ripe blackberries.",
         "forageable", {"immovable": True, "forage_yield": "blackberries", "forage_nourishment": 6}),
        ("cluster of mushrooms", "A cluster of plump brown mushrooms, good enough to eat.",
         "forageable", {"immovable": True, "forage_yield": "mushrooms", "forage_nourishment": 5}),
    ],
    'park': [
        ("lavender bush", "A neat bush of lavender, humming with bees.",
         "environmental", {"immovable": True}),
        ("bed of daffodils", "A bright bed of daffodils nodding in the breeze.",
         "environmental", {"immovable": True}),
        ("ornamental shrub", "A neatly clipped ornamental shrub.",
         "environmental", {"immovable": True}),
    ],
    'track': [
        ("clump of dandelions", "Dandelions growing thick in the verge.",
         "environmental", {"immovable": True}),
        ("patch of daisies", "A scatter of daisies along the track.",
         "environmental", {"immovable": True}),
        ("roadside well", "An old stone well beside the track, its bucket on a rope.",
         "forageable", {"immovable": True, "forage_yield": "water", "forage_hydration": 40, "forage_liquid": "water"}),
    ],
    'stream': [
        ("bed of reeds", "A dense bed of reeds at the water's edge.",
         "environmental", {"immovable": True}),
        ("clump of watercress", "A bright green clump of watercress trailing in the current.",
         "environmental", {"immovable": True}),
        ("smooth boulder", "A large smooth boulder, half-submerged in the stream.",
         "environmental", {"immovable": True}),
        ("clear pool", "A clear, still pool fed by the stream.",
         "forageable", {"immovable": True, "forage_yield": "water", "forage_hydration": 40, "forage_liquid": "water"}),
    ],
}

_ENV_CHANCE = 0.50      # per-eligible-cell chance of an environmental object
_FORAGE_CHANCE = 0.14   # per-eligible-cell chance of a forageable object


def make_environmental_objects(building_cells, occupied_cells):
    """Scatter randomly-generated environmental & forageable texture across
    eligible outdoor terrain.

    building_cells: set of (x, y, building_id) — never scatter onto buildings.
    occupied_cells: set of (x, y) already holding a hand-placed object — skip
    those tiles so the random layer never collides with named landmarks.
    """
    building_xy = {(bx, by) for (bx, by, _bid) in building_cells}
    objs = []
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            if (x, y) in building_xy or (x, y) in occupied_cells:
                continue
            templates = _SCATTER_TEMPLATES.get(terrain_at(x, y))
            if not templates:
                continue
            forageables = [t for t in templates if t[2] == 'forageable']
            environmentals = [t for t in templates if t[2] == 'environmental']
            roll = random.random()
            template = None
            if forageables and roll < _FORAGE_CHANCE:
                template = random.choice(forageables)
            elif environmentals and roll < _FORAGE_CHANCE + _ENV_CHANCE:
                template = random.choice(environmentals)
            if not template:
                continue
            name, desc, otype, props = template
            objs.append({"name": name, "object_type": otype, "description": desc,
                         "is_portable": 0, "value": 0, "weight": 5,
                         "properties": json.dumps(props), "x": x, "y": y, "z": 0})
    return objs


# -----------------------------------------------------------------------
# Main initialization function
# -----------------------------------------------------------------------

def init_town():
    print("Initialising Millhaven...")
    db.init_schema()
    print("  Schema created.")

    # 1. Generate all grid locations
    print("  Generating terrain grid (100x100)...")
    building_cells = set()

    # First pass: place buildings in DB and collect footprints
    building_map = {}  # name -> id
    for b in BUILDINGS:
        name, btype, x1, y1, x2, y2, ex, ey, floors, basement, desc = b
        bid = db.insert_building(
            name=name, building_type=btype,
            x1=x1, y1=y1, x2=x2, y2=y2,
            entrance_x=ex, entrance_y=ey,
            description=desc, floor_count=floors, has_basement=basement
        )
        building_map[name] = bid
        for bx in range(x1, x2 + 1):
            for by in range(y1, y2 + 1):
                building_cells.add((bx, by, bid))

    # Second pass: generate terrain for every grid cell
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            bid = None
            terrain = None
            loc_name = None
            desc = None

            # Check if inside a building
            for (bx, by, b_id) in building_cells:
                if bx == x and by == y:
                    bid = b_id
                    terrain = 'building'
                    break

            if terrain is None:
                terrain = terrain_at(x, y)

            # Special location names
            if 46 <= x <= 54 and 46 <= y <= 54:
                loc_name = "Town Square"
            elif y == 50 and 18 <= x <= 82:
                loc_name = "Main Street"
            elif x == 50 and 18 <= y <= 82:
                loc_name = "Mill Road"
            elif 55 <= x <= 60 and 38 <= y <= 43:
                loc_name = "St. Cuthbert's Green"
            elif x < 12 or x > 88 or y < 12 or y > 88:
                loc_name = "Wilderness"
            elif 22 <= x <= 40 and 13 <= y <= 30:
                if terrain == 'farmyard':
                    loc_name = "Finch Farmyard"
                else:
                    loc_name = "North Fields"
            elif 60 <= x <= 78 and 70 <= y <= 87:
                if terrain == 'farmyard':
                    loc_name = "Meadow Farmyard"
                else:
                    loc_name = "South Fields"

            if bid:
                # Look up building name
                for bname, b_id2 in building_map.items():
                    if b_id2 == bid:
                        loc_name = bname
                        break

            db.upsert_location(x, y, 0, loc_name, terrain, bid, desc)

    # Add upstairs cells for multi-floor buildings
    for b in BUILDINGS:
        name, btype, x1, y1, x2, y2, ex, ey, floors, basement, desc = b
        bid = building_map[name]
        if floors > 1:
            for bx in range(x1, x2 + 1):
                for by in range(y1, y2 + 1):
                    db.upsert_location(bx, by, 1, f"{name} (upper floor)", 'upstairs', bid, "An upper floor room.")
        if basement:
            for bx in range(x1, x2 + 1):
                for by in range(y1, y2 + 1):
                    db.upsert_location(bx, by, -1, f"{name} (cellar)", 'cellar', bid, "A low stone cellar.")

    print(f"  {GRID_WIDTH * GRID_HEIGHT} ground locations + upper/lower floors generated.")

    # 2. Place characters
    print("  Placing characters...")
    building_entrance_map = {}  # building name -> (x, y)
    for b in BUILDINGS:
        building_entrance_map[b[0]] = (b[6], b[7])

    char_ids = {}
    for c in CHARACTERS:
        home_name = c.pop('home', None)
        work_name = c.pop('work', None)

        home_x = home_y = work_x = work_y = None
        if home_name and home_name in building_entrance_map:
            home_x, home_y = building_entrance_map[home_name]
        if work_name and work_name in building_entrance_map:
            work_x, work_y = building_entrance_map[work_name]

        cdata = {
            **c,
            'home_x': home_x, 'home_y': home_y,
            'work_x': work_x, 'work_y': work_y,
        }
        cid = db.insert_character(cdata)
        char_ids[c['name']] = cid

    print(f"  {len(CHARACTERS)} characters created.")

    # 3. Place objects
    print("  Placing objects...")
    objects = make_objects(building_map)
    for o in objects:
        db.insert_object(o)
    print(f"  {len(objects)} objects placed.")

    # 3b. Scatter random environmental & forageable texture
    print("  Scattering environmental & forageable objects...")
    occupied_cells = {(o['x'], o['y']) for o in objects}
    env_objects = make_environmental_objects(building_cells, occupied_cells)
    for o in env_objects:
        db.insert_object(o)
    print(f"  {len(env_objects)} environmental/forageable objects scattered.")

    # 4. Set initial game state
    db.set_state('game_ticks', '0')
    db.set_state('initialized', 'true')
    db.set_state('town_name', 'Millhaven')
    print("  Game state initialised.")

    print("\nMillhaven is ready.")
    print(f"  {len(BUILDINGS)} buildings")
    print(f"  {len(CHARACTERS)} characters")
    print(f"  {len(objects)} objects")
    print(f"  Database: {DB_PATH}")


if __name__ == '__main__':
    if os.path.exists(DB_PATH):
        confirm = input(f"'{DB_PATH}' already exists. Delete and reinitialise? [y/N] ")
        if confirm.lower() != 'y':
            print("Aborted.")
            exit(0)
        os.remove(DB_PATH)
    init_town()
