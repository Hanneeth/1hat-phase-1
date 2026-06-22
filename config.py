from pathlib import Path

# Paths
PROJECT_ROOT: Path = Path(__file__).parent.resolve()
DATA_DIR: Path = PROJECT_ROOT / "data"
HBP_DIR: Path = DATA_DIR / "hbp"
STG_DIR: Path = DATA_DIR / "stg"
SCHEMES_DIR: Path = DATA_DIR / "schemes"
DUMMY_DIR: Path = DATA_DIR / "dummy"
INDEX_FILE: Path = HBP_DIR / "_index.json"
PMJAY_RULES_FILE: Path = SCHEMES_DIR / "pmjay.json"
QUERY_TAXONOMY_FILE: Path = DATA_DIR / "query_taxonomy.json"
DUMMY_BIS_FILE: Path = DUMMY_DIR / "dummy_bis.json"
DUMMY_HEM_FILE: Path = DUMMY_DIR / "dummy_hem.json"

# Phase 2 fuzzy search
TOP_N_CANDIDATES: int = 30
MIN_FUZZY_SCORE: int = 50        # 0-100 scale, rapidfuzz

# Phase 3 enhancement calculation
ENHANCEMENT_BATCH_PRIVATE: int = 2
ENHANCEMENT_BATCH_PUBLIC: int = 5
NE_STATES_AND_ISLANDS: list[str] = [
    "Assam", "Tripura", "Arunachal Pradesh", "Meghalaya",
    "Nagaland", "Mizoram", "Sikkim",
    "Andaman and Nicobar Islands", "Lakshadweep"
]
REQUIRE_STG_FOR_VALIDATION: bool = False  # False = warn; True = block when STG missing

# LLM (Phase 3 STG check only)
LLM_MODEL: str = "gemini-2.5-flash"
LLM_TIMEOUT_SECONDS: int = 30
QUERY_PREDICTOR_TIMEOUT_SECONDS = 90
LLM_MAX_RETRIES: int = 2

# Age thresholds
SENIOR_CITIZEN_AGE: int = 70
PAEDIATRIC_AGE_MAX: int = 14

# Wallet defaults
FAMILY_WALLET_DEFAULT_INR: int = 500000
VAY_VANDANA_WALLET_INR: int = 500000

# Logging
LOG_LEVEL: str = "INFO"
LOG_FORMAT: str = "[%(levelname)s][%(name)s] %(message)s"

# ---------------------------------------------------------------------------
# Phase 2 Search Mode
# ---------------------------------------------------------------------------
# Controls which candidate generation backend Phase 2 uses.
# "fuzzy"  — rapidfuzz token_set_ratio against _index.json aliases (default,
#             deterministic, zero LLM cost, weaker clinical understanding)
# "llm"    — Gemini reads full _index.json + clinical input and returns
#             procedure codes directly (better clinical reasoning, one LLM
#             call per run, non-deterministic)
# Switch by changing this one constant. No other code changes needed.
PHASE2_SEARCH_MODE: str = "llm"
