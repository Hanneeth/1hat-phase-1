# IRIS — Per-File Prompts for Antigravity

**Usage:** For each file, copy the matching prompt into the Antigravity chat. Make sure `SYSTEM_DESIGN.md` exists in the project root — every prompt references it.

**Common preamble** (Antigravity should already have read SYSTEM_DESIGN.md, but reinforce in each prompt):

> Refer to `SYSTEM_DESIGN.md` in the project root for full project context, conventions, edge cases, and architecture. Follow the conventions there exactly. Do not deviate from the file/function structure unless I tell you to.

---

## File 1: `config.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `config.py` at the project root. This file holds ALL tunable constants used anywhere in the IRIS pipeline. No other file is allowed to hardcode these values — they import from here.

Required constants:

# Phase 2 fuzzy search
TOP_N_CANDIDATES: int = 30
MIN_FUZZY_SCORE: int = 60  # 0-100 scale from rapidfuzz

# Phase 3 enhancement
ENHANCEMENT_BATCH_PRIVATE: int = 2
ENHANCEMENT_BATCH_PUBLIC: int = 5
NE_STATES_AND_ISLANDS: list[str] = [
    "Assam", "Tripura", "Arunachal Pradesh", "Meghalaya",
    "Nagaland", "Mizoram", "Sikkim",
    "Andaman and Nicobar Islands", "Lakshadweep"
]

# Phase 3 STG behaviour
REQUIRE_STG_FOR_VALIDATION: bool = False  # if True, candidates without STG file are blocked; if False, warned

# LLM (Phase 3 STG check)
LLM_MODEL: str = "claude-sonnet-4-6"  # or whichever
LLM_TIMEOUT_SECONDS: int = 30
LLM_MAX_RETRIES: int = 2

# Paths — use pathlib.Path
from pathlib import Path
PROJECT_ROOT: Path = Path(__file__).parent
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

# Logging
LOG_LEVEL: str = "INFO"
LOG_FORMAT: str = "[%(levelname)s][%(name)s] %(message)s"

# Vay Vandana age threshold
SENIOR_CITIZEN_AGE: int = 70

# Paediatric implant age threshold
PAEDIATRIC_AGE_MAX: int = 14

# Wallet defaults
FAMILY_WALLET_DEFAULT_INR: int = 500000
VAY_VANDANA_WALLET_INR: int = 500000

Use plain module-level assignments. No class wrapper.
```

---

## File 2: `logger_setup.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `logger_setup.py` at the project root. Single function `setup_logging()` that configures Python's `logging` module using values from `config.py`.

Requirements:
- Use `config.LOG_LEVEL` and `config.LOG_FORMAT`
- Format must show level, module name, and message
- Configure root logger so all child loggers inherit
- Function should be idempotent (safe to call multiple times)
- Returns the root logger

This function is called once at the top of `main.py`. Every other module gets its logger via:
    import logging
    logger = logging.getLogger(__name__)

Do NOT create file handlers. Stdout only for now.
```

---

## File 3: `models.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read the "Data Flow Through Phases" section carefully.

Create `models.py` at the project root. Contains all dataclasses used across the pipeline EXCEPT IRISSession (which lives in session.py).

Use Python 3.11+ dataclasses. Use type hints. Use `field(default_factory=list)` for list defaults. No Pydantic. No Enums (use string literals).

Required dataclasses, in this order:

1. WalletBalance
   - family_balance_inr: int
   - vay_vandana_balance_inr: int | None
   - policy_year_start: str  # ISO date string

2. PastClaim
   - procedure_code: str
   - admission_date: str
   - package_amount_inr: int
   - status: str

3. PatientContext
   - patient_id: str
   - family_id: str
   - name: str
   - age: int
   - gender: str  # "M" | "F"
   - home_state: str
   - home_district: str
   - wallet: WalletBalance
   - past_claims: list[PastClaim] = field(default_factory=list)

4. HospitalContext
   - hospital_id: str
   - name: str
   - type: str  # "private" | "public"
   - city_tier: str  # "tier1" | "tier2" | "tier3"
   - state: str
   - district: str
   - is_aspirational_district: bool
   - accreditation: str  # "none" | "bronze" | "nabh_entry" | "nabh_full" | "nqas"
   - scheme: str  # "pmjay" | other
   - empanelled_specialties: list[str]

5. Investigation
   - type: str  # canonical key: "ecg", "blood_reports", etc.
   - result_summary: str | None
   - document_available: bool

6. DocumentInHand
   - key: str
   - label: str
   - available: bool

7. TreatingDoctor
   - name: str
   - registration_number: str
   - qualification: str
   - specialty_code: str

8. ClinicalInput
   - is_emergency: bool
   - is_medico_legal: bool
   - chief_complaints: str
   - duration_days: int
   - provisional_diagnosis: str
   - planned_procedure: str | None
   - vitals: dict
   - investigations: list[Investigation]
   - comorbidities: list[str]
   - non_clinical_documents_in_hand: list[DocumentInHand]
   - treating_doctor: TreatingDoctor
   - notes: str | None

9. CandidatePackage
   - procedure_code: str
   - package_code: str
   - specialty_code: str
   - specialty: str
   - package_name: str
   - procedure_name: str
   - billing_unit: str
   - reserved_public_only: bool
   - procedure_label: str  # "regular" | "add_on" | "standalone" | "follow_up"
   - auto_approved: str  # "none" | "full" | "day1_only"
   - day_care: bool
   - base_rate_inr: int | None
   - match_score: float

10. StratificationResult
    - determinable: bool
    - selected_stratum: str | None
    - note: str | None  # populated when not determinable

11. ImplantResult
    - required: bool
    - name: str | None
    - cost_inr: int | None
    - age_appropriate: bool
    - gender_appropriate: bool
    - quantity: int | None

12. ValidatedPackage
    - procedure_code: str
    - package_code: str
    - specialty_code: str
    - package_name: str
    - procedure_name: str
    - billing_type: str  # "surgical" | "fixed_medical" | "per_day" | "day_care"
    - billing_unit: str
    - procedure_label: str
    - auto_approved: str
    - enhancement_applicable: bool
    - enhancement_requests_needed: int | None
    - reserved_public_only: bool
    - base_rate_inr: int | None
    - stratification: StratificationResult
    - implant: ImplantResult
    - special_conditions_popup: bool
    - special_conditions_rule: bool
    - stg_eligible: bool
    - stg_missing_criteria: list[str] = field(default_factory=list)
    - is_addon_to: list[str] | None
    - addon_type: str | None
    - match_score: float
    - flags: list[str] = field(default_factory=list)  # per-package warnings as plain strings

13. FinalPackage
    - validated: ValidatedPackage
    - role: str  # "primary" | "secondary" | "tertiary" | "addon" | "standalone"
    - deduction_factor: float
    - pre_auth_group: int

14. DocumentItem
    - key: str
    - label: str
    - package_code: str | None  # None = universal requirement
    - available: bool
    - criticality: str  # "hard_block" | "ppd_query_risk"

15. Flag
    - code: str  # uppercase snake_case, e.g. "PUB_RESERVED_BLOCK"
    - message: str
    - severity: str  # "info" | "warning" | "block"

16. EnhancementPlan
    - procedure_code: str
    - estimated_requests: int
    - batch_size_used: int
    - los_indicative_days: int
    - caveat: str  # always set to remind LoS is indicative

17. IRISOutput
    - readiness_status: str  # "READY" | "READY_WITH_WARNINGS" | "CONDITIONAL" | "BLOCKED"
    - selected_packages: list[FinalPackage] = field(default_factory=list)
    - blocked_candidates: list[dict] = field(default_factory=list)  # {procedure_code, reason, message}
    - preauth_docs_required: list[DocumentItem] = field(default_factory=list)
    - preauth_docs_missing: list[DocumentItem] = field(default_factory=list)
    - enhancement_plan: list[EnhancementPlan] = field(default_factory=list)
    - copayment_required: bool = False
    - copayment_gap_inr: int | None = None
    - flags: list[Flag] = field(default_factory=list)
    - stg_coverage: dict = field(default_factory=lambda: {"validated": 0, "stg_missing": 0})
    - errors: list[str] = field(default_factory=list)

Add a docstring to each dataclass explaining what it represents in the pipeline.
```

---

## File 4: `session.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "The Session Object — Pipeline Spine" section.

Create `session.py` at the project root. Single dataclass: IRISSession.

This is the pipeline spine — every phase reads and writes to this object.

Imports from models.py: PatientContext, HospitalContext, ClinicalInput, CandidatePackage, ValidatedPackage, FinalPackage, DocumentItem, Flag, EnhancementPlan.

Required fields:

# Raw input (set at session creation)
input_data: dict
clinical: ClinicalInput

# Populated by Phase 0
patient: PatientContext | None = None
hospital: HospitalContext | None = None
patient_eligible: bool = False
hospital_empanelled: bool = False
mlc_required: bool = False

# Populated by Phase 1
is_emergency: bool = False
er_package_code: str | None = None
needs_specialty_package: bool = True

# Populated by Phase 2
candidate_packages: list[CandidatePackage] = field(default_factory=list)

# Populated by Phase 3
validated_packages: list[ValidatedPackage] = field(default_factory=list)
phase3_blocked: list[dict] = field(default_factory=list)  # {procedure_code, reason, message}

# Populated by Phase 4
final_package_set: list[FinalPackage] = field(default_factory=list)

# Populated by Phase 5
wallet_sufficient: bool = True
copayment_required: bool = False
copayment_gap_inr: int | None = None
estimated_total_inr: int = 0

# Populated by Phase 6-8
exclusion_flags_added: bool = False
comorbidity_notes: list[str] = field(default_factory=list)

# Populated by Phase 9
preauth_docs_required: list[DocumentItem] = field(default_factory=list)
preauth_docs_missing: list[DocumentItem] = field(default_factory=list)

# Accumulated by all phases
flags: list[Flag] = field(default_factory=list)
errors: list[str] = field(default_factory=list)

Add a module-level docstring with the following text:

"""
IRISSession — the pipeline spine.

Convention for flags vs errors:

session.flags : list[Flag]
    Business outcomes the MEDCO needs to see. Examples: package blocked
    for public reservation, stratification undeterminable, wallet
    insufficient. Each Flag has code, message, severity (info/warning/block).

session.errors : list[str]
    Technical failures the developer needs to fix. Examples: STG file
    parse error, LLM API timeout, KeyError in clinical input.
    Plain string messages with debug context.

The orchestrator does NOT stop on errors. It only stops when a flag
with severity='block' is set, or after all phases complete normally.
"""

Add helper method:
    def has_block_flag(self) -> bool:
        return any(f.severity == "block" for f in self.flags)

Add helper method:
    def add_flag(self, code: str, message: str, severity: str) -> None:
        from models import Flag
        self.flags.append(Flag(code=code, message=message, severity=severity))
        # also log it via logger
```

---

## File 5: `input_validator.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `input_validator.py` at the project root. Single function:

def validate_input(raw_json: dict) -> tuple[bool, list[str]]:
    '''
    Validates the input JSON structure.
    
    Returns:
        (True, []) if valid
        (False, [error_messages]) if invalid
    
    For now, this is a STUB. Always returns (True, []).
    Full validation will be implemented later.
    '''
    return (True, [])

Include the stub with TODO comments listing what real validation will check:
- Required top-level keys: patient, hospital, clinical
- clinical.chief_complaints not empty
- clinical.provisional_diagnosis not empty
- clinical.investigations is a list (can be empty)
- patient.patient_id exists
- hospital.hospital_id exists
- Age is int between 0 and 120
- Gender is "M" or "F"
```

---

## File 6: `kb/__init__.py`

**Prompt:**

```
Create empty file at `kb/__init__.py`. Just one line:

# IRIS knowledge base layer
```

---

## File 7: `kb/loader.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "Knowledge Base Structure" section.

Create `kb/loader.py`. Pure I/O — loads JSON files from disk. All functions use @lru_cache so each file is read at most once per run.

Imports from config: INDEX_FILE, HBP_DIR, STG_DIR, PMJAY_RULES_FILE, QUERY_TAXONOMY_FILE.

Use pathlib.Path and json.loads(path.read_text(encoding="utf-8")). Use logger.

Required functions:

@lru_cache(maxsize=1)
def load_index() -> list[dict]:
    '''Loads data/hbp/_index.json. Returns list of thin index rows.'''

@lru_cache(maxsize=32)
def load_specialty_shard(specialty_code: str) -> dict:
    '''Loads data/hbp/<specialty_code>.json. Returns full shard.
    
    The specialty_code is the lowercase shard filename WITHOUT extension.
    Examples: "general_surgery", "burnsmanagement", "cardiology"
    
    Note: the specialty_code from KB-2 records is the 2-letter code like "SG", "MC".
    A separate mapping is needed. For now, accept the filename directly.
    Raises FileNotFoundError if shard doesn't exist.'''

@lru_cache(maxsize=500)
def load_stg(procedure_code: str) -> dict | None:
    '''Loads data/stg/<procedure_code>.json.
    Returns None if file doesn't exist (STG not built yet — common case).
    Returns dict if found.'''

@lru_cache(maxsize=1)
def load_pmjay_rules() -> dict:
    '''Loads data/schemes/pmjay.json. Returns the scheme-wide rules dict.'''

@lru_cache(maxsize=1)
def load_query_taxonomy() -> dict:
    '''Loads data/query_taxonomy.json. Returns query + rejection reasons.'''

def get_procedure_from_shard(procedure_code: str, shard: dict) -> dict | None:
    '''Helper: given a loaded shard, find the procedure dict by code.
    Searches shard["packages"][*]["procedures"][*] where procedure["procedure_code"] == procedure_code.
    Returns the procedure dict or None.'''

def get_package_from_shard(package_code: str, shard: dict) -> dict | None:
    '''Helper: given a loaded shard, find the package dict by package_code.
    Returns the package dict (containing procedures[]) or None.'''

Log at INFO when each file is loaded for the first time.
Catch FileNotFoundError on load_stg and return None — DO NOT raise.
Catch JSONDecodeError everywhere and log ERROR, then re-raise (these are real bugs).
```

---

## File 8: `stubs/__init__.py`

**Prompt:**

```
Create empty file at `stubs/__init__.py`. Just one line:

# IRIS data source stubs — replaced by real APIs later
```

---

## File 9: `stubs/bis_stub.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `stubs/bis_stub.py`. Stubs that simulate the real BIS (Beneficiary Identification System) API.

Imports from config: DUMMY_BIS_FILE.
Imports from models: PatientContext, WalletBalance, PastClaim.

Required functions:

def verify_bis(patient_id: str) -> PatientContext | None:
    '''Loads dummy_bis.json, finds patient by patient_id.
    Returns PatientContext if found, None if not.
    
    The dummy_bis.json structure is:
    {
        "PMJAY-TN-001": {
            "patient_id": "...",
            "family_id": "...",
            "name": "...",
            "age": 45,
            "gender": "M",
            "home_state": "...",
            "home_district": "...",
            "wallet": {
                "family_balance_inr": 350000,
                "vay_vandana_balance_inr": null,
                "policy_year_start": "2025-04-01"
            },
            "past_claims": [
                {"procedure_code": "...", "admission_date": "...", "package_amount_inr": ..., "status": "..."}
            ]
        }
    }
    
    NOTE: Will be replaced by real BIS API call. Do not change the return type.'''

def get_wallet_balance(family_id: str) -> WalletBalance | None:
    '''Get just the wallet for a family. For now reads from dummy_bis.json.
    Returns None if family_id not found in any patient record.
    
    NOTE: Real BIS API will have a separate endpoint for wallet. Same return type.'''

Use the logger. Log INFO on successful verification, WARNING if patient not found.
```

---

## File 10: `stubs/hem_stub.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `stubs/hem_stub.py`. Stubs the real HEM (Hospital Empanelment Module) API.

Imports from config: DUMMY_HEM_FILE.
Imports from models: HospitalContext.

Required function:

def check_empanelment(hospital_id: str) -> HospitalContext:
    '''Loads dummy_hem.json, finds hospital by hospital_id.
    
    ALWAYS returns a HospitalContext (never None) — for MVP we assume
    the hospital is always empanelled. If the hospital_id is not in
    dummy_hem.json, return a default HospitalContext using fallback values:
    
        type="private", city_tier="tier1", scheme="pmjay",
        accreditation="nabh_full", is_aspirational_district=False,
        state="Tamil Nadu", district="Chennai",
        empanelled_specialties=["SG","MC","SB","MG","SO","SU","BM","SN","MR","SC","MO"]
    
    Log WARNING when fallback is used.
    
    NOTE: Real HEM API will return None if hospital not empanelled. The
    caller (Phase 0) will need to handle that. For MVP this never happens.'''

Use the logger.
```

---

## File 11: `phases/__init__.py`

**Prompt:**

```
Create empty file at `phases/__init__.py`. Just one line:

# IRIS pipeline phases
```

---

## File 12: `phases/phase0_preflight.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "Critical Rules" #16 (CMCHIS fast-fail).

Create `phases/phase0_preflight.py`. Phase 0 — pre-flight checks.

Imports from session: IRISSession.
Imports from stubs.bis_stub: verify_bis.
Imports from stubs.hem_stub: check_empanelment.

Required function:

def run_phase0(session: IRISSession) -> IRISSession:
    '''Phase 0 — Pre-flight gates.
    
    Steps:
    1. Extract patient_id from session.input_data["patient"]["patient_id"]
    2. Call verify_bis(patient_id) → set session.patient
       If None → add BLOCK flag PATIENT_NOT_IN_BIS, return session
    3. Set session.patient_eligible = True
    4. Extract hospital_id from session.input_data["hospital"]["hospital_id"]
    5. Call check_empanelment(hospital_id) → set session.hospital
    6. Set session.hospital_empanelled = True
    7. Check session.hospital.scheme — if not "pmjay":
       add BLOCK flag SCHEME_NOT_SUPPORTED with message naming the scheme
       return session
    8. Set session.mlc_required = session.clinical.is_medico_legal
    9. Return session
    
    Use session.add_flag() helper. Use logger at INFO level for each step.
    Wrap try/except around steps 2 and 5 — on exception, append to session.errors
    and add a BLOCK flag with code PREFLIGHT_FAILED.'''
```

---

## File 13: `phases/phase1_emergency.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `phases/phase1_emergency.py`. Phase 1 — emergency routing. STUBBED for MVP.

Imports from session: IRISSession.

Required function:

def run_phase1(session: IRISSession) -> IRISSession:
    '''Phase 1 — Emergency routing. STUBBED.
    
    For MVP, this phase always assumes NON-emergency planned admission.
    
    Steps:
    1. Set session.is_emergency = False
    2. Set session.er_package_code = None
    3. Set session.needs_specialty_package = True
    4. Add INFO flag EMERGENCY_PHASE_STUBBED with message
       "Emergency routing not implemented in MVP — assuming planned admission"
    5. Return session
    
    TODO comments listing what real implementation will do:
    - Determine emergency based on vitals (BP, GCS, SpO2) + chief_complaints
    - Select ER package (ER001A/ER002A/ER002B/ER003A) based on severity
    - If hospitalisation expected >12h, set needs_specialty_package=True and
      return two pre-auth recommendations
    - Set mlc_required based on accident/assault indicators in chief_complaints'''
```

---

## File 14: `kb/searcher.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "Critical Rules" #3 (cross-specialty dedup).

Create `kb/searcher.py`. Fuzzy search over _index.json.

Use `rapidfuzz` library. Add `rapidfuzz>=3.0` to requirements.

Imports from config: TOP_N_CANDIDATES, MIN_FUZZY_SCORE.
Imports from kb.loader: load_index.
Imports from models: ClinicalInput, CandidatePackage.

Required public function:

def search_candidates(
    clinical: ClinicalInput,
    empanelled_specialties: list[str],
    hospital_is_public: bool
) -> list[CandidatePackage]:
    '''Fuzzy search _index.json against clinical input.
    
    Steps:
    1. Build search string by concatenating:
       chief_complaints + " " + provisional_diagnosis + " " + (planned_procedure or "")
    2. Load index via load_index()
    3. Pre-filter index entries:
       - Only entries where row["specialty_code"] in empanelled_specialties
       - If hospital is private (hospital_is_public=False): exclude entries
         where reserved_public_only=True (hard pre-filter)
    4. For each surviving entry, compute match_score using rapidfuzz.fuzz.token_set_ratio:
       - Score against each alias in row["aliases"], take max
       - Also score against the package_name and procedure_name, take max
       - Final match_score = best of (best_alias_score, best_name_score)
    5. Filter: keep only entries where match_score >= MIN_FUZZY_SCORE
    6. Sort by match_score descending
    7. Take top TOP_N_CANDIDATES entries
    8. Deduplicate by package_code: if multiple entries have same package_code
       (cross-specialty duplicates), keep the one with highest match_score
    9. Convert each surviving row to CandidatePackage object
    10. Return list

    Log INFO with count of candidates after each filter step.
    Log WARNING if zero candidates survive.'''

Private helper:

def _entity_extract(clinical: ClinicalInput) -> str:
    '''STUB for now. Returns raw concatenated text.
    Later this will be replaced by an LLM call that extracts structured entities.
    Same function signature, just better implementation.'''
    parts = [clinical.chief_complaints, clinical.provisional_diagnosis]
    if clinical.planned_procedure:
        parts.append(clinical.planned_procedure)
    return " ".join(parts)
```

---

## File 15: `phases/phase2_candidates.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `phases/phase2_candidates.py`. Phase 2 — candidate generation.

Imports from session: IRISSession.
Imports from kb.searcher: search_candidates.

Required function:

def run_phase2(session: IRISSession) -> IRISSession:
    '''Phase 2 — Candidate generation via fuzzy search.
    
    Steps:
    1. If session.is_emergency and session.er_package_code is set,
       skip fuzzy search and add the ER package directly as a single
       candidate. (Currently impossible since Phase 1 is stubbed but
       structure for later.)
    2. Otherwise call:
       candidates = search_candidates(
           session.clinical,
           session.hospital.empanelled_specialties,
           hospital_is_public=(session.hospital.type == "public")
       )
    3. Set session.candidate_packages = candidates
    4. Add INFO flag CANDIDATES_GENERATED with count
    5. If len(candidates) == 0:
       Add WARNING flag NO_CANDIDATES_FOUND
       (Do NOT block here — Phase 3 will handle USP routing if needed)
    6. Return session
    
    Wrap in try/except — on exception, append to session.errors and
    add BLOCK flag CANDIDATE_GENERATION_FAILED.'''
```

---

## File 16: `llm/__init__.py`

**Prompt:**

```
Create empty file at `llm/__init__.py`. Just one line:

# IRIS LLM-powered checks
```

---

## File 17: `llm/stg_checker.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "LLM Usage Policy" section.

Create `llm/stg_checker.py`. The single LLM call in the IRIS MVP — STG eligibility check.

Imports from config: LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES.
Imports from models: ClinicalInput.

External: anthropic SDK (or openai — make this configurable later).
Add `anthropic` to requirements.

Required public function:

def check_stg_eligibility(
    procedure_code: str,
    stg: dict,
    clinical: ClinicalInput
) -> dict:
    '''Calls LLM to check if patient meets STG eligibility for this procedure.
    
    Inputs:
        procedure_code — for logging/context
        stg — full STG dict loaded from data/stg/<code>.json
        clinical — patient clinical input
    
    Returns dict:
        {
            "eligible": bool,
            "missing_criteria": [str, ...],  # list of unmet criteria
            "reasoning": str,                # one-paragraph explanation
            "confidence": str                # "high" | "medium" | "low"
        }
    
    Steps:
    1. Build a focused prompt using ONLY the relevant parts of the STG:
       - stg["clinical_indications"]
       - stg["clinical_thresholds"]
       - stg["alos"] if present
       - stg["min_doctor_qualification"] if present
       Do NOT send the full STG (too much noise).
    2. Build clinical summary from:
       - clinical.provisional_diagnosis
       - clinical.chief_complaints
       - clinical.duration_days
       - clinical.investigations (just type + result_summary)
       - clinical.comorbidities
       - clinical.vitals (only non-null values)
    3. Call LLM with structured prompt asking for JSON response.
    4. Parse JSON response, validate structure, return.
    5. On any failure (timeout, malformed JSON, API error):
       - Retry up to LLM_MAX_RETRIES
       - If all retries fail, return:
         {"eligible": True, "missing_criteria": [],
          "reasoning": "LLM check failed — passed by default",
          "confidence": "low"}
       - Caller (Phase 3) will handle adding a flag.
    
    Use logger. Log the LLM raw response at DEBUG.'''

The prompt template (build inside the function):

SYSTEM_PROMPT = """You are a clinical eligibility validator for PM-JAY package selection.

You are given:
1. A Standard Treatment Guideline (STG) with clinical indications and thresholds for a specific procedure
2. The patient's clinical presentation

Your task: determine if the patient's clinical condition meets the STG criteria for this procedure.

You MUST respond with valid JSON only. No prose outside the JSON. Schema:
{
  "eligible": true/false,
  "missing_criteria": ["criterion 1 not met because...", ...],
  "reasoning": "one paragraph explanation",
  "confidence": "high"/"medium"/"low"
}

Be strict but practical. Match medical concepts even if exact words differ
(e.g. "BCVA 6/60" satisfies threshold "BCVA <= 6/9" because 6/60 is worse vision).
If a required investigation result is missing entirely, list it under missing_criteria
and set confidence to "low".
"""

USER_PROMPT_TEMPLATE = """STG criteria for procedure {procedure_code}:

Clinical indications:
{indications}

Clinical thresholds:
{thresholds}

Patient presentation:
- Provisional diagnosis: {diagnosis}
- Chief complaints: {complaints}
- Duration: {duration} days
- Vitals: {vitals}
- Investigations available: {investigations}
- Comorbidities: {comorbidities}

Evaluate eligibility. Respond with JSON only."""

Pass the model name from config. Use temperature=0 for determinism.
```

---

## File 18: `phases/phase3_validator.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read all 16 Critical Rules. Read "Data Flow Through Phases".

This is THE most complex file in the project. Build it carefully.

Create `phases/phase3_validator.py`. Phase 3 — per-package validation.

Imports from session: IRISSession.
Imports from models: CandidatePackage, ValidatedPackage, StratificationResult, ImplantResult.
Imports from kb.loader: load_specialty_shard, get_procedure_from_shard, load_stg.
Imports from llm.stg_checker: check_stg_eligibility.
Imports from config: ENHANCEMENT_BATCH_PRIVATE, ENHANCEMENT_BATCH_PUBLIC, NE_STATES_AND_ISLANDS, PAEDIATRIC_AGE_MAX, REQUIRE_STG_FOR_VALIDATION.

Public function:

def run_phase3(session: IRISSession) -> IRISSession:
    '''Phase 3 — per-candidate validation loop.
    
    For each candidate in session.candidate_packages, run all checks.
    Survivors → session.validated_packages.
    Blocked → session.phase3_blocked with reason.
    
    Returns session.'''

Inside the loop, for each candidate, follow this exact sequence:

1. Load full procedure record:
   - Map specialty_code (2-letter) to shard filename. For now, use this mapping
     stored as a module-level CONSTANT in this file (will move to config later):
       SPECIALTY_CODE_TO_SHARD = {
           "BM": "burnsmanagement",
           "MC": "cardiology",
           "SV": "ctvs",
           "ER": "emergency_room",
           "MG": "general_medicine",
           "SG": "general_surgery",
           "ID": "infectious_diseases",
           "IR": "interventional_radiology",
           "MO": "medical_oncology",
           "MM": "mental_disorders",
           "MN": "neonatal_care",
           "SN": "neurosurgery",
           "SO": "obstetrics_gynaecology",
           "SE": "ophthalmology",
           "SM": "oral_maxillofacial",
           "OT": "organ_transplant",
           "SB": "orthopaedics",
           "SL": "ent",
           "SS": "paediatric_surgery",
           "SP": "plastic_reconstructive",
           "ST": "polytrauma",
           "MR": "radiation_oncology",
           "SC": "surgical_oncology",
           "SU": "urology",
           "PM": "palliative_medicine",
           "HM": "high_end_medicine",
           "HD": "high_end_diagnostics",
           "HP": "high_end_procedures",
           "IN": "interventional_neuroradiology",
           "PHCnCHC": "primary_care",
           "HRP": "hrp",
           "US": "unspecified_surgical",
       }
   - Try: shard = load_specialty_shard(SPECIALTY_CODE_TO_SHARD[candidate.specialty_code])
   - procedure = get_procedure_from_shard(candidate.procedure_code, shard)
   - If procedure is None → block with reason "Procedure not found in shard"

2. Check public reservation:
   - If procedure["reserved_public_only"] is True AND session.hospital.type == "private":
     block with reason PUB_RESERVED_BLOCK

3. Classify billing type:
   - "surgical" if procedure has Medical_or_Surgical == "Surgical" or billing_unit=="one_time" and not day_care
   - "day_care" if day_care == True
   - "per_day" if billing_unit == "per_day"
   - "fixed_medical" otherwise
   Helper: _classify_billing_type(procedure) -> str

4. Check STG eligibility:
   - stg = load_stg(candidate.procedure_code)
   - If stg is None:
     - stg_eligible = True
     - stg_missing_criteria = []
     - Add a per-package warning flag "STG_MISSING" to candidate
     - If REQUIRE_STG_FOR_VALIDATION is True: block with reason STG_REQUIRED
   - If stg is not None:
     - Call check_stg_eligibility(procedure_code, stg, session.clinical) → result dict
     - stg_eligible = result["eligible"]
     - stg_missing_criteria = result["missing_criteria"]
     - If not eligible: block with reason STG_NOT_ELIGIBLE + reasoning

5. Determine stratification:
   - If procedure["stratification_required"] is False:
     stratification = StratificationResult(determinable=True, selected_stratum=None, note=None)
   - If True:
     - For MVP, do simple keyword matching on procedure["stratification_criteria"]
       against clinical.chief_complaints + clinical.notes + clinical.provisional_diagnosis
     - If a clear match found → set selected_stratum
     - If no clear match → StratificationResult(determinable=False, selected_stratum=None,
                          note="Stratification undeterminable — physician input required")
     - Add per-package warning flag if undeterminable
   Helper: _determine_stratification(procedure, stg, clinical) -> StratificationResult

6. Check implant:
   - If procedure["implant"] is None:
     ImplantResult(required=False, ...)
   - Else:
     - For MVP, just check age-appropriateness:
       - Age <= PAEDIATRIC_AGE_MAX → assume paediatric implant
       - Age > PAEDIATRIC_AGE_MAX → assume adult implant
     - Set age_appropriate = True (don't block; just track)
     - Set gender_appropriate = True (placeholder)
     - Set quantity = 1 (MEDCO will adjust in TMS)
     - If age boundary case, add warning flag IMPLANT_AGE_BOUNDARY
   Helper: _check_implant(procedure, patient) -> ImplantResult

7. Check special conditions:
   - popup = procedure["special_conditions_popup"] (bool)
   - rule = procedure["special_conditions_rule"] (bool)
   - If rule is True:
     - Scan session.patient.past_claims for same procedure_code in same policy year
     - If found, add WARNING flag SPECIAL_CONDITIONS_RULE_TRIGGERED with details
   - These are flags only — do NOT block

8. Plan enhancement:
   - If procedure["enhancement_applicable"] is False:
     enhancement_requests_needed = None
   - Else:
     - los = procedure.get("los_indicative") or 1
     - is_ne = session.hospital.state in NE_STATES_AND_ISLANDS
     - if session.hospital.type == "public" or is_ne:
         batch = ENHANCEMENT_BATCH_PUBLIC
       else:
         batch = ENHANCEMENT_BATCH_PRIVATE
     - enhancement_requests_needed = ceil((los - 1) / batch)
   Helper: _plan_enhancement(procedure, hospital) -> int | None

9. Build ValidatedPackage:
   - Pull all fields from candidate and procedure
   - Pull is_addon_to and addon_type from procedure (may be null)
   - Set match_score = candidate.match_score
   - flags = list of per-package warnings collected above

10. Append to session.validated_packages

If candidate is blocked at any step:
- Append to session.phase3_blocked as dict {procedure_code, reason_code, message}
- Continue to next candidate

After loop completes:
- Log INFO with count of validated vs blocked
- If len(validated_packages) == 0:
  add WARNING flag NO_VALIDATED_PACKAGES with message recommending USP path
  (Phase 4 will handle skipping to Phase 9)

Helper functions to define inside this file (private):
- _classify_billing_type(procedure) -> str
- _determine_stratification(procedure, stg, clinical) -> StratificationResult
- _check_implant(procedure, patient) -> ImplantResult
- _plan_enhancement(procedure, hospital) -> int | None

Wrap the per-candidate loop body in try/except. On exception per candidate,
log ERROR, append to session.errors, add to phase3_blocked with reason
"INTERNAL_ERROR", continue to next candidate. Never let one candidate crash
the whole phase.
```

---

## File 19: `phases/phase4_multipackage.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "Critical Rules" #9, #10, #11, #12.

Create `phases/phase4_multipackage.py`. Phase 4 — multi-package resolution.

Imports from session: IRISSession.
Imports from models: ValidatedPackage, FinalPackage.

Public function:

def run_phase4(session: IRISSession) -> IRISSession:
    '''Phase 4 — multi-package resolution.
    
    If session.validated_packages is empty, skip entirely.
    
    Otherwise:
    1. Classify into buckets by billing_type
    2. Apply combination rules — drop incompatible combos with flags
    3. Isolate standalones into separate pre_auth_group
    4. Attach add-ons — drop if parent missing
    5. Assign deduction factors (100-50-25 for surgicals, sorted by base_rate desc)
       Note: this is APPROXIMATE — real rule uses final price post-multipliers.
       Add a flag DEDUCTION_APPROXIMATE explaining this.
    6. Populate session.final_package_set with FinalPackage objects
    
    Returns session.'''

Helper functions (private):

def _classify_buckets(packages: list[ValidatedPackage]) -> dict:
    '''Returns dict with keys: "surgical", "fixed_medical", "per_day", "day_care".
    Each value is a list of ValidatedPackage.'''

def _check_combination_rules(buckets: dict, session: IRISSession) -> dict:
    '''Apply Phase 4.2 rules:
    - surgical + per_day → drop per_day, flag SURGICAL_PERDAY_BLOCKED
    - per_day + per_day → keep first only, flag PERDAY_MULTIPLE_BLOCKED
    - surgical + surgical → allowed (100-50-25 handled later)
    - surgical + fixed_medical → allowed
    - day_care + surgical → day_care behaves as surgical for combination
    
    Returns modified buckets dict. Adds flags to session.'''

def _isolate_standalones(buckets: dict, session: IRISSession) -> tuple[list, list]:
    '''Find any package with procedure_label == "standalone".
    Returns (standalone_list, rest_list).
    Standalones go to pre_auth_group=2, rest goes to pre_auth_group=1.
    
    If standalones exist alongside others, add flag STANDALONE_SPLIT.'''

def _attach_addons(packages: list[ValidatedPackage], session: IRISSession) -> list[ValidatedPackage]:
    '''For each package where procedure_label == "add_on":
    - Get is_addon_to (list of parent procedure_codes)
    - Check if any parent is in the package list
    - If yes, keep
    - If no, drop and add flag ADDON_PARENT_MISSING with procedure_code
    
    Also handle diagnostic add-ons (HD* procedures):
    - Only allowed when primary is per_day medical
    - If primary is surgical, drop with flag DIAGNOSTIC_ADDON_BLOCKED
    
    Returns filtered list.'''

def _assign_deduction_factors(packages: list[ValidatedPackage], pre_auth_group: int) -> list[FinalPackage]:
    '''Assign roles and deduction factors.
    
    Separate surgicals from others:
    - Surgical packages: sort by base_rate_inr descending.
      First → role="primary", deduction_factor=1.0
      Second → role="secondary", deduction_factor=0.5
      Third onwards → role="tertiary", deduction_factor=0.25
    - Fixed medical packages: role="primary" (multiple allowed), factor=1.0
    - Per-day packages: role="primary", factor=1.0
    - Day-care: treated as surgical for sort, but role still assigned same way
    - Add-ons: role="addon", factor=1.0
    - Standalone: role="standalone", factor=1.0
    
    Return list of FinalPackage with pre_auth_group set.'''

Wrap each helper call in try/except — log to session.errors, continue.
```

---

## File 20: `phases/phase5_financial.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "Critical Rules" #8 (Vay Vandana ambiguity).

Create `phases/phase5_financial.py`. Phase 5 — financial check. SIMPLIFIED for MVP.

Imports from session: IRISSession.
Imports from config: SENIOR_CITIZEN_AGE.

Required function:

def run_phase5(session: IRISSession) -> IRISSession:
    '''Phase 5 — wallet sufficiency check.
    
    Simplified for MVP — uses base_rate_inr * deduction_factor (no multipliers).
    
    Steps:
    1. If session.final_package_set is empty → set wallet_sufficient=True, return
    2. Sum estimated cost:
       total = sum(pkg.validated.base_rate_inr * pkg.deduction_factor
                   for pkg in session.final_package_set
                   if pkg.validated.base_rate_inr is not None)
    3. Set session.estimated_total_inr = total
    4. Get available wallet:
       family_balance = session.patient.wallet.family_balance_inr
       vay_vandana = session.patient.wallet.vay_vandana_balance_inr or 0
    5. If session.patient.age >= SENIOR_CITIZEN_AGE and vay_vandana > 0:
       - Add INFO flag VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS with message:
         "Patient has dual wallet (family ₹X + Vay Vandana ₹Y).
          NHA does not specify debit order — verify with SHA before submission."
       - available = family_balance + vay_vandana
    6. else:
       - available = family_balance
    7. If total <= available:
       wallet_sufficient = True
    8. else:
       wallet_sufficient = False
       copayment_required = True
       copayment_gap_inr = total - available
       Add WARNING flag WALLET_INSUFFICIENT with gap amount
    9. Add INFO flag FINANCIAL_ESTIMATE_APPROXIMATE with message
       "Estimate uses base rates only — final rates include tier/accreditation/geo multipliers"
    10. Return session
    
    Add TODO: real pricing requires Tier × Level × Accreditation × Geo multipliers
    + stratification rate + implant cost flat — implement in Phase 2 of project.'''
```

---

## File 21: `phases/phase6_exclusion.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `phases/phase6_exclusion.py`. Phase 6 — exclusion verification.

Imports from session: IRISSession.
Imports from kb.loader: load_pmjay_rules.

Required function:

def run_phase6(session: IRISSession) -> IRISSession:
    '''Phase 6 — exclusion check.
    
    For MVP, use simple keyword matching. The 8 exclusion categories from
    HBP Guidelines 2026 Annexure 6:
    
    1. OPD-only conditions
    2. Dental (exception: trauma/injury requiring bone treatment)
    3. Infertility/ART (exception: if listed in HBP)
    4. Vaccination/immunisation
    5. Cosmetic/aesthetic (exception: trauma deformity, congenital functional impairment)
    6. Circumcision under 2 years (exception: disease/accident)
    7. Persistent Vegetative State
    8. Drug rehabilitation (exception: life-threatening until stabilisation)
    
    Steps:
    1. Build a text blob: clinical.chief_complaints + " " + provisional_diagnosis +
       " " + (clinical.notes or "")
    2. For each exclusion category, check keyword matches:
       - "dental", "tooth", "cavity" → category 2
       - "infertility", "IVF", "ART" → category 3
       - "vaccine", "immunisation" → category 4
       - "cosmetic", "aesthetic" → category 5
       - "circumcision" + age < 2 → category 6
       - "PVS", "persistent vegetative" → category 7
       - "rehabilitation", "de-addiction" → category 8
    3. For any matched category, add a WARNING flag EXCLUSION_REVIEW_NEEDED
       with the category name and exception text:
       "May fall under exclusion category X — verify exception applies.
        Exception: [exception text]"
    4. Do NOT block — the exceptions are complex and require manual review.
       This is intentional for MVP.
    
    Add TODO comment: real implementation will use LLM to check exception
    conditions against clinical context.
    
    Return session.'''
```

---

## File 22: `phases/phase7_comorbidity.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `phases/phase7_comorbidity.py`. Phase 7 — comorbidity resolution.

Imports from session: IRISSession.

Required function:

def run_phase7(session: IRISSession) -> IRISSession:
    '''Phase 7 — comorbidity handling.
    
    HBP Guidelines: management of medical comorbidities (diabetes, hypertension,
    anaemia, etc.) during a surgical admission are INCLUDED in the surgical
    package price. They do NOT require separate packages.
    
    However, if a separate surgical condition is discovered/operated on during
    the admission, that IS a separate package (handled by Phase 4 combination).
    
    Steps:
    1. If session.clinical.comorbidities is empty → return session
    2. Determine if primary admission is surgical:
       - Look at session.final_package_set for any role="primary" with
         billing_type in {"surgical", "day_care"}
       - If yes: surgical_admission = True
    3. If surgical_admission:
       - For each comorbidity in clinical.comorbidities:
         - If it's a management condition (diabetes, hypertension, anaemia, dyslipidaemia, COPD):
           add note to session.comorbidity_notes:
           "Comorbidity '{name}' absorbed in surgical package — do not raise separately"
         - Else:
           add WARNING flag COMORBIDITY_REVIEW_NEEDED:
           "Comorbidity '{name}' may require separate evaluation"
    4. If not surgical admission:
       - Just add INFO note for each comorbidity that it's being managed
         under the relevant medical package
    
    Helper:
    
    def _is_management_condition(comorbidity: str) -> bool:
        '''Returns True for conditions typically absorbed in surgical admissions.
        Match common terms: diabetes, hypertension, anaemia, dyslipidaemia, copd, asthma'''
    
    Return session.'''
```

---

## File 23: `phases/phase8_special_pop.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `phases/phase8_special_pop.py`. Phase 8 — special populations.

Imports from session: IRISSession.
Imports from config: SENIOR_CITIZEN_AGE, PAEDIATRIC_AGE_MAX.

Public function:

def run_phase8(session: IRISSession) -> IRISSession:
    '''Phase 8 — flags for special population categories.
    
    Calls all sub-checks, each appends flags to session.
    Returns session.'''

Sub-functions (private):

def _check_age_routing(session: IRISSession) -> None:
    '''Age-based flags:
    - age <= 28 days → INFO flag NEONATAL_ESCALATION_RISK with message
      "Neonatal case — if condition deteriorates, current package must be
       unblocked and higher-level neonatal package booked"
    - age <= PAEDIATRIC_AGE_MAX → INFO flag PAEDIATRIC_DEVICE
      "Paediatric implants/devices apply for age <=14"
    - age >= SENIOR_CITIZEN_AGE → INFO flag SENIOR_CITIZEN
      (Vay Vandana wallet flag already set in Phase 5)'''

def _check_oncology(session: IRISSession) -> None:
    '''If any package in session.final_package_set has specialty_code in
    {"MO", "MR", "SC"}:
    - Add HARD WARNING flag MTB_REQUIRED:
      "Multidisciplinary Tumour Board (MTB) decision required before
       finalising oncology package. If hospital does not have MTB, refer
       to nearest Regional Cancer Centre (RCC)."
    - Add INFO flag ONCOLOGY_MULTI_STAGE:
      "Oncology treatment typically involves multiple stages — this IRIS
       run handles ONE stage only. Other stages require separate runs."'''

def _check_portability(session: IRISSession) -> None:
    '''If session.patient.home_state != session.hospital.state:
    - Add INFO flag PORTABILITY_CASE
      "Patient from {home_state} treated in {hospital.state}. Claims processing
       TAT is 30 days (vs 15 days for non-portability). Home state may also
       reject public-reserved packages booked by private hospital."'''

def _check_transplant(session: IRISSession) -> None:
    '''If any package in session.final_package_set has specialty_code == "OT":
    - Add HARD WARNING flag NOTTO_DOCS_REQUIRED:
      "Organ transplant — both recipient AND donor NOTTO IDs required.
       Missing either blocks claim. Also required: donor work-up summary,
       recipient work-up summary, cross-match report, signed donor undertaking,
       hospital authorisation letter."'''

Call each sub-function in order. Return session.
```

---

## File 24: `phases/phase9_documents.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "Critical Rules" #7 (public hospital relaxation).

Create `phases/phase9_documents.py`. Phase 9 — document gap analysis.

Imports from session: IRISSession.
Imports from models: DocumentItem, Investigation, DocumentInHand.
Imports from kb.loader: load_specialty_shard, get_procedure_from_shard.

Public function:

def run_phase9(session: IRISSession) -> IRISSession:
    '''Phase 9 — build required document list and compute gap.
    
    Steps:
    1. Compute set of available document keys from clinical input
    2. Compute universal required docs (based on hospital type)
    3. Compute conditional required docs (MLC, NOTTO, MTB, etc.)
    4. Per package, compute KB-2 mandatory_documents.preauth (private hospitals only)
    5. Merge all into session.preauth_docs_required
    6. Diff against available → session.preauth_docs_missing
    
    Returns session.'''

Helpers (private):

def _get_available_docs(clinical) -> set[str]:
    '''Returns set of canonical keys available right now.
    
    Sources:
    - clinical.investigations[] where document_available == True → invest.type
    - clinical.non_clinical_documents_in_hand[] where available == True → doc.key
    
    Returns set of strings.'''

def _get_universal_required(hospital) -> list[DocumentItem]:
    '''Universal required docs (every pre-auth):
    
    Public hospital (relaxation per CAM Annexure 7):
        - clinical_notes (hard_block)
    
    Private hospital:
        - clinical_notes (hard_block)
        - patient_photo (hard_block)
    
    Returns list of DocumentItem with package_code=None.'''

def _get_conditional_required(session) -> list[DocumentItem]:
    '''Check session flags for conditions that add doc requirements:
    
    - If session.mlc_required:
        + mlc_fir (hard_block)
        + self_declaration (hard_block)
    
    - If any flag with code "NOTTO_DOCS_REQUIRED":
        + notto_recipient_id (hard_block)
        + notto_donor_id (hard_block)
    
    - If any flag with code "MTB_REQUIRED":
        + tumour_board_approval (hard_block)
    
    Returns list of DocumentItem with package_code=None.'''

def _get_package_docs(session) -> list[DocumentItem]:
    '''Per-package required docs from KB-2 mandatory_documents.preauth.
    
    For public hospital: return EMPTY list (relaxation applies).
    
    For private hospital:
    - For each package in session.final_package_set:
      - Load shard, find procedure
      - Get procedure["mandatory_documents"]["preauth"] — list of {key, label} dicts
      - For each: create DocumentItem(key, label, package_code=pkg.validated.package_code,
                                       available=False (set later), criticality="ppd_query_risk")
      Note: criticality is ppd_query_risk because missing these triggers PPD query,
      not outright rejection. Only universal docs are hard_block.
    
    Returns merged list.'''

def _compute_gap(required: list[DocumentItem], available: set[str]) -> list[DocumentItem]:
    '''For each required doc:
    - Set doc.available = (doc.key in available)
    Return only the missing ones (where available == False).'''

In run_phase9, orchestrate:
1. available = _get_available_docs(session.clinical)
2. required = _get_universal_required(session.hospital) +
              _get_conditional_required(session) +
              _get_package_docs(session)
3. # Mark availability on the required list:
   for doc in required:
       doc.available = doc.key in available
4. session.preauth_docs_required = required
5. session.preauth_docs_missing = [d for d in required if not d.available]
6. Add INFO flag DOC_GAP_ANALYSIS with counts
7. If any missing doc has criticality="hard_block":
   Add WARNING flag MANDATORY_DOCS_MISSING
```

---

## File 25: `phases/phase10_output.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context. Read "Output Schema" section.

Create `phases/phase10_output.py`. Phase 10 — output assembly.

Imports from session: IRISSession.
Imports from models: IRISOutput, EnhancementPlan.

Required functions:

def run_phase10(session: IRISSession) -> IRISOutput:
    '''Phase 10 — assemble IRISOutput from session.
    Does NOT modify session. Pure read.
    
    Steps:
    1. Determine readiness_status using _determine_status(session)
    2. Build enhancement plan list
    3. Construct IRISOutput from session fields
    4. Return IRISOutput'''

def _determine_status(session: IRISSession) -> str:
    '''Returns one of: READY | READY_WITH_WARNINGS | CONDITIONAL | BLOCKED.
    
    Rules in order (first match wins):
    1. If any flag has severity == "block" → BLOCKED
    2. If any missing doc has criticality == "hard_block" → BLOCKED
    3. If session.final_package_set is empty → BLOCKED
    4. If any missing doc has criticality == "ppd_query_risk" → CONDITIONAL
    5. If any flag has severity == "warning" → READY_WITH_WARNINGS
    6. Otherwise → READY'''

def _build_enhancement_plan(session: IRISSession) -> list[EnhancementPlan]:
    '''For each package in session.final_package_set where
    enhancement_requests_needed is not None:
    
    Build an EnhancementPlan with:
    - procedure_code = pkg.validated.procedure_code
    - estimated_requests = pkg.validated.enhancement_requests_needed
    - batch_size_used = ENHANCEMENT_BATCH_PUBLIC if public/NE else ENHANCEMENT_BATCH_PRIVATE
    - los_indicative_days = load from procedure if available, else 0
    - caveat = "Estimated based on indicative LoS — actual stay may vary.
                File additional enhancement requests as needed."
    
    Returns list.'''

def serialize_output(output: IRISOutput) -> dict:
    '''Convert IRISOutput dataclass to a JSON-serialisable dict.
    Use dataclasses.asdict() but handle nested dataclasses correctly.
    Use this in main.py to produce final JSON output.'''
```

---

## File 26: `main.py`

**Prompt:**

```
Refer to SYSTEM_DESIGN.md for project context.

Create `main.py` at the project root. This is the CLI entry point and orchestrator.

Imports:
- import sys, json
- from pathlib import Path
- from logger_setup import setup_logging
- from input_validator import validate_input
- from session import IRISSession
- from models import ClinicalInput, Investigation, DocumentInHand, TreatingDoctor
- from phases.phase0_preflight import run_phase0
- from phases.phase1_emergency import run_phase1
- from phases.phase2_candidates import run_phase2
- from phases.phase3_validator import run_phase3
- from phases.phase4_multipackage import run_phase4
- from phases.phase5_financial import run_phase5
- from phases.phase6_exclusion import run_phase6
- from phases.phase7_comorbidity import run_phase7
- from phases.phase8_special_pop import run_phase8
- from phases.phase9_documents import run_phase9
- from phases.phase10_output import run_phase10, serialize_output

Required functions:

def parse_clinical_input(raw_clinical: dict) -> ClinicalInput:
    '''Convert raw JSON dict to ClinicalInput dataclass.
    Handle nested objects: investigations[], non_clinical_documents_in_hand[], treating_doctor.'''

def build_session(raw_json: dict) -> IRISSession:
    '''Create IRISSession from raw input JSON.
    1. Parse clinical input
    2. Create IRISSession with input_data=raw_json and clinical=parsed
    3. Return session'''

def run_pipeline(session: IRISSession) -> IRISOutput:
    '''Run all phases in sequence.
    
    Important: check session.has_block_flag() after every phase.
    If block flag set, skip to Phase 10 immediately.
    
    Also: after Phase 3, if session.validated_packages is empty,
    add USP_RECOMMENDED flag and skip Phases 4-8 (go directly to 9, 10).
    
    Returns IRISOutput.'''

def main():
    '''CLI entry point.
    
    Usage:
        python main.py <input.json>
        python main.py < input.json  (read from stdin)
    
    Output: JSON printed to stdout.
    '''
    
    setup_logging()
    
    # Read input
    if len(sys.argv) > 1:
        raw_json = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    else:
        raw_json = json.load(sys.stdin)
    
    # Validate
    valid, errors = validate_input(raw_json)
    if not valid:
        print(json.dumps({"error": "Invalid input", "details": errors}, indent=2))
        sys.exit(1)
    
    # Build session
    session = build_session(raw_json)
    
    # Run pipeline
    output = run_pipeline(session)
    
    # Serialize and print
    output_dict = serialize_output(output)
    print(json.dumps(output_dict, indent=2, default=str))

if __name__ == "__main__":
    main()

Pipeline orchestration logic (in run_pipeline):

session = run_phase0(session)
if session.has_block_flag():
    return run_phase10(session)

session = run_phase1(session)
if session.has_block_flag():
    return run_phase10(session)

session = run_phase2(session)
if session.has_block_flag():
    return run_phase10(session)

session = run_phase3(session)
if session.has_block_flag():
    return run_phase10(session)

# If no validated packages, recommend USP and skip Phase 4-8
if len(session.validated_packages) == 0:
    session.add_flag("USP_RECOMMENDED",
                     "No standard packages match — Unspecified Surgical Package (USP) pathway may be required. Verify with SHA.",
                     "warning")
    session = run_phase9(session)
    return run_phase10(session)

session = run_phase4(session)
session = run_phase5(session)
session = run_phase6(session)
session = run_phase7(session)
session = run_phase8(session)
session = run_phase9(session)
return run_phase10(session)
```

---

## Antigravity Setup Strategy

**Step 1.** Create your project folder. Drop `SYSTEM_DESIGN.md` and `IMPLEMENTATION_ORDER.md` in the root.

**Step 2.** In Antigravity, open the project folder. The chat panel will see those .md files.

**Step 3.** Start a session by saying:

> "I'm building a project called IRIS. Read SYSTEM_DESIGN.md and IMPLEMENTATION_ORDER.md in the project root. Acknowledge what you understand. Then wait for my per-file prompts."

**Step 4.** When ready to build a file, paste its prompt. Antigravity will already have the context.

**Step 5.** Build files in the order listed in IMPLEMENTATION_ORDER.md. After each file, run the smoke test before moving on.

**Step 6.** If a file's generation goes wrong, regenerate by pasting the same prompt with the note "Previous attempt had issue X. Try again with that fixed."

**Step 7.** When you hit Phase 3 (the complex one), expect to iterate 2-3 times. Don't accept the first generation if it doesn't match the prompt exactly.

---

## Cheat Sheet — What Order, What File

| # | File | LOC est |
|---|---|---|
| 1 | config.py | ~40 |
| 2 | logger_setup.py | ~20 |
| 3 | models.py | ~180 |
| 4 | session.py | ~80 |
| 5 | input_validator.py | ~20 |
| 6 | kb/__init__.py | 1 |
| 7 | kb/loader.py | ~80 |
| 8 | stubs/__init__.py | 1 |
| 9 | stubs/bis_stub.py | ~50 |
| 10 | stubs/hem_stub.py | ~40 |
| 11 | phases/__init__.py | 1 |
| 12 | phases/phase0_preflight.py | ~60 |
| 13 | phases/phase1_emergency.py | ~25 |
| 14 | kb/searcher.py | ~80 |
| 15 | phases/phase2_candidates.py | ~40 |
| 16 | llm/__init__.py | 1 |
| 17 | llm/stg_checker.py | ~100 |
| 18 | phases/phase3_validator.py | ~250 |
| 19 | phases/phase4_multipackage.py | ~150 |
| 20 | phases/phase5_financial.py | ~50 |
| 21 | phases/phase6_exclusion.py | ~60 |
| 22 | phases/phase7_comorbidity.py | ~50 |
| 23 | phases/phase8_special_pop.py | ~70 |
| 24 | phases/phase9_documents.py | ~120 |
| 25 | phases/phase10_output.py | ~80 |
| 26 | main.py | ~80 |

**Total: ~1,700 LOC across 26 files.**

---

## After You Have All Files

Run end-to-end smoke test:

```bash
python main.py iris_input_schema.json
```

Expect: full IRISOutput JSON printed. May have errors initially. Fix incrementally using logger output to find which phase is breaking.

**Save 3-5 test inputs in `tests/inputs/`** with hand-written expected outputs in `tests/expected/`. Re-run all of them whenever you modify any phase file.
