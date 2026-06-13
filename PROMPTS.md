# IRIS — Per-File Prompts for Antigravity

**Before every prompt:** make sure Antigravity has read `SYSTEM_DESIGN.md`. Start each session with:
> "Read SYSTEM_DESIGN.md in the project root. This is your full context for the IRIS project. Acknowledge, then wait for my prompts."

**One prompt per file. Paste the matching prompt. Review generated code against the interface contract before moving on.**

---

## FILE 1: `config.py`

```
Refer to SYSTEM_DESIGN.md.

Create `config.py` at the project root. All tunable constants used anywhere in the IRIS pipeline live here. No other file may hardcode these values.

Use plain module-level assignments. No class wrapper. No dataclass.

from pathlib import Path

# Paths
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

# Phase 2 fuzzy search
TOP_N_CANDIDATES: int = 30
MIN_FUZZY_SCORE: int = 60        # 0-100 scale, rapidfuzz

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
```

---

## FILE 2: `logger_setup.py`

```
Refer to SYSTEM_DESIGN.md.

Create `logger_setup.py` at the project root.

Single function setup_logging() that configures Python's logging module using values from config.py.

Requirements:
- Use config.LOG_LEVEL and config.LOG_FORMAT
- Configure root logger so all child loggers inherit the format
- Function must be idempotent (safe to call multiple times without duplicating handlers)
- Returns the root logger

No file handlers. Console (stdout) only.

Usage in other modules:
    import logging
    logger = logging.getLogger(__name__)
```

---

## FILE 3: `models.py`

```
Refer to SYSTEM_DESIGN.md. Read "Input Schema (v2)" and "Output Schema" sections carefully.

Create `models.py` at the project root. All dataclasses for the IRIS pipeline EXCEPT IRISSession (which lives in session.py).

Rules:
- Python 3.11+ dataclasses only. No Pydantic.
- Type hints on every field.
- field(default_factory=list) for list defaults.
- field(default_factory=dict) for dict defaults.
- Add a docstring to each dataclass explaining its role.

Dataclasses in order:

1. WalletBalance
   family_balance_inr: int
   vay_vandana_balance_inr: int | None
   policy_year_start: str   # ISO date string e.g. "2025-04-01"

2. PastClaim
   procedure_code: str
   admission_date: str
   package_amount_inr: int
   status: str

3. PatientContext
   patient_id: str
   family_id: str
   name: str
   age: int
   gender: str              # "M" | "F"
   home_state: str
   home_district: str
   wallet: WalletBalance
   past_claims: list[PastClaim] = field(default_factory=list)

4. HospitalContext
   hospital_id: str
   name: str
   type: str                # "private" | "public"
   city_tier: str           # "tier1" | "tier2" | "tier3"
   state: str
   district: str
   is_aspirational_district: bool
   accreditation: str       # "none" | "bronze" | "nabh_entry" | "nabh_full" | "nqas"
   scheme: str              # "pmjay" only in MVP
   empanelled_specialties: list[str]

5. StructuredValue
   # One extracted lab/investigation parameter from OCR
   parameter: str
   value: float | str | None
   unit: str | None
   flag: str | None         # "H" (high) | "L" (low) | "N" (normal) | null
   leads: str | None        # for ECG findings e.g. "II, III, aVF"

6. Investigation
   type: str                # canonical enum: ecg|echo|xray|ct|mri|usg|blood_reports|
                            # urine_report|stool_report|cag_report|eeg|abg_chart|csf|hpe|fnac|other
   result_summary: str | None
   structured_values: list[StructuredValue] | None   # null if doc not available or not OCR'd
   document_available: bool
   report_date: str | None  # ISO date or null

7. DocumentInHand
   key: str                 # canonical key e.g. "clinical_notes", "patient_photo"
   label: str
   available: bool

8. ExaminationFindings
   general: str | None
   cvs: str | None
   rs: str | None
   abdomen: str | None
   cns: str | None
   local: str | None

9. PersonalHistory
   smoking: str | None
   alcohol: str | None
   diet: str | None

10. TreatingDoctor
    name: str
    registration_number: str
    qualification: str
    specialty_code: str      # 2-letter HBP code e.g. "MC"

11. ClinicalInput
    admission_date: str | None             # ISO date
    bed_category: str | None              # ward|hdu|icu_no_vent|icu_vent or null
    is_emergency: bool
    is_medico_legal: bool
    chief_complaints: str
    duration_days: int
    history_of_present_illness: str | None
    provisional_diagnosis: str
    planned_procedure: str | None
    weight_kg: float | None
    height_cm: float | None
    vitals: dict                            # flexible dict: bp_systolic_mmhg, pulse_bpm, etc.
    examination_findings: ExaminationFindings | None
    investigations: list[Investigation]
    comorbidities: list[str]
    past_medical_history: str | None
    past_surgical_history: str | None
    current_medications: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    personal_history: PersonalHistory | None = None
    family_history: str | None = None
    non_clinical_documents_in_hand: list[DocumentInHand] = field(default_factory=list)
    treating_doctor: TreatingDoctor | None = None
    notes: str | None = None

12. CandidatePackage
    # Thin — from _index.json after fuzzy search
    procedure_code: str
    package_code: str
    specialty_code: str
    specialty: str
    package_name: str
    procedure_name: str
    billing_unit: str
    reserved_public_only: bool
    procedure_label: str     # regular|add_on|standalone|follow_up
    auto_approved: str       # none|full|day1_only
    day_care: bool
    base_rate_inr: int | None
    match_score: float

13. StratificationResult
    determinable: bool
    selected_stratum: str | None    # stratum_id e.g. "ward", "hdu", "General Anaesthesia"
    note: str | None                # populated when not determinable

14. ImplantResult
    required: bool
    name: str | None
    cost_inr: int | None
    age_appropriate: bool
    gender_appropriate: bool
    quantity: int | None

15. ValidatedPackage
    # Rich — after Phase 3 validation
    procedure_code: str
    package_code: str
    specialty_code: str
    package_name: str
    procedure_name: str
    billing_type: str           # surgical|fixed_medical|per_day|day_care
    billing_unit: str
    procedure_label: str
    auto_approved: str
    enhancement_applicable: bool
    enhancement_requests_needed: int | None
    reserved_public_only: bool
    base_rate_inr: int | None
    stratification: StratificationResult
    implant: ImplantResult
    special_conditions_popup: bool
    special_conditions_rule: bool
    stg_eligible: bool
    stg_missing_criteria: list[str] = field(default_factory=list)
    is_addon_to: list[str] | None = None
    addon_type: str | None = None
    match_score: float = 0.0
    flags: list[str] = field(default_factory=list)   # per-package warning strings

16. FinalPackage
    validated: ValidatedPackage
    role: str                   # primary|secondary|tertiary|addon|standalone
    deduction_factor: float     # 1.0 | 0.5 | 0.25
    pre_auth_group: int         # 1 = main pre-auth, 2 = standalone or split pre-auth

17. DocumentItem
    key: str
    label: str
    package_code: str | None    # None = universal requirement
    available: bool
    criticality: str            # hard_block | ppd_query_risk

18. Flag
    code: str                   # UPPER_SNAKE_CASE e.g. "PUB_RESERVED_BLOCK"
    message: str
    severity: str               # info | warning | block

19. EnhancementPlan
    procedure_code: str
    estimated_requests: int
    batch_size_used: int
    los_indicative_days: int    # 0 if los was "daycare"
    caveat: str                 # always set to: "Estimated based on indicative LoS — actual stay may vary. File additional enhancement requests as needed."

20. IRISOutput
    readiness_status: str       # READY|READY_WITH_WARNINGS|CONDITIONAL|BLOCKED
    selected_packages: list[FinalPackage] = field(default_factory=list)
    blocked_candidates: list[dict] = field(default_factory=list)  # {procedure_code, reason_code, message}
    preauth_docs_required: list[DocumentItem] = field(default_factory=list)
    preauth_docs_missing: list[DocumentItem] = field(default_factory=list)
    enhancement_plan: list[EnhancementPlan] = field(default_factory=list)
    copayment_required: bool = False
    copayment_gap_inr: int | None = None
    comorbidity_notes: list[str] = field(default_factory=list)  # from Phase 7 — which comorbidities are absorbed vs flagged
    flags: list[Flag] = field(default_factory=list)
    stg_coverage: dict = field(default_factory=lambda: {"validated": 0, "stg_missing": 0})
    errors: list[str] = field(default_factory=list)
```

---

## FILE 4: `session.py`

```
Refer to SYSTEM_DESIGN.md. Read "The Session Object" section.

Create `session.py` at the project root. Single dataclass IRISSession — the pipeline spine.

Imports from models: PatientContext, HospitalContext, ClinicalInput, CandidatePackage,
ValidatedPackage, FinalPackage, DocumentItem, Flag.

Module-level docstring:
"""
IRISSession — the IRIS pipeline spine.

session.flags : list[Flag]
    Business outcomes the MEDCO needs to see. Severity: info | warning | block.
    Pipeline proceeds to Phase 10 immediately when any block flag is set.

session.errors : list[str]
    Technical failures for the developer. Plain strings. Pipeline NEVER stops on errors.

session.stg_coverage : dict
    {"validated": n, "stg_missing": n} — incremented by Phase 3, read by Phase 10.

session.usp_recommended : bool
    Set True by main.py when Phase 3 returns zero validated packages.
    Causes main.py to skip Phases 4-8 and jump to Phase 9.
"""

Fields:

# Set at session creation
input_data: dict               # raw input JSON
clinical: ClinicalInput        # parsed clinical block

# Populated by Phase 0
patient: PatientContext | None = None
hospital: HospitalContext | None = None
patient_eligible: bool = False
hospital_empanelled: bool = False
mlc_required: bool = False

# Populated by Phase 1 (stubbed)
is_emergency: bool = False
er_package_code: str | None = None
needs_specialty_package: bool = True

# Populated by Phase 2
candidate_packages: list[CandidatePackage] = field(default_factory=list)

# Populated by Phase 3
validated_packages: list[ValidatedPackage] = field(default_factory=list)
phase3_blocked: list[dict] = field(default_factory=list)  # {procedure_code, reason_code, message}
stg_coverage: dict = field(default_factory=lambda: {"validated": 0, "stg_missing": 0})

# Set by main.py after Phase 3
usp_recommended: bool = False

# Populated by Phase 4
final_package_set: list[FinalPackage] = field(default_factory=list)

# Populated by Phase 5
wallet_sufficient: bool = True
copayment_required: bool = False
copayment_gap_inr: int | None = None
estimated_total_inr: int = 0

# Populated by Phase 7
comorbidity_notes: list[str] = field(default_factory=list)

# Populated by Phase 9
preauth_docs_required: list[DocumentItem] = field(default_factory=list)
preauth_docs_missing: list[DocumentItem] = field(default_factory=list)

# Accumulated by all phases
flags: list[Flag] = field(default_factory=list)
errors: list[str] = field(default_factory=list)

Helper methods:

def has_block_flag(self) -> bool:
    return any(f.severity == "block" for f in self.flags)

def add_flag(self, code: str, message: str, severity: str) -> None:
    from models import Flag
    self.flags.append(Flag(code=code, message=message, severity=severity))
```

---

## FILE 5: `input_validator.py`

```
Refer to SYSTEM_DESIGN.md.

Create `input_validator.py` at the project root.

def validate_input(raw_json: dict) -> tuple[bool, list[str]]:
    """
    Validates IRIS input JSON structure.

    Returns (True, []) if valid.
    Returns (False, [error_messages]) if invalid.

    STUB: always returns (True, []) for now.
    Full validation to be implemented later.

    TODO — real validation will check:
    - Required top-level keys present: patient, hospital, clinical
    - patient.patient_id is a non-empty string
    - hospital.hospital_id is a non-empty string
    - clinical.chief_complaints is a non-empty string
    - clinical.provisional_diagnosis is a non-empty string
    - clinical.is_emergency is a boolean
    - clinical.investigations is a list (can be empty)
    - clinical.bed_category is null or one of: ward, hdu, icu_no_vent, icu_vent
    - clinical.vitals is a dict (can be empty)
    """
    return (True, [])
```

---

## FILE 6: `kb/__init__.py`

```
Create empty file `kb/__init__.py` containing only:
# IRIS knowledge base layer
```

---

## FILE 7: `kb/loader.py`

```
Refer to SYSTEM_DESIGN.md. Read "KB-2 HBP Shard Schema" and "KB-3 STG Schema" sections carefully.
These sections show the EXACT field names in the JSON files. Use only those field names.

Create `kb/loader.py`. Pure I/O — loads JSON files from disk.
All functions use @lru_cache so each file is loaded at most once per process run.

Imports:
from functools import lru_cache
import json
import logging
from pathlib import Path
from config import INDEX_FILE, HBP_DIR, STG_DIR, PMJAY_RULES_FILE, QUERY_TAXONOMY_FILE

logger = logging.getLogger(__name__)

Functions:

@lru_cache(maxsize=1)
def load_index() -> list[dict]:
    """Load data/hbp/_index.json. Returns list of thin index rows.
    Each row has: procedure_code, package_code, specialty_code, specialty,
    package_name, procedure_name, aliases, billing_unit, reserved_public_only,
    procedure_label, auto_approved, day_care, base_rate_inr, stg_ref."""

@lru_cache(maxsize=32)
def load_specialty_shard(shard_filename: str) -> dict:
    """Load data/hbp/<shard_filename>.json.

    shard_filename is the filename WITHOUT extension e.g. "burnsmanagement",
    "emergency_room_packages", "general_surgery".

    Top-level shard structure: {scheme_id, specialty, specialty_code, packages: [...]}
    Each package: {package_code, package_name, procedures: [...]}
    Each procedure has all fields from the KB-2 schema in SYSTEM_DESIGN.md.

    Raises FileNotFoundError if shard doesn't exist (valid — not all shards built yet).
    Raises json.JSONDecodeError on malformed JSON (always log + re-raise — this is a bug)."""

@lru_cache(maxsize=500)
def load_stg(procedure_code: str) -> dict | None:
    """Load data/stg/<procedure_code>.json.

    Returns None if file does not exist — this is expected and NOT an error.
    Many procedures don't have STG files built yet.

    Returns the dict if found. Key fields in the STG dict:
    clinical_indications (list[str]),
    clinical_thresholds (list[{field, operator, value, note}] — NO unit field),
    min_doctor_qualification (list[str] — an ARRAY not a string),
    additional_information.clinical_key_pointers (list[str] — can be very long),
    checklist.ppd_preauth (list[{q: str, expected: bool}] — expected is boolean not string),
    common_queries (list[str]),
    mandatory_documents.preauth and .claim."""

@lru_cache(maxsize=1)
def load_pmjay_rules() -> dict:
    """Load data/schemes/pmjay.json. Returns scheme-wide rules dict."""

@lru_cache(maxsize=1)
def load_query_taxonomy() -> dict:
    """Load data/query_taxonomy.json. Returns query + rejection reasons."""

def get_procedure_from_shard(procedure_code: str, shard: dict) -> dict | None:
    """Find a procedure dict by code in a loaded shard.

    Searches: shard["packages"] → each package["procedures"] → procedure["procedure_code"]
    Returns the procedure dict or None if not found.

    The returned procedure dict has all KB-2 fields including:
    billing_unit, medical_or_surgical, day_care, reserved_public_only,
    stratification_required, stratification_criteria (list or null),
    los_indicative (int or string "daycare"),
    enhancement_applicable, auto_approved, procedure_label,
    special_conditions_popup, special_conditions_rule,
    is_addon_to (list or null), addon_type (str or null),
    implant (null or dict or list),
    mandatory_documents.preauth (list of {key, label}),
    rates_inr (dict or null for per_day),
    pricing.base_rate_inr (int or null)."""

def get_package_from_shard(package_code: str, shard: dict) -> dict | None:
    """Find a package dict by package_code in a loaded shard.
    Searches shard["packages"] for matching package_code.
    Returns the package dict (containing procedures list) or None."""

Logging:
- INFO when a file is loaded for the first time (lru_cache will prevent duplicates)
- WARNING when load_stg returns None (file doesn't exist)
- ERROR + re-raise on JSONDecodeError
- Catch FileNotFoundError in load_stg specifically and return None (not an error)
- Catch FileNotFoundError in load_specialty_shard and re-raise (IS an error)
```

---

## FILE 8: `stubs/__init__.py`

```
Create empty file `stubs/__init__.py` containing only:
# IRIS data source stubs — replaced by real APIs later
```

---

## FILE 9: `stubs/bis_stub.py`

```
Refer to SYSTEM_DESIGN.md.

Create `stubs/bis_stub.py`. Simulates the BIS (Beneficiary Identification System) API.

Imports from config: DUMMY_BIS_FILE
Imports from models: PatientContext, WalletBalance, PastClaim

The dummy_bis.json structure is a dict keyed by patient_id:
{
  "P001": {
    "patient_id": "P001",
    "family_id": "FAM-TN-001",
    "name": "Ravi Kumar",
    "age": 58,
    "gender": "M",
    "home_state": "Tamil Nadu",
    "home_district": "Chennai",
    "wallet": {
      "family_balance_inr": 350000,
      "vay_vandana_balance_inr": null,
      "policy_year_start": "2025-04-01"
    },
    "past_claims": [
      {"procedure_code": "SG019A", "admission_date": "2025-08-15", "package_amount_inr": 18000, "status": "paid"}
    ]
  }
}

Functions:

def verify_bis(patient_id: str) -> PatientContext | None:
    """Load dummy_bis.json, find patient by patient_id.
    Returns PatientContext if found, None if not found.
    NOTE: Will be replaced by real BIS API call. Return type stays the same."""

def get_wallet_balance(family_id: str) -> WalletBalance | None:
    """Get wallet for a family_id. Reads from dummy_bis.json.
    Scans all patient records for matching family_id.
    Returns WalletBalance or None.
    NOTE: Will be replaced by dedicated BIS wallet endpoint."""

Log INFO on successful find, WARNING if patient_id not found.
```

---

## FILE 10: `stubs/hem_stub.py`

```
Refer to SYSTEM_DESIGN.md.

Create `stubs/hem_stub.py`. Simulates the HEM (Hospital Empanelment Module) API.

Imports from config: DUMMY_HEM_FILE
Imports from models: HospitalContext

The dummy_hem.json structure is a dict keyed by hospital_id:
{
  "H001": {
    "hospital_id": "H001",
    "name": "Apollo Hospitals Chennai",
    "type": "private",
    "city_tier": "tier1",
    "state": "Tamil Nadu",
    "district": "Chennai",
    "is_aspirational_district": false,
    "accreditation": "nabh_full",
    "scheme": "pmjay",
    "empanelled_specialties": ["SG", "MC", "SB", "MG", "SO", "SU", "BM", "SN"]
  }
}

Function:

def check_empanelment(hospital_id: str) -> HospitalContext:
    """Load dummy_hem.json, find hospital by hospital_id.

    ALWAYS returns a HospitalContext (never None) — MVP assumes always empanelled.

    If hospital_id not found, return fallback HospitalContext:
    {hospital_id: hospital_id, name: "Unknown Hospital", type: "private",
     city_tier: "tier1", state: "Tamil Nadu", district: "Chennai",
     is_aspirational_district: False, accreditation: "nabh_full",
     scheme: "pmjay",
     empanelled_specialties: ["SG","MC","SB","MG","SO","SU","BM","SN","MR","SC","MO"]}

    Log WARNING when fallback is used.
    NOTE: Real HEM API will return None if not empanelled. Caller (Phase 0) must
    handle that when real API is wired in."""
```
## FILE 11: `phases/__init__.py`

```
Create empty file `phases/__init__.py` containing only:
# IRIS pipeline phases
```

---

## FILE 12: `phases/phase0_preflight.py`

```
Refer to SYSTEM_DESIGN.md. Read "Critical Rules" #2 (CMCHIS fast-fail).

Create `phases/phase0_preflight.py`.

Imports from session: IRISSession
Imports from stubs.bis_stub: verify_bis
Imports from stubs.hem_stub: check_empanelment

def run_phase0(session: IRISSession) -> IRISSession:
    """Phase 0 — Pre-flight gates.

    Steps:
    1. Extract patient_id from session.input_data["patient"]["patient_id"]
    2. Call verify_bis(patient_id) → set session.patient
       If None → add_flag("PATIENT_NOT_IN_BIS", "Patient ID not found in BIS", "block") → return session
    3. session.patient_eligible = True
    4. Extract hospital_id from session.input_data["hospital"]["hospital_id"]
    5. Call check_empanelment(hospital_id) → set session.hospital
    6. session.hospital_empanelled = True
    7. Check session.hospital.scheme:
       if scheme != "pmjay":
           add_flag("SCHEME_NOT_SUPPORTED",
                    f"Scheme '{session.hospital.scheme}' not supported. IRIS handles PM-JAY only.",
                    "block")
           return session
    8. session.mlc_required = session.clinical.is_medico_legal
    9. Log INFO: patient name, hospital name, scheme, mlc_required
    10. Return session

    Wrap steps 1-2 and 4-5 in try/except. On exception:
    - append f"Phase0 error: {e}" to session.errors
    - add_flag("PREFLIGHT_FAILED", str(e), "block")
    - return session"""
```

---

## FILE 13: `phases/phase1_emergency.py`

```
Refer to SYSTEM_DESIGN.md.

Create `phases/phase1_emergency.py`. Phase 1 — emergency routing. STUBBED.

Imports from session: IRISSession

def run_phase1(session: IRISSession) -> IRISSession:
    """Phase 1 — Emergency routing. STUBBED for MVP.

    Always assumes non-emergency planned admission.

    Steps:
    1. session.is_emergency = False
    2. session.er_package_code = None
    3. session.needs_specialty_package = True
    4. add_flag("EMERGENCY_PHASE_STUBBED",
                "Emergency routing not implemented — assuming planned elective admission",
                "info")
    5. Return session

    TODO (real implementation):
    - Use vitals (BP, GCS, SpO2) + chief_complaints to determine emergency
    - Select ER package (ER001A/ER002A/ER002B/ER003A) based on severity
    - If hospitalisation expected >12h: needs_specialty_package=True, two pre-auths
    - Animal bite → ER003A with payment-after-5th-dose flag
    - Set is_emergency=True and er_package_code accordingly"""
```

---

## FILE 14: `kb/searcher.py`

```
Refer to SYSTEM_DESIGN.md. Read "KB-2 Index Schema" section for exact field names.

Create `kb/searcher.py`. Fuzzy search over _index.json.

Dependencies: rapidfuzz library. Add rapidfuzz>=3.0 to requirements.txt.

Imports:
from rapidfuzz import fuzz
import logging
from config import TOP_N_CANDIDATES, MIN_FUZZY_SCORE
from kb.loader import load_index
from models import ClinicalInput, CandidatePackage

logger = logging.getLogger(__name__)

Public function:

def search_candidates(
    clinical: ClinicalInput,
    empanelled_specialties: list[str],
    hospital_is_public: bool
) -> list[CandidatePackage]:
    """Fuzzy search _index.json to generate candidate packages.

    Steps:
    1. Build search string via _build_search_string(clinical)
    2. Load index via load_index()
    3. Pre-filter:
       - Only rows where row["specialty_code"] in empanelled_specialties
       - If hospital_is_public is False: exclude rows where reserved_public_only=True
    4. Score each surviving row:
       - For each alias in row["aliases"]: score = fuzz.token_set_ratio(search_string, alias)
       - Also score against row["package_name"] and row["procedure_name"]
       - match_score = max of all scores
    5. Filter: keep only rows where match_score >= MIN_FUZZY_SCORE
    6. Sort descending by match_score
    7. Take top TOP_N_CANDIDATES
    8. Deduplicate by package_code: if multiple rows share same package_code,
       keep only the one with highest match_score (cross-specialty duplicates)
    9. Convert each row to CandidatePackage:
       - All fields from index row map directly to CandidatePackage fields
       - match_score from step 4
    10. Return list

    Log at INFO: count after each filter step.
    Log WARNING if final list is empty."""

Private helper:

def _build_search_string(clinical: ClinicalInput) -> str:
    """Build search string from clinical input.

    STUB for now — concatenates available text fields.
    Later will be replaced by LLM entity extraction.

    Concatenate (space-separated, skip None):
    - clinical.chief_complaints
    - clinical.provisional_diagnosis
    - clinical.planned_procedure (if not None)
    - clinical.history_of_present_illness (if not None)

    Return the joined string."""

The _index.json row fields (for CandidatePackage mapping):
procedure_code → procedure_code
package_code → package_code
specialty_code → specialty_code
specialty → specialty
package_name → package_name
procedure_name → procedure_name
billing_unit → billing_unit
reserved_public_only → reserved_public_only
procedure_label → procedure_label
auto_approved → auto_approved
day_care → day_care
base_rate_inr → base_rate_inr (can be null)
```

---

## FILE 15: `phases/phase2_candidates.py`

```
Refer to SYSTEM_DESIGN.md.

Create `phases/phase2_candidates.py`.

Imports from session: IRISSession
Imports from kb.searcher: search_candidates

def run_phase2(session: IRISSession) -> IRISSession:
    """Phase 2 — Candidate package generation via fuzzy search.

    Steps:
    1. If session.has_block_flag() → return session immediately (defensive)
    2. Call:
       candidates = search_candidates(
           clinical=session.clinical,
           empanelled_specialties=session.hospital.empanelled_specialties,
           hospital_is_public=(session.hospital.type == "public")
       )
    3. session.candidate_packages = candidates
    4. add_flag("CANDIDATES_GENERATED",
                f"Generated {len(candidates)} candidates from index",
                "info")
    5. If len(candidates) == 0:
       add_flag("NO_CANDIDATES_FOUND",
                "Fuzzy search returned zero candidates. Check clinical input or consider USP pathway.",
                "warning")
    6. Return session

    Wrap in try/except: on exception, append to session.errors,
    add_flag("CANDIDATE_GENERATION_FAILED", str(e), "block"), return session."""
```

---

## FILE 16: `llm/__init__.py`

```
Create empty file `llm/__init__.py` containing only:
# IRIS LLM-powered checks
```

---

## FILE 17: `llm/stg_checker.py`

```
Refer to SYSTEM_DESIGN.md. Read "LLM Usage Policy" section and "KB-3 STG Schema" section.

IMPORTANT: The STG schema has specific field types:
- stg["clinical_thresholds"] items have: {field, operator, value, note} — NO unit field
- stg["min_doctor_qualification"] is a LIST of strings, not a single string
- stg["checklist"]["ppd_preauth"] items have expected as boolean (true/false), not string
- stg["additional_information"]["clinical_key_pointers"] is a list of potentially long strings

Create `llm/stg_checker.py`.

Dependencies: gemini SDK. Add gemini's sdk (which is implemented as 'from google import genai') to requirements.txt.

Imports:
from google import genai
import json
import logging
from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES
from models import ClinicalInput

logger = logging.getLogger(__name__)

Public function:

def check_stg_eligibility(
    procedure_code: str,
    stg: dict,
    clinical: ClinicalInput
) -> dict:
    """LLM-based STG eligibility check.

    Returns dict:
    {
        "eligible": bool,
        "missing_criteria": [str, ...],   # list of unmet criteria descriptions
        "reasoning": str,                  # one-paragraph explanation
        "confidence": str                  # "high" | "medium" | "low"
    }

    On failure after all retries, returns:
    {"eligible": True, "missing_criteria": [],
     "reasoning": "LLM check failed — passed by default", "confidence": "low"}
    """

Build the LLM prompt using ONLY these STG fields (others are noisy):
- stg.get("clinical_indications", [])           — list of strings
- stg.get("clinical_thresholds", [])             — list of {field, operator, value, note}
- stg.get("min_doctor_qualification", [])        — LIST of strings (join with ", " for prompt)
- stg.get("alos", "unspecified")                 — string
- stg.get("additional_information", {}).get("clinical_key_pointers", [])  — list of strings (include all)

Build the clinical summary from:
- clinical.provisional_diagnosis
- clinical.chief_complaints
- clinical.history_of_present_illness (if not None)
- clinical.duration_days
- clinical.vitals (include non-null values only)
- clinical.examination_findings (each non-null field)
- clinical.investigations: for each, include type + result_summary + structured_values if present
- clinical.comorbidities
- clinical.past_medical_history (if not None)
- clinical.current_medications (if not empty)

SYSTEM_PROMPT = """You are a clinical eligibility validator for PM-JAY (India's national health scheme) package selection.

You are given:
1. Standard Treatment Guideline (STG) criteria for a specific procedure
2. A patient's clinical presentation

Your task: determine if the patient's clinical condition meets the STG eligibility criteria.

You MUST respond with valid JSON only. No prose outside the JSON object.

Response schema:
{
  "eligible": true or false,
  "missing_criteria": ["description of each unmet criterion"],
  "reasoning": "one paragraph explanation of your decision",
  "confidence": "high", "medium", or "low"
}

Rules:
- Match medical concepts even when exact words differ. Example: "BCVA 6/60" satisfies threshold "BCVA <= 6/9" because 6/60 is worse vision than 6/9.
- If a required investigation type is mentioned in criteria but not present in the clinical input at all, list it as missing.
- If an investigation exists but the specific value isn't recorded, set confidence to "low".
- Comorbidities do not block eligibility unless the STG explicitly states a contraindication.
- Minimum doctor qualification: if treating doctor's qualification is not provided or clearly below minimum, note it but do not block — set confidence to "low".
- If clinical_key_pointers contain important eligibility details, use them.
"""

USER_PROMPT_TEMPLATE:
"""STG eligibility criteria for PM-JAY procedure {procedure_code}:

=== CLINICAL INDICATIONS ===
{indications}

=== CLINICAL THRESHOLDS (must be met for pre-auth approval) ===
{thresholds}

=== MINIMUM DOCTOR QUALIFICATION ===
{qualifications}

=== EXPECTED LENGTH OF STAY ===
{alos}

=== CLINICAL KEY NOTES ===
{clinical_key_pointers}

=== PATIENT CLINICAL PRESENTATION ===
Provisional diagnosis: {diagnosis}
Chief complaints: {complaints}
Duration: {duration} days
{history_section}
{vitals_section}
{examination_section}
Investigations: {investigations}
Comorbidities: {comorbidities}
{pmh_section}
{medications_section}

Evaluate eligibility and respond with JSON only."""

Retry logic:
- Try up to LLM_MAX_RETRIES times
- On each attempt, call the Gemini API with LLM_MODEL, temperature=0, max_tokens=500
- Parse response as JSON
- Validate that response has "eligible" key (bool) and "reasoning" key (str)
- If parse fails or validation fails → retry
- After all retries exhausted → return the failure fallback dict

Log:
- DEBUG: raw LLM response text
- INFO: procedure_code, eligible result, confidence
- WARNING: on retry
- ERROR: when all retries fail

Private helpers:
def _format_thresholds(thresholds: list[dict]) -> str:
    """Format threshold list for prompt. Each threshold: {field, operator, value, note}
    Format as: "- {field} {operator} {value} (Note: {note})" """

def _format_investigations(investigations: list) -> str:
    """Format investigation list for prompt.
    For each investigation: include type, result_summary, and structured_values if present.
    Structured values: show as "parameter=value unit" pairs."""
```

---

## FILE 18: `phases/phase3_validator.py`

```
Refer to SYSTEM_DESIGN.md carefully. Read:
- "KB-2 HBP Shard Schema" — all exact field names
- "Billing Type Classification" — primary + fallback logic
- "Critical Rules" — all rules relevant to Phase 3
- "KB-3 STG Schema" — field types for STG

This is the most complex file. Build it carefully.

Create `phases/phase3_validator.py`.

Imports:
from math import ceil
import logging
from session import IRISSession
from models import CandidatePackage, ValidatedPackage, StratificationResult, ImplantResult
from kb.loader import load_specialty_shard, get_procedure_from_shard, load_stg
from llm.stg_checker import check_stg_eligibility
from config import (ENHANCEMENT_BATCH_PRIVATE, ENHANCEMENT_BATCH_PUBLIC,
                    NE_STATES_AND_ISLANDS, PAEDIATRIC_AGE_MAX,
                    REQUIRE_STG_FOR_VALIDATION)

SPECIALTY_CODE_TO_SHARD = {
    "BM": "burnsmanagement",
    "MC": "cardiology",
    "SV": "ctvs",
    "ER": "emergency_room_packages",
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

Public function:

def run_phase3(session: IRISSession) -> IRISSession:
    """Phase 3 — per-candidate validation.

    For each candidate in session.candidate_packages, run all checks.
    Survivors → session.validated_packages.
    Blocked → session.phase3_blocked.

    After loop:
    - Update session.stg_coverage
    - If zero validated packages: add WARNING flag NO_VALIDATED_PACKAGES"""

Per-candidate loop — wrap each candidate in try/except. On exception:
- log ERROR
- append error to session.errors
- append to phase3_blocked with reason_code="INTERNAL_ERROR"
- continue to next candidate

Steps per candidate:

STEP 1 — Load full procedure from shard:
    shard_filename = SPECIALTY_CODE_TO_SHARD.get(candidate.specialty_code)
    if not shard_filename:
        block candidate: reason_code="SPECIALTY_CODE_UNKNOWN"
        continue
    try:
        shard = load_specialty_shard(shard_filename)
    except FileNotFoundError:
        block candidate: reason_code="SHARD_NOT_FOUND",
                         message=f"Shard file {shard_filename}.json not found"
        continue
    procedure = get_procedure_from_shard(candidate.procedure_code, shard)
    if procedure is None:
        block: reason_code="PROCEDURE_NOT_IN_SHARD"
        continue

STEP 2 — Public reservation check:
    if procedure["reserved_public_only"] and session.hospital.type == "private":
        block: reason_code="PUB_RESERVED_BLOCK",
               message=f"{candidate.procedure_code} is reserved for public hospitals only"
        continue

STEP 3 — Classify billing type via _classify_billing_type(procedure):
    # Store result — used in later steps and in ValidatedPackage

STEP 4 — STG eligibility:
    stg = load_stg(candidate.procedure_code)
    if stg is None:
        session.stg_coverage["stg_missing"] += 1
        pkg_flags.append("STG_MISSING")
        if REQUIRE_STG_FOR_VALIDATION:
            block: reason_code="STG_REQUIRED"
            continue
        # else: pass with warning — stg_eligible=True, stg_missing_criteria=[]
        stg_eligible = True
        stg_missing_criteria = []
    else:
        session.stg_coverage["validated"] += 1
        result = check_stg_eligibility(candidate.procedure_code, stg, session.clinical)
        stg_eligible = result["eligible"]
        stg_missing_criteria = result["missing_criteria"]
        if not stg_eligible:
            block: reason_code="STG_NOT_ELIGIBLE",
                   message=result["reasoning"]
            continue
        if result["confidence"] == "low":
            pkg_flags.append(f"STG_LOW_CONFIDENCE: {result['reasoning'][:100]}")

STEP 5 — Stratification:
    stratification = _determine_stratification(procedure, session.clinical)
    if not stratification.determinable:
        pkg_flags.append(f"STRATIFICATION_UNDETERMINABLE: {stratification.note}")

STEP 6 — Implant check:
    implant = _check_implant(procedure, session.patient)
    if implant.required and not implant.age_appropriate:
        pkg_flags.append("IMPLANT_AGE_BOUNDARY: verify paediatric vs adult device")

STEP 7 — Special conditions:
    popup = procedure.get("special_conditions_popup", False)
    rule = procedure.get("special_conditions_rule", False)
    if rule:
        # Check past_claims for same procedure_code in same policy year
        policy_year_start = session.patient.wallet.policy_year_start  # e.g. "2025-04-01"
        policy_year = policy_year_start[:4]  # "2025"
        for claim in session.patient.past_claims:
            if (claim.procedure_code == candidate.procedure_code and
                    claim.admission_date[:4] == policy_year):
                pkg_flags.append(
                    f"SPECIAL_CONDITIONS_RULE_TRIGGERED: prior claim for "
                    f"{candidate.procedure_code} found in policy year {policy_year}"
                )
                break

STEP 8 — Enhancement planning:
    enhancement_requests_needed = _plan_enhancement(procedure, session.hospital)

STEP 9 — Build ValidatedPackage and append to session.validated_packages

Private helpers:

def _classify_billing_type(procedure: dict) -> str:
    """Classify billing type for combination rules.

    Priority order:
    1. billing_unit == "per_day" → "per_day"
    2. day_care == True → "day_care"
    3. medical_or_surgical field present:
       - "surgical" → "surgical"
       - "medical" → "fixed_medical"
    4. Fallback (medical_or_surgical field missing):
       - Check source_refs["billing_unit"] for "(Surgical)" substring → "surgical"
       - Check source_refs["billing_unit"] for "(Medical)" substring → "fixed_medical"
       - Default → "fixed_medical" (safer default for combination rules)
       - Log WARNING with procedure_code when fallback is used"""

def _determine_stratification(procedure: dict, clinical: ClinicalInput) -> StratificationResult:
    """Determine stratification for this procedure.

    If procedure["stratification_required"] is False:
        return StratificationResult(determinable=True, selected_stratum=None, note=None)

    If True:
        stratification_criteria = procedure["stratification_criteria"]  # list of {stratum_id, description, criterion, source}

        CASE A — per_day packages (bed category stratification):
        Check if any stratum has stratum_id in {"ward","hdu","icu_no_vent","icu_vent"}
        If yes, this is a per_day bed-category stratification.
        Match clinical.bed_category against stratum_id values:
            if clinical.bed_category is None:
                return StratificationResult(determinable=False, selected_stratum=None,
                    note="bed_category not provided in clinical input — required for per-day package stratification")
            if clinical.bed_category in {s["stratum_id"] for s in stratification_criteria}:
                return StratificationResult(determinable=True, selected_stratum=clinical.bed_category, note=None)

        CASE B — non-per-day stratification (anaesthesia type, laterality, etc.):
        For MVP: attempt simple keyword matching on criterion fields against
        clinical text (chief_complaints + notes + examination_findings).
        Build combined_text = " ".join of all available clinical text fields.
        For each stratum, check if key terms from criterion appear in combined_text.
        If match found → return that stratum.
        If no match → return StratificationResult(determinable=False, selected_stratum=None,
            note="Stratification type undeterminable from available clinical input — physician selection required")"""

def _check_implant(procedure: dict, patient) -> ImplantResult:
    """Check implant requirement and appropriateness.

    procedure["implant"] is null, a dict {name, cost_inr}, or a list of such dicts.

    If null: return ImplantResult(required=False, name=None, cost_inr=None,
                                   age_appropriate=True, gender_appropriate=True, quantity=None)

    If dict or list: required=True.
    For MVP:
    - name = implant["name"] if dict, else implant[0]["name"] if list
    - cost_inr = implant["cost_inr"] if dict, else implant[0]["cost_inr"] if list
    - age_appropriate = True (TMS auto-detects; overriding triggers audit — just track)
    - gender_appropriate = True (same)
    - quantity = 1 (MEDCO selects in TMS)

    Note: age boundary check is advisory only — do NOT block."""

def _plan_enhancement(procedure: dict, hospital) -> int | None:
    """Calculate enhancement requests needed.

    If procedure["enhancement_applicable"] is False: return None

    los = procedure["los_indicative"]

    IMPORTANT: los_indicative can be int OR string "daycare"
    If los == "daycare" or los == 0 or los is None: return None

    is_ne = hospital.state in NE_STATES_AND_ISLANDS
    if hospital.type == "public" or is_ne:
        batch = ENHANCEMENT_BATCH_PUBLIC
    else:
        batch = ENHANCEMENT_BATCH_PRIVATE

    los_int = int(los) if isinstance(los, (int, float)) else 0
    if los_int <= 1: return None
    return ceil((los_int - 1) / batch)"""
```
## FILE 19: `phases/phase4_multipackage.py`

```
Refer to SYSTEM_DESIGN.md. Read "Critical Rules" #5-12 (combination rules).

Create `phases/phase4_multipackage.py`. Phase 4 — multi-package resolution.

Imports from session: IRISSession
Imports from models: ValidatedPackage, FinalPackage

def run_phase4(session: IRISSession) -> IRISSession:
    """Phase 4 — multi-package resolution.

    If session.validated_packages is empty → return session immediately (usp path).

    Steps:
    1. Classify into buckets
    2. Apply combination rules — drop incompatible, add flags
    3. Isolate standalones into pre_auth_group=2
    4. Attach/validate add-ons — drop orphaned ones
    5. Assign deduction factors and roles
    6. Populate session.final_package_set

    Returns session."""

Private helpers:

def _classify_buckets(packages: list[ValidatedPackage]) -> dict:
    """Group by billing_type.
    Returns: {"surgical": [...], "fixed_medical": [...], "per_day": [...], "day_care": [...]}
    Note: day_care is treated as surgical for combination rule purposes."""

def _check_combination_rules(buckets: dict, session: IRISSession) -> list[ValidatedPackage]:
    """Apply combination rules. Returns allowed packages after drops.

    Rules:
    - surgical/day_care + per_day → REMOVE all per_day.
      add_flag("SURGICAL_PERDAY_BLOCKED",
               "Surgical and per-day medical packages cannot be combined in same pre-auth. Per-day package(s) removed.",
               "warning")

    - per_day + per_day (multiple) → keep only first (highest match_score).
      add_flag("PERDAY_MULTIPLE_BLOCKED",
               "Multiple per-day packages not allowed in same pre-auth. Keeping highest-matched only.",
               "warning")

    - surgical/day_care + fixed_medical → allowed, 100% each (no action)
    - surgical + surgical → allowed, 100-50-25 (handled in _assign_deduction_factors)
    - fixed_medical + fixed_medical → allowed, 100% each

    Return combined list of all allowed packages."""

def _isolate_standalones(packages: list[ValidatedPackage], session: IRISSession) -> tuple[list, list]:
    """Separate standalone packages from the rest.

    Returns (standalone_list, regular_list).

    If standalones exist alongside regular packages:
        add_flag("STANDALONE_SPLIT",
                 f"{len(standalone_list)} standalone package(s) must be raised as a separate pre-auth (pre_auth_group=2).",
                 "info")"""

def _attach_addons(packages: list[ValidatedPackage], session: IRISSession) -> list[ValidatedPackage]:
    """Validate add-on packages have their parent in the set.

    For each package where procedure_label == "add_on":
        is_addon_to = package.is_addon_to  # list of parent procedure_codes or None
        parent_codes_in_set = {p.procedure_code for p in packages if p.procedure_label != "add_on"}

        If is_addon_to is None or is_addon_to is empty:
            Drop add-on.
            add_flag("ADDON_PARENT_UNKNOWN", f"{package.procedure_code} add-on parent unknown — removed.", "warning")
            continue

        If no parent from is_addon_to is in parent_codes_in_set:
            Drop add-on.
            add_flag("ADDON_PARENT_MISSING", f"{package.procedure_code} parent not in validated set — removed.", "warning")
            continue

        Also check diagnostic add-on rule:
        If package.addon_type == "diagnostic_highend":
            primary_billing_types = {p.billing_type for p in packages if p.procedure_label != "add_on"}
            if "per_day" not in primary_billing_types:
                Drop add-on.
                add_flag("DIAGNOSTIC_ADDON_BLOCKED",
                         f"{package.procedure_code} diagnostic add-on only allowed with per-day medical primary.",
                         "warning")
                continue

    Return list with invalid add-ons removed."""

def _assign_deduction_factors(
    regular_packages: list[ValidatedPackage],
    standalone_packages: list[ValidatedPackage]
) -> list[FinalPackage]:
    """Assign roles, deduction factors, and pre_auth_group.

    For regular packages (pre_auth_group=1):
        Separate surgicals (billing_type in {"surgical","day_care"}) from others.
        Sort surgicals by base_rate_inr descending (APPROXIMATE — real rule uses final price).

        Surgical[0] → role="primary",   deduction_factor=1.0
        Surgical[1] → role="secondary", deduction_factor=0.5
        Surgical[2+]→ role="tertiary",  deduction_factor=0.25

        Fixed medical → role="primary", deduction_factor=1.0 (each)
        Per-day      → role="primary", deduction_factor=1.0
        Add-ons      → role="addon",   deduction_factor=1.0

    For standalone packages (pre_auth_group=2):
        All → role="standalone", deduction_factor=1.0

    Add flag: add_flag("DEDUCTION_APPROXIMATE",
                       "100-50-25 deduction order uses base_rate as proxy for final price. "
                       "Actual order should use final price after tier/level/accreditation multipliers.",
                       "info")

    Return combined list of FinalPackage objects."""
```

---

## FILE 20: `phases/phase5_financial.py`

```
Refer to SYSTEM_DESIGN.md. Read "Critical Rules" #17-18 (Vay Vandana, null rates).

Create `phases/phase5_financial.py`. Phase 5 — wallet sufficiency check.

Imports from session: IRISSession
Imports from config: SENIOR_CITIZEN_AGE

def run_phase5(session: IRISSession) -> IRISSession:
    """Phase 5 — simplified financial check.

    If session.final_package_set is empty → session.wallet_sufficient=True, return.

    Steps:
    1. Estimate total cost:
       total = 0
       null_rate_count = 0
       for pkg in session.final_package_set:
           rate = pkg.validated.base_rate_inr
           if rate is None:
               null_rate_count += 1
               continue  # skip — per_day rate depends on bed category
           total += int(rate * pkg.deduction_factor)
       session.estimated_total_inr = total

       If null_rate_count > 0:
           add_flag("RATE_NULL_FOR_PERDAY",
                    f"{null_rate_count} per-day package(s) excluded from estimate — rate depends on bed category and LoS.",
                    "info")

    2. Determine available wallet:
       family_balance = session.patient.wallet.family_balance_inr
       vay_vandana = session.patient.wallet.vay_vandana_balance_inr or 0

       if session.patient.age >= SENIOR_CITIZEN_AGE and vay_vandana > 0:
           add_flag("VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS",
                    f"Patient has dual wallet: family ₹{family_balance:,} + Vay Vandana ₹{vay_vandana:,}. "
                    f"NHA does not specify debit order — verify with SHA before submission.",
                    "warning")
           available = family_balance + vay_vandana
       else:
           available = family_balance

    3. Check sufficiency:
       if total <= available:
           session.wallet_sufficient = True
       else:
           session.wallet_sufficient = False
           session.copayment_required = True
           session.copayment_gap_inr = total - available
           add_flag("WALLET_INSUFFICIENT",
                    f"Estimated cost ₹{total:,} exceeds available wallet ₹{available:,}. Gap: ₹{total-available:,}.",
                    "warning")

    4. Always add:
       add_flag("FINANCIAL_ESTIMATE_APPROXIMATE",
                "Cost estimate uses base rates only. Final rates include tier, level, accreditation, and geo multipliers.",
                "info")

    5. Return session

    TODO: real pricing uses Final Price = (Procedure + Stratification) × Level × Tier × Accreditation × Geo + Implant_flat"""
```

---

## FILE 21: `phases/phase6_exclusion.py`

```
Refer to SYSTEM_DESIGN.md.

Create `phases/phase6_exclusion.py`. Phase 6 — exclusion check.

Imports from session: IRISSession

def run_phase6(session: IRISSession) -> IRISSession:
    """Phase 6 — exclusion category check.

    8 exclusion categories from PM-JAY HBP Guidelines Annexure 6.
    For MVP: keyword matching on clinical text. Adds WARNING flags only — no blocking.
    Exceptions are complex; require manual review.

    Build clinical_text = (chief_complaints + " " + provisional_diagnosis +
                           " " + (notes or "") + " " + (history_of_present_illness or "")).lower()

    Check each category:

    1. OPD-only: "outpatient", "opd", "clinic visit" → flag EXCLUSION_OPD_ONLY_RISK
       Exception: none

    2. Dental: "dental", "tooth", "teeth", "cavity", "root canal" → flag EXCLUSION_DENTAL_RISK
       Exception: "trauma/injury requiring bone treatment — verify exception applies"

    3. Infertility/ART: "infertility", "ivf", "art", "ivf", "assisted reproductive" → flag EXCLUSION_INFERTILITY_RISK
       Exception: "unless procedure is listed in HBP — verify"

    4. Vaccination: "vaccine", "vaccination", "immunisation", "immunization" → flag EXCLUSION_VACCINATION_RISK
       Exception: none

    5. Cosmetic/Aesthetic: "cosmetic", "aesthetic", "augmentation", "rhinoplasty", "liposuction" → flag EXCLUSION_COSMETIC_RISK
       Exception: "trauma deformity or congenital functional impairment — verify exception applies"

    6. Circumcision under 2 years: "circumcision" AND session.patient.age < 2 → flag EXCLUSION_CIRCUMCISION_RISK
       Exception: "disease or accident — verify exception applies"

    7. Persistent Vegetative State: "persistent vegetative", "pvs" → flag EXCLUSION_PVS_RISK
       Exception: none

    8. Drug rehabilitation: "rehabilitation", "de-addiction", "deaddiction", "detox" → flag EXCLUSION_DRUG_REHAB_RISK
       Exception: "life-threatening condition until stabilisation — verify exception applies. Suicide attempt/alcohol overdose: cover until stable."

    For each match, add_flag(code, message_with_exception_note, "warning").

    Return session.

    TODO: replace keyword matching with LLM call that can handle exception conditions properly."""
```

---

## FILE 22: `phases/phase7_comorbidity.py`

```
Refer to SYSTEM_DESIGN.md.

Create `phases/phase7_comorbidity.py`. Phase 7 — comorbidity resolution.

Imports from session: IRISSession

MANAGEMENT_CONDITIONS = {
    "diabetes", "type2_diabetes", "type1_diabetes", "dm", "t2dm", "t1dm",
    "hypertension", "htn", "anaemia", "anemia", "dyslipidaemia", "dyslipidemia",
    "hypothyroidism", "copd", "asthma", "chronic_kidney_disease", "ckd",
    "obesity", "hyperlipidaemia", "hyperlipidemia"
}

def run_phase7(session: IRISSession) -> IRISSession:
    """Phase 7 — comorbidity resolution.

    PM-JAY rule: management of comorbidities during a surgical admission is INCLUDED
    in the surgical package. Separate packages for management conditions are NOT raised.

    Only separate packages when: a comorbidity requires its own surgical intervention
    (handled by Phase 4 combination, not here).

    Steps:
    1. If no comorbidities → return session
    2. Determine if primary admission is surgical:
       surgical_admission = any(pkg.validated.billing_type in {"surgical", "day_care"}
                                for pkg in session.final_package_set
                                if pkg.role == "primary")
    3. For each comorbidity:
       if _is_management_condition(comorbidity):
           if surgical_admission:
               session.comorbidity_notes.append(
                   f"Comorbidity '{comorbidity}' is absorbed in the surgical package — do not raise separately.")
           else:
               session.comorbidity_notes.append(
                   f"Comorbidity '{comorbidity}' managed under current medical package.")
       else:
           add_flag("COMORBIDITY_REVIEW_NEEDED",
                    f"Comorbidity '{comorbidity}' may require separate clinical review — not a standard management condition.",
                    "info")
    4. Return session

def _is_management_condition(comorbidity: str) -> bool:
    return comorbidity.lower() in MANAGEMENT_CONDITIONS"""
```

---

## FILE 23: `phases/phase8_special_pop.py`

```
Refer to SYSTEM_DESIGN.md. Read "Critical Rules" #22-26 (special populations).

Create `phases/phase8_special_pop.py`. Phase 8 — special population flags.

Imports from session: IRISSession
Imports from config: SENIOR_CITIZEN_AGE, PAEDIATRIC_AGE_MAX

def run_phase8(session: IRISSession) -> IRISSession:
    """Phase 8 — special population routing flags.
    Calls each sub-check. Returns session."""

def _check_age_routing(session: IRISSession) -> None:
    age = session.patient.age
    if age <= 28:   # neonatal (days, but age field in PatientContext is integer — if 0, assume neonatal)
        # Note: age=0 means under 1 year; we treat age=0 as potential neonatal
        session.add_flag("NEONATAL_ESCALATION_RISK",
            "Neonatal case (age ≤28 days): if condition deteriorates, current package must be "
            "UNBLOCKED and higher-level neonatal package booked. Monitor vitals closely.",
            "warning")
    if age <= PAEDIATRIC_AGE_MAX:
        session.add_flag("PAEDIATRIC_DEVICE",
            f"Patient age {age} ≤{PAEDIATRIC_AGE_MAX}: paediatric implants/devices apply where relevant. "
            "TMS auto-detects; overriding triggers medical audit.",
            "info")

def _check_oncology(session: IRISSession) -> None:
    oncology_specialties = {"MO", "MR", "SC"}
    has_oncology = any(
        pkg.validated.specialty_code in oncology_specialties
        for pkg in session.final_package_set
    )
    if has_oncology:
        session.add_flag("MTB_REQUIRED",
            "Oncology package selected. Multidisciplinary Tumour Board (MTB) decision is mandatory "
            "before finalising package. If hospital lacks MTB, refer to nearest Regional Cancer Centre (RCC).",
            "warning")
        session.add_flag("ONCOLOGY_MULTI_STAGE",
            "Oncology treatment involves multiple stages (staging/surgery/chemo/radiation). "
            "This IRIS run handles ONE stage only. Each subsequent stage requires a separate run.",
            "info")

def _check_portability(session: IRISSession) -> None:
    if session.patient.home_state.lower() != session.hospital.state.lower():
        session.add_flag("PORTABILITY_CASE",
            f"Portability case: patient from {session.patient.home_state} treated in {session.hospital.state}. "
            "Claims processing TAT is 30 days (vs 15 for standard). "
            "Home state may reject public-reserved packages booked by private hospitals.",
            "info")

def _check_transplant(session: IRISSession) -> None:
    has_transplant = any(
        pkg.validated.specialty_code == "OT"
        for pkg in session.final_package_set
    )
    if has_transplant:
        session.add_flag("NOTTO_DOCS_REQUIRED",
            "Organ transplant package selected. Both recipient AND donor NOTTO IDs required. "
            "Also required: donor work-up summary, recipient work-up summary, cross-match report, "
            "signed donor undertaking, hospital authorisation letter.",
            "warning")

Call all four sub-checks in run_phase8.
```

---

## FILE 24: `phases/phase9_documents.py`

```
Refer to SYSTEM_DESIGN.md. Read "Critical Rules" #19-21 (document rules).

Create `phases/phase9_documents.py`. Phase 9 — document gap analysis.

Imports from session: IRISSession
Imports from models: DocumentItem
Imports from kb.loader import load_specialty_shard, get_procedure_from_shard
From config import SPECIALTY_CODE_TO_SHARD (or re-import from phase3)

Note: SPECIALTY_CODE_TO_SHARD dict is defined in phase3_validator.py.
To avoid duplication, either import it from there or define it in a shared module.
For MVP: redefine it at top of this file.

def run_phase9(session: IRISSession) -> IRISSession:
    """Phase 9 — build required document list and compute gap.

    Steps:
    1. available_keys = _get_available_doc_keys(session.clinical)
    2. required = (
           _get_universal_required(session.hospital) +
           _get_conditional_required(session) +
           _get_package_docs(session)
       )
    3. Deduplicate required by key+package_code to avoid repeats
    4. For each doc in required: doc.available = (doc.key in available_keys)
    5. session.preauth_docs_required = required
    6. session.preauth_docs_missing = [d for d in required if not d.available]
    7. add_flag("DOC_GAP_ANALYSIS",
                f"Required: {len(required)} docs. Missing: {len(session.preauth_docs_missing)}.",
                "info")
    8. If any missing doc has criticality=="hard_block":
       add_flag("MANDATORY_DOCS_MISSING",
                f"{sum(1 for d in session.preauth_docs_missing if d.criticality=='hard_block')} mandatory document(s) missing — cannot submit pre-auth until resolved.",
                "warning")
    9. Return session"""

def _get_available_doc_keys(clinical) -> set[str]:
    """Build set of available canonical doc keys.

    Sources:
    - clinical.investigations where document_available==True → add investigation.type
    - clinical.non_clinical_documents_in_hand where available==True → add doc.key

    Returns set of strings."""

def _get_universal_required(hospital) -> list[DocumentItem]:
    """Universal docs for every pre-auth.

    Public hospital (per CAM Annexure 7 relaxation):
        DocumentItem(key="clinical_notes", label="Admission / clinical notes",
                     package_code=None, available=False, criticality="hard_block")

    Private hospital (universal baseline):
        DocumentItem(key="clinical_notes", ..., criticality="hard_block")
        DocumentItem(key="patient_photo", label="Photo of patient on hospital bed",
                     package_code=None, available=False, criticality="hard_block")"""

def _get_conditional_required(session) -> list[DocumentItem]:
    """Conditional docs based on session flags and clinical context.

    If session.mlc_required:
        Add mlc_fir (hard_block)
        Add self_declaration (hard_block)

    If any flag with code "NOTTO_DOCS_REQUIRED":
        Add notto_recipient_id (hard_block)
        Add notto_donor_id (hard_block)

    If any flag with code "MTB_REQUIRED":
        Add tumour_board_approval (hard_block)

    All package_code=None (universal conditionals)."""

def _get_package_docs(session) -> list[DocumentItem]:
    """Per-package required docs from KB-2 mandatory_documents.preauth.

    Public hospital: return [] (relaxation — only clinical_notes needed, already in universal)

    Private hospital:
    For each pkg in session.final_package_set:
        Try:
            shard_filename = SPECIALTY_CODE_TO_SHARD.get(pkg.validated.specialty_code)
            if not shard_filename: continue
            shard = load_specialty_shard(shard_filename)
            procedure = get_procedure_from_shard(pkg.validated.procedure_code, shard)
            if not procedure: continue
            for doc in procedure["mandatory_documents"]["preauth"]:
                Add DocumentItem(
                    key=doc["key"],
                    label=doc["label"],
                    package_code=pkg.validated.package_code,
                    available=False,  # set later in run_phase9
                    criticality="ppd_query_risk"
                )
        Except any error: log WARNING, continue

    Return list."""
```

---

## FILE 25: `phases/phase10_output.py`

```
Refer to SYSTEM_DESIGN.md. Read "Output Schema" section — four readiness states and status determination logic.

Create `phases/phase10_output.py`. Phase 10 — output assembly.

Imports:
from dataclasses import asdict
from session import IRISSession
from models import IRISOutput, EnhancementPlan
from config import ENHANCEMENT_BATCH_PRIVATE, ENHANCEMENT_BATCH_PUBLIC, NE_STATES_AND_ISLANDS

def run_phase10(session: IRISSession) -> IRISOutput:
    """Assemble IRISOutput from session. Pure read — does not modify session.

    Build IRISOutput by reading all relevant session fields:
    - readiness_status = _determine_status(session)
    - selected_packages = session.final_package_set
    - blocked_candidates = session.phase3_blocked
    - preauth_docs_required = session.preauth_docs_required
    - preauth_docs_missing = session.preauth_docs_missing
    - enhancement_plan = _build_enhancement_plan(session)
    - copayment_required = session.copayment_required
    - copayment_gap_inr = session.copayment_gap_inr
    - comorbidity_notes = session.comorbidity_notes   ← copy from session
    - flags = session.flags
    - stg_coverage = session.stg_coverage
    - errors = session.errors
    """

def _determine_status(session: IRISSession) -> str:
    """Determine readiness status. First match wins:
    1. Any flag with severity=="block" → "BLOCKED"
    2. Any missing doc with criticality=="hard_block" → "BLOCKED"
    3. session.final_package_set is empty AND not usp_recommended → "BLOCKED"
    4. Any missing doc with criticality=="ppd_query_risk" → "CONDITIONAL"
    5. Any flag with severity=="warning" → "READY_WITH_WARNINGS"
    6. Otherwise → "READY" """

def _build_enhancement_plan(session: IRISSession) -> list[EnhancementPlan]:
    """Build enhancement plan for per-day packages.

    For each pkg in session.final_package_set where
    pkg.validated.enhancement_requests_needed is not None and > 0:

        is_ne = session.hospital.state in NE_STATES_AND_ISLANDS
        batch = ENHANCEMENT_BATCH_PUBLIC if (session.hospital.type=="public" or is_ne) else ENHANCEMENT_BATCH_PRIVATE

        los = pkg.validated.   # look up from the procedure — use enhancement_requests_needed and batch to back-calc
        # Actually: store los_indicative in ValidatedPackage. For now use 0 if not available.

        EnhancementPlan(
            procedure_code=pkg.validated.procedure_code,
            estimated_requests=pkg.validated.enhancement_requests_needed,
            batch_size_used=batch,
            los_indicative_days=0,   # placeholder if not stored on ValidatedPackage
            caveat="Estimated based on indicative LoS — actual stay may vary. File additional enhancement requests as needed."
        )"""

def serialize_output(output: IRISOutput) -> dict:
    """Convert IRISOutput to JSON-serialisable dict.

    Use dataclasses.asdict(output).
    Handle any non-serialisable types (datetime, Path, etc.) by converting to str.
    Return the dict."""
```

---

## FILE 26: `main.py`

```
Refer to SYSTEM_DESIGN.md.

Create `main.py` at the project root. CLI entry point and pipeline orchestrator.

Imports:
import sys
import json
import logging
from pathlib import Path
from logger_setup import setup_logging
from input_validator import validate_input
from session import IRISSession
from models import (ClinicalInput, Investigation, StructuredValue, DocumentInHand,
                    ExaminationFindings, PersonalHistory, TreatingDoctor)
from phases.phase0_preflight import run_phase0
from phases.phase1_emergency import run_phase1
from phases.phase2_candidates import run_phase2
from phases.phase3_validator import run_phase3
from phases.phase4_multipackage import run_phase4
from phases.phase5_financial import run_phase5
from phases.phase6_exclusion import run_phase6
from phases.phase7_comorbidity import run_phase7
from phases.phase8_special_pop import run_phase8
from phases.phase9_documents import run_phase9
from phases.phase10_output import run_phase10, serialize_output

logger = logging.getLogger(__name__)

def parse_clinical_input(raw_clinical: dict) -> ClinicalInput:
    """Convert raw clinical dict to ClinicalInput dataclass.

    Parse nested objects:
    - investigations: list of Investigation (each with StructuredValue list or None)
    - non_clinical_documents_in_hand: list of DocumentInHand
    - treating_doctor: TreatingDoctor or None
    - examination_findings: ExaminationFindings or None
    - personal_history: PersonalHistory or None

    Use .get() with defaults for all optional fields.
    vitals stays as dict — don't parse into a dataclass."""

def build_session(raw_json: dict) -> IRISSession:
    """Create IRISSession from raw input JSON.

    1. Parse clinical block via parse_clinical_input(raw_json.get("clinical", {}))
    2. Return IRISSession(input_data=raw_json, clinical=parsed_clinical)"""

def run_pipeline(session: IRISSession) -> IRISOutput:
    """Run all pipeline phases in sequence.

    After Phase 0: check has_block_flag() → if True, skip to Phase 10
    After Phase 1: check has_block_flag() → if True, skip to Phase 10
    After Phase 2: check has_block_flag() → if True, skip to Phase 10
    After Phase 3: check has_block_flag() → if True, skip to Phase 10

    After Phase 3 — special routing:
        if len(session.validated_packages) == 0:
            session.usp_recommended = True
            session.add_flag("USP_RECOMMENDED",
                             "No standard PM-JAY packages validated for this clinical input. "
                             "Unspecified Surgical Package (USP) pathway may apply. Consult SHA.",
                             "warning")
            # Skip Phases 4-8 — go directly to doc check and output
            session = run_phase9(session)
            return run_phase10(session)

    Otherwise run all phases:
    session = run_phase4(session)
    session = run_phase5(session)
    session = run_phase6(session)
    session = run_phase7(session)
    session = run_phase8(session)
    session = run_phase9(session)
    return run_phase10(session)"""

def main():
    """CLI entry point.

    Usage:
        python main.py input.json          (read from file)
        python main.py < input.json        (read from stdin)

    Output: IRISOutput as formatted JSON to stdout."""

    setup_logging()

    # Read input
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1])
        raw_json = json.loads(input_path.read_text(encoding="utf-8"))
        logger.info(f"Input loaded from {input_path}")
    else:
        raw_json = json.load(sys.stdin)
        logger.info("Input loaded from stdin")

    # Validate
    valid, errors = validate_input(raw_json)
    if not valid:
        print(json.dumps({"error": "Invalid input", "details": errors}, indent=2))
        sys.exit(1)

    # Build session
    session = build_session(raw_json)
    logger.info(f"Session created: patient_id={raw_json.get('patient',{}).get('patient_id','?')}")

    # Run pipeline
    output = run_pipeline(session)

    # Serialize and print
    output_dict = serialize_output(output)
    print(json.dumps(output_dict, indent=2, default=str))

    logger.info(f"Pipeline complete. Status: {output.readiness_status}")

if __name__ == "__main__":
    main()
```

---

## Antigravity Setup Strategy

**Step 1.** Create your project folder. Put `SYSTEM_DESIGN.md` and `IMPLEMENTATION_ORDER.md` in the root.

**Step 2.** Start Antigravity session:
> "I'm building a project called IRIS — a PM-JAY package selection engine. Read SYSTEM_DESIGN.md and IMPLEMENTATION_ORDER.md in the project root now. These are your complete context for this project. Acknowledge what you understand, then wait for my file prompts."

**Step 3.** Build files in order 1→26. For each: paste that file's prompt. Let Antigravity generate. Review the code — specifically check that KB field names match SYSTEM_DESIGN.md exactly.

**Step 4.** After each phase block (A through F), run the smoke test from IMPLEMENTATION_ORDER.md before continuing.

**Step 5.** Phase 3 and Phase 17 (stg_checker.py) will likely need 2-3 iterations. When regenerating, add: "Previous version had this issue: [describe]. Fix it and regenerate the full file."

**Step 6.** If Antigravity invents a field name not in SYSTEM_DESIGN.md, call it out: "That field doesn't exist in our KB schema. The correct field is X. Regenerate."

---

## Cheat Sheet

| # | File | Key dependency | Est. LOC |
|---|---|---|---|
| 1 | config.py | None | ~40 |
| 2 | logger_setup.py | config | ~20 |
| 3 | models.py | None | ~200 |
| 4 | session.py | models | ~80 |
| 5 | input_validator.py | None | ~15 |
| 6 | kb/__init__.py | None | 1 |
| 7 | kb/loader.py | config | ~90 |
| 8 | stubs/__init__.py | None | 1 |
| 9 | stubs/bis_stub.py | config, models | ~50 |
| 10 | stubs/hem_stub.py | config, models | ~40 |
| 11 | phases/__init__.py | None | 1 |
| 12 | phases/phase0_preflight.py | session, stubs | ~60 |
| 13 | phases/phase1_emergency.py | session | ~25 |
| 14 | kb/searcher.py | config, kb/loader, models | ~90 |
| 15 | phases/phase2_candidates.py | session, kb/searcher | ~40 |
| 16 | llm/__init__.py | None | 1 |
| 17 | llm/stg_checker.py | config, models | ~120 |
| 18 | phases/phase3_validator.py | all kb, llm, models | ~280 |
| 19 | phases/phase4_multipackage.py | session, models | ~160 |
| 20 | phases/phase5_financial.py | session, config | ~60 |
| 21 | phases/phase6_exclusion.py | session | ~60 |
| 22 | phases/phase7_comorbidity.py | session | ~50 |
| 23 | phases/phase8_special_pop.py | session, config | ~70 |
| 24 | phases/phase9_documents.py | session, models, kb | ~130 |
| 25 | phases/phase10_output.py | session, models | ~80 |
| 26 | main.py | all phases | ~90 |

**Total: ~1,800 LOC across 26 files.**

After all files are generated: `python main.py tests/inputs/TC01_happy_path_surgical.json`

This should produce a full IRISOutput JSON. Debug from there using the logger output.
