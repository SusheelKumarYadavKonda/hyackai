"""Configuration. Reads .env, exposes per-adapter stub flags.

Every layer flips between stub and real independently, so a vendor that is slow
or down never blocks the loop. Stub-by-default: an unconfigured checkout runs
end to end with no credentials.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CORPUS_DIR = DATA_DIR / "corpus"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
STATE_DIR = REPO_ROOT / ".state"
INCIDENTS_FILE = DATA_DIR / "incidents.json"
LINEAGE_FILE = DATA_DIR / "lineage.json"
MUSCLE_FILE = STATE_DIR / "muscle_memory.json"
METRICS_FILE = STATE_DIR / "metrics.json"
REPORT_FILE = REPO_ROOT / "report.html"


def _load_dotenv() -> None:
    """Minimal .env reader. Avoids a dependency for six lines of parsing."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


# Stub flags. Default to stub so a fresh clone runs with no credentials.
USE_STUB_COGNEE = _flag("USE_STUB_COGNEE")
USE_STUB_HYDRA = _flag("USE_STUB_HYDRA")
USE_STUB_HOTDATA = _flag("USE_STUB_HOTDATA")
USE_STUB_ROCKETRIDE = _flag("USE_STUB_ROCKETRIDE")

# Credentials. Referenced by name only; never logged.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
HYDRA_DB_API_KEY = os.environ.get("HYDRA_DB_API_KEY", "")
HOTDATA_API_KEY = os.environ.get("HOTDATA_API_KEY", "")
HOTDATA_WORKSPACE_ID = os.environ.get("HOTDATA_WORKSPACE_ID", "")
ROCKETRIDE_URI = os.environ.get("ROCKETRIDE_URI", "https://cloud.rocketride.ai")
ROCKETRIDE_APIKEY = os.environ.get("ROCKETRIDE_APIKEY", "")

# Namespacing for the hosted memory layers.
HYDRA_DATABASE = os.environ.get("HYDRA_DATABASE", "compounding_oncall")
HYDRA_COLLECTION = os.environ.get("HYDRA_COLLECTION", "incidents")
COGNEE_DATASET = os.environ.get("COGNEE_DATASET", "oncall_corpus")

# Cognee Cloud. When both are set the adapter calls cognee.serve() and the hosted
# tenant performs LLM extraction, so no OPENAI_API_KEY is needed. Falling back to
# local/OSS mode is what requires an LLM provider of our own.
COGNEE_API_KEY = os.environ.get("COGNEE_API_KEY", "")
COGNEE_BASE_URL = os.environ.get("COGNEE_BASE_URL", "")

# Cost model for the metrics table. Approximate and clearly labelled as such.
COST_PER_1K_TOKENS = float(os.environ.get("COST_PER_1K_TOKENS", "0.0045"))

TABLES = ["orders", "shipments", "inventory"]


def ensure_dirs() -> None:
    for directory in (DATA_DIR, CORPUS_DIR, WAREHOUSE_DIR, STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def stub_summary() -> str:
    """One line naming which layers are live. Printed at demo start and in the
    report, because saying plainly what is stubbed is part of the pitch."""
    modes = {
        "cognee": "STUB" if USE_STUB_COGNEE else "REAL",
        "hydradb": "STUB" if USE_STUB_HYDRA else "REAL",
        "hotdata": "STUB" if USE_STUB_HOTDATA else "REAL",
        "rocketride": "STUB" if USE_STUB_ROCKETRIDE else "REAL",
        "muscle": "IN-HOUSE",
    }
    return "  ".join(f"{name}={mode}" for name, mode in modes.items())
