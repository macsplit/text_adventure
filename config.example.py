"""
Copy this file to config.py and edit to suit your setup.
config.py is gitignored so your local settings (including any HF token) stay private.
"""
import os

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:14b"
DB_PATH = "millhaven.db"
GRID_WIDTH = 100
GRID_HEIGHT = 100
GAME_TITLE = "Millhaven"
PLAYER_START_X = 50
PLAYER_START_Y = 50
PLAYER_START_Z = 0
NPC_TICK_INTERVAL = 3  # NPCs act every N player turns
MAP_STYLE = "symbols"  # terminal mini-map style

LLM_BACKEND = "auto"  # "auto" | "ollama" | "embedded"

# --- Embedded model: Option 1 — point at a local .gguf file you already have ---
# Any GGUF downloaded via Ollama, LM Studio, or a direct download will work.
# When set, the automatic download below is skipped entirely.
EMBEDDED_MODEL_PATH = ""  # e.g. "/home/user/models/Qwen3-4B-Instruct-Q4_K_M.gguf"

# --- Embedded model: Option 2 — automatic download on first run ---
# Requires a free HuggingFace account and accepting the model licence.
# Ignored when EMBEDDED_MODEL_PATH points to an existing file.
EMBEDDED_MODEL_DIR = os.path.expanduser("~/.local/share/millhaven/models")
EMBEDDED_MODEL_FILENAME = "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
EMBEDDED_MODEL_URL = (
    "https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF"
    "/resolve/main/Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
)
# HuggingFace token — required if the download returns 401.
# Generate one at https://huggingface.co/settings/tokens
HF_TOKEN = ""
