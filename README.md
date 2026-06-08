# Millhaven

A text adventure set in a quiet English village, driven by a local LLM via Ollama.

## Requirements

- Python 3.9+
- [Ollama](https://ollama.ai) running locally
- A model pulled in Ollama (default: `qwen2.5:14b`)

## Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Pull a model into Ollama (if not already done)
ollama pull qwen2.5:14b

# Generate the world (run once)
python init_town.py

# Play
python main.py
```

## Changing the model

Edit `config.py` and change `OLLAMA_MODEL`. Any model available in Ollama works.
Smaller models (e.g. `qwen2.5:7b`, `llama3.2:3b`) run faster but produce less
nuanced narrative.

## The world

**Millhaven** is a 100×100 grid (10,000 locations). The town occupies roughly
the central 60×60 area; the edges fade into wilderness, farmland and countryside.

- ~23 buildings: inn, bakery, smithy, church, town hall, surgery, school, shops, homes, two farms
- 24 characters with personalities, occupations, needs and home locations
- 200+ physical objects across the world
- Pseudo-2D: most locations are ground level (z=0); some buildings have upper
  floors (z=1) or cellars (z=-1)

## Commands

Type naturally — the LLM interprets your intent. Examples:

```
north / south / east / west / up / down
look around
examine the anvil
take the bread roll
say "Good morning"
ask James about rooms
give the coin to the beggar
buy a loaf
eat the apple
sleep
wait
inventory  (or just: i)
status
help
quit
```

## Emergent gameplay

There is no fixed plot, but needs create pressure:

- **Hunger** rises each turn — find food or buy it
- **Energy** falls — find somewhere to sleep
- Characters react to what you say and do
- NPCs move, have their own schedules and moods
- Objects persist; states change; things can be taken, used, given away

Goals might emerge: earn money working odd jobs, find accommodation at the inn,
uncover why Old Peter wanders the square, discover what the wanted poster means.
