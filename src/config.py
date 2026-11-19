import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

MAX_HISTORY_TURNS = 6
MAX_REQUEST_BYTES = 65536
MAX_PENDING_EVALUATIONS = int(os.getenv("MAX_PENDING_EVALUATIONS", "3"))
DATASET_UPDATE_TIMEZONE = os.getenv("DATASET_UPDATE_TIMEZONE", "Asia/Kolkata")
ENABLE_DATASET_UPDATES = os.getenv("ENABLE_DATASET_UPDATES", "true").lower() in {"1", "true", "yes"}

# --- Configuration ---
COLLECTION_NAME = "prompt_firewall"
EMBEDDING_MODEL_NAME = "BAAI/bge-large-en-v1.5"
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
FORCE_REBUILD = os.getenv("FORCE_REBUILD", "false").lower() in {"1", "true", "yes"}
FIREWALL_HOST = os.getenv("FIREWALL_HOST", "0.0.0.0")
FIREWALL_PORT = int(os.getenv("FIREWALL_PORT", "8000")) 

HF_DATASETS = {
    "xTRam1/safe-guard-prompt-injection": None,
    "neuralchemy/Prompt-injection-dataset": None,
    "deepset/prompt-injections": None,
    "JailbreakBench/JBB-Behaviors": "behaviors", 
    "lmsys/toxic-chat": "toxicchat1123", 
    "PKU-Alignment/BeaverTails": None,
    "allenai/wildguardmix": "wildguardtrain" 
}
