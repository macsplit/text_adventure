import os

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5-coder:14b"
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
EMBEDDED_MODEL_DIR = os.path.expanduser("~/.local/share/millhaven/models")
EMBEDDED_MODEL_FILENAME = "Qwen3-4B-Instruct-Q4_K_M.gguf"
EMBEDDED_MODEL_URL = (
    "https://huggingface.co/bartowski/Qwen3-4B-Instruct-GGUF"
    "/resolve/main/Qwen3-4B-Instruct-Q4_K_M.gguf"
)
# HuggingFace access token — needed if the model repo requires authentication.
# Get one free at https://huggingface.co/settings/tokens
HF_TOKEN = ""
