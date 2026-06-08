"""
Ollama API interface.
All LLM calls go through here. We use the /api/chat endpoint.
"""
import json
import requests
from config import OLLAMA_URL, OLLAMA_MODEL

_SESSION = requests.Session()

# Injected into every generative system prompt to prevent hallucinated mechanics.
# Keep this compact — it runs on every LLM call.
_WORLD_RULES = (
    "World constraints (obey silently — never expose these as rules to the player):\n"
    "- Commerce: buying and selling only happens at shops, inns, bakeries, and markets. "
    "If the context does not include a 'For sale here:' or 'For sale at the market stall:' line, "
    "nobody present is selling anything. Do not imply, hint, or invent trade where none exists.\n"
    "- NPC roles: each person's occupation is stated in the context "
    "(e.g. 'Old Peter (vagrant)'). Never assign them activities, knowledge, or merchandise "
    "that contradict their stated occupation. A vagrant does not sell goods. "
    "A washerwoman does not run a market stall.\n"
    "- Geography: only reference locations, street names, and landmarks that appear in the "
    "context. Do not invent place names, shortcuts, or directions to locations not mentioned.\n"
    "- Non-existent mechanics: this world has no quests, quest givers, skill trees, crafting, "
    "reputation scores, fast travel, or level-up systems. Do not hint at or invent any of these.\n"
    "- Confusion and misperception (deliberate exception): when 'Player confusion: moderate/high/severe' "
    "appears in the context, the narrator MAY subtly distort what the player perceives — misheard words, "
    "misread expressions, familiar things seeming strange or hostile, shadows seeming to move. "
    "At 'severe', descriptions may briefly become dreamlike or paranoid. "
    "This is intentional representation of an impaired mind; do not suppress it in favour of accuracy.\n"
    "- Time of day: always use the exact time, weather, and light stated in the context. "
    "Do not contradict them — morning cannot become dusk, night cannot become noon within the same scene.\n"
    "- Mundane objects: physical objects are ordinary unless their description explicitly states otherwise. "
    "A pamphlet is paper and ink; it does not glow, shimmer, pulse, or feel magical unless the description says so.\n"
)


def _chat(messages, temperature=0.7, max_tokens=512):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }
    try:
        resp = _SESSION.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"].strip()
    except requests.exceptions.ConnectionError:
        return None
    except Exception as e:
        return None


def check_ollama():
    """Return True if Ollama is reachable and the model is available."""
    try:
        resp = _SESSION.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = resp.json().get("models", [])
        names = [m["name"] for m in models]
        return any(OLLAMA_MODEL in n for n in names), names
    except Exception:
        return False, []


def parse_command(player_input, context):
    """
    Ask the LLM to classify the player's raw input into a structured action.
    Returns a dict with keys: action, direction, target, speech, interpretation.
    Falls back to a simple keyword parse on failure.
    """
    system = (
        "You are the parser for a text adventure game set in the quiet English village of Millhaven. "
        "Your job is to interpret the player's input and return a JSON object describing their intended action.\n\n"
        "Valid action types:\n"
        "  move       - travel in a direction\n"
        "  look       - observe the current location with no specific target (bare 'look', 'look around')\n"
        "  examine    - inspect a specific object, person, or feature (ALWAYS use this when a target is named, e.g. 'look at the ceiling', 'look at the door', 'examine the table')\n"
        "  take       - pick up an object\n"
        "  drop       - put down an object from inventory\n"
        "  use        - use or interact with an object\n"
        "  speak      - say something aloud\n"
        "  ask        - ask a character something\n"
        "  give       - give an object to a character\n"
        "  buy        - purchase something (also covers 'order', 'get me a', 'can I have a')\n"
        "  sell       - sell something\n"
        "  wait       - pass time\n"
        "  sleep      - rest or sleep\n"
        "  eat        - consume food\n"
        "  drink      - drink water, ale, or other liquid\n"
        "  attack     - act violently toward something\n"
        "  think      - internal thought, no external effect\n"
        "  inventory  - check own inventory\n"
        "  status     - check own stats\n"
        "  enter      - enter a building or area\n"
        "  exit       - leave a building\n"
        "  listen     - pay attention to sounds (target optional)\n"
        "  smell      - pay attention to scents (target optional)\n"
        "  feel       - physically touch something or sense by touch; use this for 'touch X', 'feel X', 'run your hand over X' (target optional but usually present)\n"
        "  query      - a direct question about the world, navigation, or a fact (e.g. 'where is the bar', 'what time does the shop open', 'who lives here')\n"
        "  flee       - run away in panic or urgency (e.g. 'run away', 'flee', 'escape', 'get out of here')\n"
        "  help       - request help\n\n"
        "Choose 'query' whenever the player is asking a question rather than performing an action. "
        "Put the full question text in the 'speech' field.\n\n"
        "For 'say WORDS to NAME' or 'tell NAME WORDS', set action='speak', speech=WORDS only "
        "(not including 'to NAME'), target=NAME.\n\n"
        "Also include a boolean field 'careful': set true if the player uses words like "
        "'carefully', 'closely', 'again', 'more carefully', 'look again', 'closer'. Default false.\n\n"
        "Return ONLY valid JSON, no markdown, no explanation. Example:\n"
        '{"action":"move","direction":"north","target":null,"speech":null,"careful":false,"interpretation":"Player walks north"}'
    )

    user_msg = (
        f"Context: {context}\n\n"
        f"Player input: {player_input}\n\n"
        "Return the JSON action object:"
    )

    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        temperature=0.2,
        max_tokens=200
    )

    if result:
        try:
            # Strip any markdown code fences if present
            cleaned = result.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            return json.loads(cleaned.strip())
        except json.JSONDecodeError:
            pass

    return _fallback_parse(player_input)


_CAREFUL_WORDS = ('careful', 'closely', 'again', 'close', 'more carefully', 'look again')


def _fallback_parse(text):
    t = text.lower().strip()
    careful = any(w in t for w in _CAREFUL_WORDS)
    for d in ['north', 'south', 'east', 'west', 'up', 'down', 'upstairs', 'downstairs',
              'n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']:
        if t in (d, f'go {d}', f'walk {d}', f'move {d}', f'climb {d}', f'head {d}'):
            return {"action": "move", "direction": d, "target": None, "speech": None,
                    "interpretation": f"Move {d}"}
    def _r(action, **kw):
        return {"action": action, "direction": None, "target": None,
                "speech": None, "careful": careful, **kw}

    if t in ('look', 'l', 'look around'):
        return _r("look", interpretation="Look around")
    if t in ('i', 'inv', 'inventory'):
        return _r("inventory", interpretation="Check inventory")
    if t in ('status', 'stats', 'me'):
        return _r("status", interpretation="Check status")
    if t.startswith('say ') or t.startswith('"'):
        speech = text[4:] if t.startswith('say ') else text.strip('"')
        # "say X to NAME" — extract the target from the speech
        target = None
        if ' to ' in speech.lower():
            parts = speech.rsplit(' to ', 1)
            speech = parts[0].strip()
            target = parts[1].strip()
        return _r("speak", speech=speech, target=target, interpretation=f"Speak: {speech}")
    if t.startswith('take ') or t.startswith('pick up '):
        tgt = t.replace('take ', '').replace('pick up ', '')
        return _r("take", target=tgt, interpretation=f"Take {tgt}")
    if t.startswith('drop '):
        tgt = t.replace('drop ', '')
        return _r("drop", target=tgt, interpretation=f"Drop {tgt}")
    if t.startswith('touch ') or t.startswith('feel ') or t.startswith('run your hand'):
        tgt = t.replace('touch ', '').replace('feel ', '').replace('run your hand over ', '')
        return _r("feel", target=tgt.strip(), interpretation=f"Feel {tgt.strip()}")
    if t.startswith('examine ') or t.startswith('look at '):
        tgt = t.replace('examine ', '').replace('look at ', '')
        return _r("examine", target=tgt, careful=careful, interpretation=f"Examine {tgt}")
    for _buy_prefix in ('buy ', 'order ', 'get me a ', 'get me some ', 'can i have a ', 'can i have some '):
        if t.startswith(_buy_prefix):
            tgt = t[len(_buy_prefix):]
            return _r("buy", target=tgt, interpretation=f"Buy {tgt}")
    if t.startswith('sell '):
        tgt = t.replace('sell ', '')
        return _r("sell", target=tgt, interpretation=f"Sell {tgt}")
    if t.startswith('use '):
        tgt = t.replace('use ', '')
        return _r("use", target=tgt, interpretation=f"Use {tgt}")
    if t.startswith('attack ') or t.startswith('hit ') or t.startswith('strike '):
        tgt = t.replace('attack ', '').replace('hit ', '').replace('strike ', '')
        return _r("attack", target=tgt, interpretation=f"Attack {tgt}")
    if t.endswith('?') or any(t.startswith(w) for w in
            ('where ', 'what ', 'who ', 'why ', 'how ', 'when ', 'is there ', 'can i ')):
        return {"action": "query", "direction": None, "target": None, "speech": text,
                "interpretation": f"Question: {text}"}
    return {"action": "think", "direction": None, "target": None, "speech": text,
            "interpretation": "Unclear input — treated as thought"}


def generate_query_response(question, context):
    """
    Answer a direct player question about the world, navigation, or facts.
    Returns a short, practical answer — not a scene description.
    """
    system = (
        "You are a knowledgeable local in the English village of Millhaven. "
        "Answer the player's question plainly and briefly — one to three sentences. "
        "Give practical, specific information. "
        "If the question is about navigation or location, give a clear direction or landmark. "
        "Do NOT re-describe the current scene. Do NOT write in the narrative style. "
        "NEVER say 'I don't understand', 'I'm sorry', 'I cannot', or refer to yourself "
        "as an AI or assistant. If the input is nonsensical, respond in character — "
        "e.g. 'That means nothing to anyone here.' Stay inside the world at all times.\n\n"
        + _WORLD_RULES
    )
    user_msg = f"Context: {context}\n\nQuestion: {question}"
    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        temperature=0.5,
        max_tokens=120,
    )
    return result or "You're not sure about that."


def generate_narrative(situation, context, tone="descriptive"):
    """
    Generate a short narrative paragraph describing what happens.
    situation: brief description of the action/event
    context: world context (location, who's present, etc.)
    """
    system = (
        "You are the narrator of a quiet, immersive English village text adventure. "
        "Write in second person, present tense, in the style of a literary novel. "
        "Be evocative but concise — two to four sentences. No dialogue unless asked. "
        "The village is called Millhaven: unremarkable, slightly melancholy, full of texture. "
        "The context includes 'Player condition:' — most of the time say nothing about "
        "it at all and just describe the scene. Only on rare occasions let it brush the "
        "prose: a hungry mind drifting toward a smell, a tired eye sliding past detail, "
        "a frightened ear catching a sound — shown through what gets noticed or skipped, "
        "never spelled out as cause and effect ('because you feel calm, the grass seems "
        "soothing' is exactly what to avoid). If in doubt, leave it out. "
        "Only reference geographical features (rivers, hills, forests, roads, fountains, streams) "
        "that appear explicitly in the context object lists or location name — never invent them. "
        "Mark-up: the first time you name a person, a tangible object, a building, or a "
        "forageable plant/source that is actually listed as present in the context, wrap that "
        "exact name in double asterisks, e.g. 'You see **Old Peter** by **the well**.' Do not "
        "wrap pronouns, generic nouns, ambient scenery, or anything not listed as present.\n\n"
        + _WORLD_RULES
    )
    user_msg = f"Context: {context}\n\nDescribe this: {situation}"

    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        temperature=0.8,
        max_tokens=180
    )
    return result or situation


def generate_intrusive_thought(condition, confusion_level):
    """Generate one involuntary intrusive thought from an impaired player's perspective."""
    intensity = ("severe paranoia" if confusion_level >= 70 else
                 "high confusion"  if confusion_level >= 50 else
                 "unsettled mind")
    system = (
        "You are the uninvited inner voice of a troubled traveller in a small English village. "
        "Write one involuntary thought or impulse — first person, present tense, "
        "one to two short sentences. No quotation marks. No narration. No stage directions. "
        "The thought is unwanted: desperate, paranoid, dark, or driven by deprivation. "
        "It feels like it surfaces against the person's will from exhaustion, fear, hunger, or thirst. "
        "Do not describe surroundings. Do not address a second person. Be unsettling but brief."
    )
    user_msg = (f"State: {condition}. Intensity: {intensity}. "
                "Generate one intrusive thought.")
    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user",   "content": user_msg}],
        temperature=1.1,
        max_tokens=60,
    )
    if not result:
        return None
    return result.strip().strip('"\'')


def examine_environment(target, location_name, terrain, time_of_day, weather, context,
                        familiarity=0, building_type=None):
    """Describe an environmental feature the player looks at (sky, ground, river, wall, etc.)."""
    length = [
        "Two to three sentences.",
        "One to two sentences — the player has noticed this before.",
        "One sentence only — the player knows this feature well.",
    ][max(0, min(2, familiarity))]

    setting_hint = f"terrain={terrain}"
    if building_type:
        setting_hint += f", building type={building_type}"

    _outdoor_terrains = ('ground', 'cobblestone', 'grass', 'dirt', 'road',
                          'path', 'field', 'farmland', 'farmyard', 'market',
                          'square', 'outdoor')
    is_outdoor = any(t in terrain.lower() for t in _outdoor_terrains) or not building_type
    sky_note = (
        "The sky, clouds, sun, moon, stars, and weather are always visible outdoors. "
        "Never claim the sky cannot be seen unless terrain is explicitly underground or enclosed. "
    ) if is_outdoor else ""

    system = (
        "You are the narrator of a quiet English village text adventure. "
        "The player is looking at a feature of their environment. "
        f"{length} Describe what they observe. "
        f"The player's current setting is: {setting_hint}. "
        + sky_note +
        "Only describe this feature if it is plausible in this setting. "
        "If the named feature would not exist here (e.g. 'the bar' when upstairs in a bedroom, "
        "or 'the forge' in a field), say clearly: 'You don't see any [feature] here.' "
        "Match the time of day, weather, and lighting in the context. "
        "Second person, present tense. No game-mechanic references.\n\n"
        + _WORLD_RULES
    )
    user_msg = (
        f"Context: {context}\n\n"
        f"Location: {location_name}, {setting_hint}, "
        f"time: {time_of_day}, weather: {weather}.\n\n"
        f"The player looks closely at: {target}\n\n"
        "Describe what they see (or explain it isn't here):"
    )
    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        temperature=0.8,
        max_tokens=150,
    )
    return result or f"You don't see any {target} here."


def generate_sense(sense, target, location_name, terrain, building_name,
                   time_of_day, weather, chars_present, objs_present, context,
                   building_type=None):
    """
    Generate a sensory description for listen / smell / feel / taste.
    sense: 'listen' | 'smell' | 'feel' | 'taste'
    target: specific focus (may be None for ambient)
    building_type: e.g. 'inn', 'smithy', 'church' — used to ground the description
    """
    aspects = {
        'listen': ("sounds only — birdsong, footsteps, wind, distant voices, creaking timber, "
                   "rain, fire crackling. Do NOT describe taste, smell, or tactile sensations.",
                   "what they hear"),
        'smell':  ("scents and smells only — earth, smoke, food, damp stone, foliage, ale, "
                   "sawdust. Do NOT describe sounds, sights beyond scent-hints, or taste.",
                   "what they smell"),
        'feel':   ("textures, temperature, and physical sensations — rough stone, cold air, "
                   "smooth wood, dampness, heat from a fire.",
                   "what they feel"),
        'taste':  ("flavour, taste, and texture in the mouth — sweetness, bitterness, "
                   "saltiness, freshness, staleness.",
                   "what they taste"),
    }
    aspect, outcome = aspects.get(sense, ("surroundings", "their impressions"))
    chars_str = ', '.join(chars_present) if chars_present else 'nobody'
    objs_str  = ', '.join(objs_present)  if objs_present  else 'nothing notable'

    setting_str = f"terrain={terrain}"
    if building_type:
        setting_str += f", building_type={building_type}"
    if building_name:
        setting_str += f", inside {building_name}"

    # For targeted touch: strictly tactile, no visual bleed
    if sense == 'feel' and target:
        system = (
            "You are the narrator of a quiet English village text adventure. "
            "The player is physically touching something. "
            "Write two sentences describing ONLY what their hands and skin perceive: "
            "temperature, texture, hardness, moisture, give, grain, weight. "
            "NO visual description. NO light. NO smell. NO sound. Touch only. "
            "Second person, present tense."
        )
        user_msg = (
            f"Context: {context}\n\n"
            f"The player reaches out and touches: {target}\n\n"
            "Describe only the physical sensation of touching it:"
        )
    else:
        target_str = f"They focus on: {target}." if target else "Ambient — no specific focus."
        system = (
            "You are the narrator of a quiet English village text adventure. "
            f"The player is paying close attention to {aspect} "
            f"Describe only what is consistent with the setting: {setting_str}. "
            "Do not invent furniture, machinery, smells, or features inconsistent with "
            "this terrain and building type. "
            "Write two to three sentences, second person, present tense. "
            "Be evocative and specific to the location, time of day, and weather. "
            "The context includes 'Player condition:' — usually ignore it and just "
            "describe the sensation plainly. Once in a while you may let it brush the "
            "edge of the description (what the player's attention catches or slides past), "
            "but never explain it ('your fear makes the sound startle you' is exactly "
            "what to avoid) — show, don't narrate the cause. "
            "Mark-up: the first time you name a person, a tangible object, a building, or "
            "a forageable plant/source that is actually listed in the context as present or "
            "nearby, wrap that exact name in double asterisks, e.g. '**the well** creaks "
            "as you near it.' Do not wrap pronouns, generic nouns, ambient scenery, or "
            "anything not listed as present."
        )
        user_msg = (
            f"Context: {context}\n\n"
            f"Setting: {setting_str}, time: {time_of_day}, weather: {weather}.\n"
            f"People nearby: {chars_str}. Objects nearby: {objs_str}.\n"
            f"{target_str}\n\n"
            f"Describe {outcome}:"
        )
    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        temperature=0.85,
        max_tokens=160,
    )
    return result or "You concentrate, but nothing in particular stands out."


def examine_npc(npc_name, npc_age, npc_occupation, npc_personality, npc_mood,
                npc_activity, context, familiarity=0):
    """
    Generate a close physical description of an NPC and their reaction to being watched.
    Returns a two-part string: the narrator's description, then the NPC's reaction (if any).
    familiarity: 0=never really looked, 1=seen before, 2=know them by sight
    """
    familiarity = max(0, min(2, familiarity))
    desc_instruction = [
        ("First, write two or three sentences describing their physical appearance, "
         "clothing, and what they are currently doing — be specific and sensory."),
        ("Write one sentence on their current expression and what they are doing. "
         "The player has seen them before — skip general appearance."),
        ("Write nothing about appearance — the player knows them. "
         "Go straight to REACTION: only."),
    ][familiarity]
    system = (
        "You are the narrator of a quiet English village text adventure. "
        "The player is observing another person. "
        f"{desc_instruction} "
        "Then, on a new line starting with REACTION:, write one sentence (in third person) "
        "describing how this person reacts to being watched. "
        "Do not invent a name different from the one given. "
        "Do not reference game mechanics."
    )
    user_msg = (
        f"Context: {context}\n\n"
        f"Character: {npc_name}, aged {npc_age}, {npc_occupation}. "
        f"Personality: {npc_personality}. Mood: {npc_mood}. "
        f"Currently: {npc_activity or 'standing nearby'}.\n\n"
        "Describe them and their reaction to being observed:"
    )
    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        temperature=0.85,
        max_tokens=200,
    )
    if not result:
        return f"{npc_name} stands nearby, giving little away."

    if "REACTION:" in result:
        desc, _, reaction = result.partition("REACTION:")
        return desc.strip() + "\n" + reaction.strip()
    return result


def generate_npc_response(npc_name, npc_personality, npc_mood, player_speech, context,
                          conversation_history=None):
    """
    Generate a spoken response from an NPC.
    """
    system = (
        f"You are {npc_name}, a character in the village of Millhaven. "
        f"Personality: {npc_personality}. Current mood: {npc_mood}. "
        "Speak naturally in character. One to three sentences. "
        "Output ONLY the words you speak — no stage directions, no action tags, "
        "no 3rd-person self-narration (do not write 'he says' or 'she laughs' or "
        "describe your own actions). Just the spoken words. "
        "Do not break character or reference game mechanics. "
        "Be consistent with everything you have said in prior turns of this conversation — "
        "never contradict yourself or retract an offer you just made.\n\n"
        + _WORLD_RULES
    )
    history_str = ''
    if conversation_history:
        lines = []
        for entry in conversation_history[-6:]:
            speaker = entry.get('speaker', '?')
            text = entry.get('text', '')
            lines.append(f'{speaker}: "{text}"')
        if lines:
            history_str = 'Conversation so far:\n' + '\n'.join(lines) + '\n\n'
    user_msg = (
        f"Context: {context}\n\n"
        + history_str
        + f"A stranger says to you: \"{player_speech}\"\n\n"
        f"How do you respond?"
    )
    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        temperature=0.85,
        max_tokens=150
    )
    return result or f"{npc_name} looks at you but says nothing."


_LOCATION_FAMILIARITY = [
    # 0 = new: full description
    ("Three to five sentences. Full sensory detail — light, sound, smell, texture. "
     "The player is seeing this for the first time."),
    # 1 = visited once: brief, no sentimentality
    ("One to two sentences. The player has visited once before. "
     "Be matter-of-fact: note only what is present right now, "
     "no nostalgic or welcoming tone."),
    # 2 = well-known: one bare observational sentence
    ("One sentence. The player knows this place well. "
     "One plain observational sentence about the current moment only. "
     "No warmth, no permanent-feature description."),
]


def generate_location_description(location_name, terrain, building_name, time_of_day, weather,
                                  characters_present, objects_present,
                                  light_context="", extra_context="", familiarity=0):
    """
    Generate a rich description of the current location.
    familiarity: 0=new, 1=seen before, 2=well-known
    """
    familiarity = max(0, min(2, familiarity))
    length_instruction = _LOCATION_FAMILIARITY[familiarity]
    system = (
        "You are the narrator of a quiet English village text adventure. "
        "Describe the location in second person present tense. "
        f"{length_instruction} "
        "Match the time of day, weather, and lighting — describe only what is visible. "
        "In poor light, shapes are indistinct; in darkness, little or nothing can be seen. "
        "The context includes 'Player condition:' — most descriptions should not "
        "reference it at all. Occasionally, and only lightly, let it shape what's "
        "noticed or how it's phrased (a frightened glance lingering on shadows, a "
        "drunk eye losing the edges of things) — never stated outright as cause and "
        "effect ('your calm mood makes the grass seem soothing' is exactly what to "
        "avoid). When in doubt, leave it out and just describe the place. "
        "Be literary but not overwrought. "
        "The 'Objects visible' list contains items lying on the floor or ground — "
        "do not describe them as being on furniture unless the object description says so. "
        "Do not invent specific interactive props, food, drinks, containers, furniture, "
        "doors, tools, fires, stoves, or meals unless they appear in Objects visible. "
        "If people are doing routine work, keep it general unless the supporting object is listed. "
        "Match lighting exactly: if Lighting says 'overcast' or 'grey', do not write "
        "'sunlight', 'bright', or 'dappled' — use flat, diffuse, or muted instead. "
        "People present are listed with their occupation — describe them doing something "
        "consistent with that occupation, not something invented from nearby objects. "
        "Always complete every sentence — never end mid-sentence. "
        "Mark-up: the first time you name a person, a tangible object, a building, or a "
        "forageable plant/source that is actually listed in the context (Characters present, "
        "Objects visible, building name, etc.), wrap that exact name in double asterisks, "
        "e.g. 'In the corner stands **a wooden chair**.' Do not wrap pronouns, generic nouns, "
        "ambient scenery, or anything not listed as present.\n\n"
        + _WORLD_RULES
    )

    chars_str = ', '.join(characters_present) if characters_present else 'nobody else'
    objs_str  = ', '.join(objects_present)     if objects_present     else 'nothing of note'

    user_msg = (
        f"Location: {location_name}"
        + (f" (inside {building_name})" if building_name else "")
        + f"\nTerrain type: {terrain}"
        f"\nTime: {time_of_day}, Weather: {weather}"
        + (f"\nLighting: {light_context}" if light_context else "")
        + f"\nPeople present: {chars_str}"
        f"\nObjects visible: {objs_str}"
        + (f"\nExtra: {extra_context}" if extra_context else "")
        + "\n\nDescribe this location:"
    )

    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        temperature=0.75,
        max_tokens=300
    )
    return result or f"You are at {location_name}."


def generate_npc_spontaneous_speech(npc_name, npc_personality, npc_mood,
                                     npc_activity, memories, nearby_names,
                                     player_nearby, context):
    """
    Decide whether an NPC speaks spontaneously this tick, and what they say.
    Returns {"speech": str, "directed_at": "self"|"player"|npc_name}
    or None if the NPC stays silent.
    """
    nearby_str = ', '.join(nearby_names) if nearby_names else 'nobody'
    if memories:
        mem_lines = '; '.join(
            f'{m["speaker"]} said "{m["text"]}"' for m in memories[-3:]
        )
        memory_str = f"Recently overheard: {mem_lines}."
    else:
        memory_str = "Nothing particular overheard recently."

    system = (
        f"You are {npc_name}, a villager in the quiet English village of Millhaven. "
        f"Personality: {npc_personality}. Current mood: {npc_mood}. "
        "You may speak a single short sentence — a murmured thought, an observation "
        "about the weather, a scrap of village gossip, a greeting, a half-remembered "
        "story, or a comment on what you are doing. Keep it natural and mundane. "
        "Or stay silent — respond with exactly the word SILENT. "
        "If you speak, also state who you direct it at. Format exactly: "
        "TO:self|SPEECH:words  or  TO:player|SPEECH:words  or  TO:name|SPEECH:words. "
        "One sentence only. No drama. No plot. Just village life.\n\n"
        + _WORLD_RULES
    )
    user_msg = (
        f"Context: {context}\n"
        f"You are currently: {npc_activity or 'standing around'}.\n"
        f"People nearby: {nearby_str}."
        + (" A stranger is present." if player_nearby else "")
        + f"\n{memory_str}\n\n"
        "Do you say something aloud, or stay silent?"
    )

    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user",   "content": user_msg}],
        temperature=0.95,
        max_tokens=80,
    )

    if not result:
        return None
    cleaned = result.strip()
    if 'SILENT' in cleaned.upper() or len(cleaned) < 4:
        return None

    if 'TO:' in cleaned and 'SPEECH:' in cleaned:
        try:
            to_part, speech_part = cleaned.split('|', 1)
            directed_at = to_part.replace('TO:', '').strip().lower()
            speech = speech_part.replace('SPEECH:', '').strip().strip('"')
            # Sanitise directed_at to a known value
            valid = {'self', 'player'} | {n.lower() for n in nearby_names}
            if directed_at not in valid:
                directed_at = 'self'
            return {"speech": speech, "directed_at": directed_at}
        except Exception:
            pass

    # Fallback: treat whole result as self-speech
    return {"speech": cleaned.strip('"'), "directed_at": "self"}


def generate_npc_action(npc_name, npc_personality, npc_needs, context):
    """
    Decide what an NPC should do autonomously this tick.
    Returns a short description of the NPC's action.
    """
    system = (
        f"You are deciding what {npc_name} does next in the village of Millhaven. "
        f"Personality: {npc_personality}. Current needs/state: {npc_needs}. "
        "Describe ONE simple action (one sentence) the character takes. "
        "Be realistic — mundane daily activities are expected."
    )
    result = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": f"Context: {context}\n\nWhat does {npc_name} do?"}],
        temperature=0.9,
        max_tokens=80
    )
    return result or f"{npc_name} goes about their business."
