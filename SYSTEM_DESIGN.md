# IRIS Pre-Auth & Claim Selection Engine — System Design

This document serves as the absolute, ground-up source of truth for the architecture, data models, knowledge base schemas, processing pipeline, and LLM integrations of the IRIS pre-authorisation and claim selection engine. All descriptions correspond directly to the actual python codebase.

---

## 1. Pipeline Architecture & Overview

The IRIS pipeline is built around a shared, mutable state container: the `IRISSession`. The system processes patient clinical cases in two major stages: **Stage 1 & 2 (Pre-Authorisation)** and **Stage 3 (Claims Verification)**.

### Stage 1 & 2: Pre-Authorisation Pipeline (Phases 0–10)

The pre-authorisation workflow runs sequentially through 11 phases (Phases 0 through 10). Each phase mutates the `IRISSession` by adding flags, errors, or calculating package fields.

#### Pipeline Sequence and State Mutations

| Phase | Name | Reads from Session / Input | Writes to Session |
| :--- | :--- | :--- | :--- |
| **Phase 0** | **Preflight Gates** | `input_data["patient"]["patient_id"]`, `input_data["hospital"]["hospital_id"]`, `clinical.is_medico_legal` | `patient` (`PatientContext`), `hospital` (`HospitalContext`), `patient_eligible`, `hospital_empanelled`, `mlc_required` |
| **Phase 1** | **Emergency Routing** | `clinical` (vitals, chief complaints) | `is_emergency` (always `False`), `er_package_code` (always `None`), `needs_specialty_package` (always `True`) |
| **Phase 2** | **Candidate Generation** | `clinical`, `hospital.empanelled_specialties`, `hospital.type` | `candidate_packages` (`list[CandidatePackage]`) |
| **Phase 3** | **Per-Candidate Validation** | `candidate_packages`, `clinical`, `hospital`, `patient` | `validated_packages` (`list[ValidatedPackage]`), `phase3_blocked` (`list[dict]`), `stg_coverage` (`dict`) |
| **Phase 4** | **Multi-Package Combinations** | `validated_packages`, `clinical` | `final_package_set` (`list[FinalPackage]`) |
| **Phase 5** | **Financial Wallet Check** | `final_package_set`, `patient` | `estimated_total_inr`, `wallet_sufficient`, `copayment_required`, `copayment_gap_inr` |
| **Phase 6** | **Clinical Exclusions Check** | `final_package_set`, `patient.age`, `clinical` | Mutates `final_package_set` (drops excluded packages) |
| **Phase 7** | **Comorbidity Absorption** | `clinical.comorbidities`, `final_package_set` | `comorbidity_notes` (`list[str]`) |
| **Phase 8** | **Special Populations Routing**| `patient`, `final_package_set`, `hospital` | Appends advisory flags |
| **Phase 9** | **Document Gap Analysis** | `final_package_set`, `clinical`, `hospital`, `mlc_required`, `flags` | `preauth_docs_required`, `preauth_docs_missing`, `query_predictions` (`list[PackageQueryPrediction]`) |
| **Phase 10**| **Output Assembly** | All accumulated session state (Read-Only) | Does not write back; returns `IRISOutput` |

#### Early Exits and Routing Pathways

```mermaid
flowchart TD
    Start([Clinical / Admit Input JSON]) --> P0[Phase 0: Preflight Gates]
    P0 --> C0{Block Flag?}
    C0 -- Yes --> P10[Phase 10: Output Assembly]
    C0 -- No --> P1[Phase 1: Emergency Routing]
    P1 --> C1{Block Flag?}
    C1 -- Yes --> P10
    C1 -- No --> P2[Phase 2: Candidate Generation]
    P2 --> C2{Block Flag?}
    C2 -- Yes --> P10
    C2 -- No --> P3[Phase 3: Per-Candidate Validation]
    P3 --> C3{Block Flag?}
    C3 -- Yes --> P10
    C3 -- No --> C3_Empty{Any Validated Packages?}
    
    C3_Empty -- No (USP Path) --> USP_Set[Set usp_recommended=True & add USP_RECOMMENDED flag]
    USP_Set --> P9[Phase 9: Document Gap Analysis]
    
    C3_Empty -- Yes (Standard Path) --> P4[Phase 4: Multi-Package Combinations]
    P4 --> P5[Phase 5: Financial Wallet Check]
    P5 --> P6[Phase 6: Clinical Exclusions Check]
    P6 --> P7[Phase 7: Comorbidity Absorption]
    P7 --> P8[Phase 8: Special Populations Routing]
    P7 --> P9
    
    P9 --> P10
    P10 --> End([IRIS Structured Output JSON])
```

1. **Early Exit (Block Flag Check):** After Phase 0, Phase 1, Phase 2, and Phase 3, the orchestrator checks `session.has_block_flag()`. If any flag with `severity == "block"` is present, the pipeline immediately halts execution, skips all remaining phases, and jumps to Phase 10 (Output Assembly) to return a `BLOCKED` status.
2. **Unspecified Surgical Package (USP) Pathway:** After Phase 3, if `session.validated_packages` is empty (meaning no candidate package passed clinical validation/STG gates), the orchestrator initiates the USP pathway:
   - `session.usp_recommended` is set to `True`.
   - The warning flag `USP_RECOMMENDED` is added to the session.
   - Phases 4 through 8 are completely bypassed.
   - The pipeline executes Phase 9 (Document Gap Analysis) and Phase 10 (Output Assembly) directly.

---

### Stage 3: Claims Verification Pipeline (Phase 11)

Implemented in `phases/phase11_claim.py` and run via `main_claim.py`, the claims verification pipeline implements a 13-step verification process to compare actual discharge data against the approved pre-authorization baseline:

*   **Step 0: Cross-consistency checking:** Validates identity mismatch checks (patient PMJAY ID, admitting hospital, treating doctor registration number) and package integrity (comparing pre-auth validated `procedure_code` against discharge `admission.package_booked`). Also performs an LLM-based clinical consistency check to alert on diagnosis or admission type inconsistencies (e.g. elective pre-auth vs. emergency discharge).
*   **Step 1: Claim context loading:** Reads the pre-auth output dict, checks for selected packages, and pulls active STG details and specialty HBP shards.
*   **Step 2: Discharge summary completeness check:** Evaluates the discharge summary against CAM Annexure 6 criteria for fields (patient/hospital identifiers, treating consultant, diagnosis, procedure, treatment details) and signatures (consultant, PMAM, patient/attendant).
*   **Step 3: Required claim documents list building:** Compiles mandatory documents from STGs, HBP shards (as fallback if STG is missing), implant invoices (if required), and mortality audit reports (if death occurs within 24 hours). Resolves duplicate documents using a bidirectional alias-to-canonical checks dictionary (`EQUIVALENT_KEYS`).
*   **Step 4: Length of Stay (LoS) check:** Computes actual LoS vs. indicative LoS. Checks if an approved enhancement plan was filed for prolonged stays.
*   **Step 5: Deviation detection:** Flags anomalies including procedure code changes, ward category upgrades/downgrades, doctor registration mismatches, LoS excesses with no enhancement, and urology minor procedure sub-inclusions.
*   **Step 6: LLM Evaluation (CPD Checker):** Calls Gemini to evaluate actual findings against the CPD claim checklist and drafts clinical justifications for any detected deviations.
*   **Step 7: Special payments calculation:** Applies partial billing calculations for LAMA/DAMA, death (on-table, within 24h, or post-operative), and referral cases based on per-day bed category rates and HBP guidelines.
*   **Step 8: Audit flag triggering:** Triggers flags for stable-in-ICU, prolonged stay, billed days exceeding stay, missing STG documents, unspecified package abuse, and patient cash collection.
*   **Step 9: SHA notification date check:** Verifies if the hospital notified the State Health Authority within 24 hours of LAMA, referral, or death.
*   **Step 10: Specialty guidelines check:** Applies rules like BM (Burns management) follow-up photo frequency (days 5, 10, 15, 20) and MC/SV (Cardiology) stent carton sticker verification.
*   **Step 11: Claim status mapping:** Assigns the final claim routing state (`CLAIM_BLOCKED`, `CLAIM_DEVIATION`, `CLAIM_GAPS`, or `CLAIM_READY`).
*   **Step 12: Final Assembly:** Packs findings and returns `IRISClaimOutput`.

---

## 2. File Structure & Module Purposes

```
e:\Code\1hat-phase1\
│
├── config.py                     # Global constants, paths, thresholds, and search mode
├── logger_setup.py               # Standardized stdout console logging utility
├── input_validator.py            # Input JSON schema validation rules (stub returning True)
├── models.py                     # Unified data models and dataclass definitions
├── session.py                    # Session state class (IRISSession) for pipeline execution
├── main.py                       # Pre-authorisation pipeline entry point and orchestrator
├── main_claim.py                 # Claims verification pipeline entry point and orchestrator
├── app.py                        # Streamlit web dashboard with Tab 1 (Pre-auth), Tab 2 (Claims), Tab 3 (About)
├── eval.py                       # Subprocess test suite running inputs against expected_output.json
│
├── kb/
│   ├── __init__.py               # Package marker
│   ├── loader.py                 # Shard-level JSON file loaders and caches (using @lru_cache)
│   ├── searcher.py               # Fuzzy candidate selection using RapidFuzz token_set_ratio
│   ├── searcher_llm.py           # Gemini-based candidate selection
│   └── searcher_router.py        # Routes search queries to fuzzy or llm backend based on config
│
├── llm/
│   ├── __init__.py               # Package marker
│   ├── conflict_resolver.py      # LLM check for mutual exclusions and sub-inclusions in Phase 4
│   ├── cpd_evaluator.py          # LLM evaluation of CPD checklists and deviation justifications
│   ├── nearest_match.py          # LLM identification of closest blocked candidate post-failure
│   ├── query_predictor.py        # LLM query predictor checking qualification, vitals, and STGs
│   └── stg_checker.py            # LLM check for STG criteria, plausibility, and stratum ties
│
├── phases/
│   ├── __init__.py               # Package marker
│   ├── phase0_preflight.py       # Patient eligibility (BIS) and hospital check (HEM)
│   ├── phase1_emergency.py       # Emergency package check (stubbed for elective cases)
│   ├── phase2_candidates.py      # Candidate generation orchestration
│   ├── phase3_validator.py       # Validation logic: rules, STGs, implants, and tiebreakers
│   ├── phase4_multipackage.py    # PM-JAY package combinations and sliding-scale deductions
│   ├── phase5_financial.py       # Wallet balance sufficiency check and Vay Vandana allocation
│   ├── phase6_exclusion.py       # Keyword screen and LLM exception engine for exclusions
│   ├── phase7_comorbidity.py     # Management conditions comorbidity absorption
│   ├── phase8_special_pop.py     # Specialty advisory routing (neonatal, oncology, transplant, etc.)
│   ├── phase9_documents.py       # Document checklist compilation and gap checker
│   ├── phase10_output.py         # Readiness status calculation and output serialization
│   └── phase11_claim.py          # Claims verification logic (13-step verification)
│
├── stubs/
│   ├── __init__.py               # Package marker
│   ├── bis_stub.py               # Mock client reading dummy_bis.json for patient details
│   └── hem_stub.py               # Mock hospital empanelment stub reading dummy_hem.json
│
├── intake/
│   ├── __init__.py               # Package marker
│   ├── intake_runner.py          # Folder scanning, text extraction, parser orchestration, schema validation
│   ├── pdf_extractor.py          # Text extraction from PDF using pdfplumber, falling back to Tesseract OCR
│   ├── docx_extractor.py         # Text extraction from DOCX paragraphs and tables using python-docx
│   ├── discharge_parser.py       # Gemini-based unstructured medical text to schema mapping parser
│   └── schema_validator.py       # Enforces hard-required patient, admission, and clinical fields
│
└── data/
    ├── KB_SPEC.md                # Specifications for knowledge base structures
    ├── dummy/
    │   ├── dummy_bis.json        # Mock patient database (identities, wallets, and past claims)
    │   └── dummy_hem.json        # Mock hospital database (empanelments and specialties)
    ├── schemes/
    │   └── pmjay.json            # PM-JAY core configuration master (KB-1)
    ├── hbp/
    │   ├── _index.json           # Flat index of all procedure codes across specialties (KB-2)
    │   └── <specialty>.json      # Category package master shards (KB-2)
    ├── stg/
    │   └── <procedure_code>.json # Standard Treatment Guideline json files (KB-3)
    └── samples/
        └── query_taxonomy.json   # Standardized query and rejection taxonomy placeholder (KB-4)
```

---

## 3. Data Models (`models.py`)

IRIS defines the following dataclasses to represent structures throughout the pipelines:

```python
@dataclass
class WalletBalance:
    family_balance_inr: int
    vay_vandana_balance_inr: int | None
    policy_year_start: str

@dataclass
class PastClaim:
    procedure_code: str
    admission_date: str
    package_amount_inr: int
    status: str

@dataclass
class PatientContext:
    patient_id: str
    family_id: str
    name: str
    age: int
    gender: str
    home_state: str
    home_district: str
    wallet: WalletBalance
    past_claims: list[PastClaim] = field(default_factory=list)

@dataclass
class HospitalContext:
    hospital_id: str
    name: str
    type: str
    city_tier: str
    state: str
    district: str
    is_aspirational_district: bool
    accreditation: str
    scheme: str
    empanelled_specialties: list[str]

@dataclass
class StructuredValue:
    parameter: str
    value: float | str | None
    unit: str | None
    flag: str | None
    leads: str | None

@dataclass
class Investigation:
    type: str
    result_summary: str | None
    structured_values: list[StructuredValue] | None
    document_available: bool
    report_date: str | None

@dataclass
class DocumentInHand:
    key: str
    label: str
    available: bool

@dataclass
class ExaminationFindings:
    general: str | None
    cvs: str | None
    rs: str | None
    abdomen: str | None
    cns: str | None
    local: str | None

@dataclass
class PersonalHistory:
    smoking: str | None
    alcohol: str | None
    diet: str | None

@dataclass
class TreatingDoctor:
    name: str
    registration_number: str
    qualification: str
    specialty_code: str

@dataclass
class ClinicalInput:
    admission_date: str | None
    bed_category: str | None
    is_emergency: bool
    is_medico_legal: bool
    chief_complaints: str
    duration_days: int
    history_of_present_illness: str | None
    provisional_diagnosis: str
    planned_procedure: str | None
    weight_kg: float | None
    height_cm: float | None
    vitals: dict
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

@dataclass
class CandidatePackage:
    procedure_code: str
    package_code: str
    specialty_code: str
    specialty: str
    package_name: str
    procedure_name: str
    billing_unit: str
    reserved_public_only: bool
    procedure_label: str
    auto_approved: str
    day_care: bool
    base_rate_inr: int | None
    match_score: float

@dataclass
class StratificationResult:
    determinable: bool
    selected_stratum: str | None
    note: str | None

@dataclass
class ImplantResult:
    required: bool
    name: str | None
    cost_inr: int | None
    age_appropriate: bool
    gender_appropriate: bool
    quantity: int | None

@dataclass
class ValidatedPackage:
    procedure_code: str
    package_code: str
    specialty_code: str
    package_name: str
    procedure_name: str
    billing_type: str
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
    stg_reasoning: str | None = None
    is_addon_to: list[str] | None = None
    addon_type: str | None = None
    match_score: float = 0.0
    flags: list[str] = field(default_factory=list)

@dataclass
class FinalPackage:
    validated: ValidatedPackage
    role: str
    deduction_factor: float
    pre_auth_group: int

@dataclass
class DocumentItem:
    key: str
    label: str
    package_code: str | None
    available: bool
    criticality: str

@dataclass
class Flag:
    code: str
    message: str
    severity: str

@dataclass
class EnhancementPlan:
    procedure_code: str
    estimated_requests: int
    batch_size_used: int
    los_indicative_days: int
    caveat: str

@dataclass
class ChecklistItemResult:
    question: str
    expected: bool
    actual: bool | None
    risk_level: str
    reasoning: str

@dataclass
class CommonQueryRisk:
    query_text: str
    risk_level: str
    reasoning: str

@dataclass
class PackageQueryPrediction:
    procedure_code: str
    package_name: str
    readiness_verdict: str
    verdict_summary: str
    checklist_results: list[ChecklistItemResult]
    common_query_risks: list[CommonQueryRisk]
    advisory_claim_docs: list[dict]
    llm_evaluation_status: str = "unknown"

@dataclass
class ClaimDocumentItem:
    key: str
    label: str
    package_code: str | None
    available: bool
    criticality: str
    notes: str | None

@dataclass
class DeviationItem:
    deviation_type: str
    description: str
    from_value: str
    to_value: str
    severity: str
    justification_draft: str | None
    justification_required: bool

@dataclass
class CPDChecklistResult:
    question: str
    expected: bool
    actual: bool | None
    risk_level: str
    reasoning: str

@dataclass
class SpecialPaymentResult:
    trigger: str
    base_package_rate_inr: int
    payable_amount_inr: int
    payable_percentage: int
    computation_note: str

@dataclass
class IRISClaimOutput:
    claim_status: str
    procedure_code: str
    package_name: str
    preauth_reference: str
    claim_docs_required: list[ClaimDocumentItem] = field(default_factory=list)
    claim_docs_missing: list[ClaimDocumentItem] = field(default_factory=list)
    image_docs_reminder: list[str] = field(default_factory=list)
    cpd_checklist_results: list[CPDChecklistResult] = field(default_factory=list)
    cpd_verdict: str = "unknown"
    cpd_verdict_summary: str = ""
    llm_evaluation_status: str = "unknown"
    deviations_detected: list[DeviationItem] = field(default_factory=list)
    deviation_justifications_drafted: int = 0
    los_approved_indicative: int = 0
    los_actual: int = 0
    los_deviation: bool = False
    los_deviation_note: str | None = None
    discharge_summary_complete: bool = False
    discharge_summary_missing_fields: list[str] = field(default_factory=list)
    special_payment: SpecialPaymentResult | None = None
    audit_flags_triggered: list[str] = field(default_factory=list)
    sha_notification_warning: str | None = None
    specialty_specific_notes: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

@dataclass
class IRISOutput:
    readiness_status: str
    selected_packages: list[FinalPackage] = field(default_factory=list)
    blocked_candidates: list[dict] = field(default_factory=list)
    preauth_docs_required: list[DocumentItem] = field(default_factory=list)
    preauth_docs_missing: list[DocumentItem] = field(default_factory=list)
    query_predictions: list[PackageQueryPrediction] = field(default_factory=list)
    enhancement_plan: list[EnhancementPlan] = field(default_factory=list)
    copayment_required: bool = False
    copayment_gap_inr: int | None = None
    comorbidity_notes: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    stg_coverage: dict = field(default_factory=lambda: {"validated": 0, "stg_missing": 0})
    errors: list[str] = field(default_factory=list)
```

---

## 4. Knowledge Base (KB) Architecture

IRIS partitions references and catalog data into 5 separate tiers:

*   **KB-1: Core Scheme Rules**
    *   *Source file:* `data/schemes/pmjay.json`
    *   *Purpose:* Declares schema parameters (limits, multipliers, SLAs, copayments, accreditation markups).
    *   *Status:* **Active**.
*   **KB-2: Specialty Shards & Derived Index**
    *   *Source files:* `data/hbp/` directory.
    *   *Purpose:* Houses category package masters (e.g. `data/hbp/Cardiology.json`) and flat catalog indexes (`data/hbp/_index.json`).
    *   *Status:* **Active**.
*   **KB-3: Standard Treatment Guidelines**
    *   *Source files:* `data/stg/<procedure_code>.json`
    *   *Purpose:* Declares mandatory document checklists, indicative LoS (ALOS), clinical check variables, and doctor qualifications.
    *   *Status:* **Active**.
*   **KB-4: Rejection & Query Taxonomy**
    *   *Source file:* `data/query_taxonomy.json` (Placeholder fallback at `data/samples/query_taxonomy.json`).
    *   *Purpose:* Standardized rejection codes and auditor query taxonomy.
    *   *Status:* **Missing** (Loads the samples fallback).
*   **KB-5: State-Specific Overrides**
    *   *Source file:* `data/schemes/cmchis.json`.
    *   *Purpose:* Tamil Nadu state overrides (CMCHIS).
    *   *Status:* **Not Started**.

---

## 5. LLM Integration Policy

IRIS integrates Large Language Models (LLMs) selectively for clinical reasoning. The system runs a **fail-open** policy: if any LLM call fails due to API limits or timeouts, the engine registers a warning flag and degrades to a safe, deterministic default.

*   **Model:** `gemini-2.5-flash`
*   **Temperature:** `0` (enforces strict determinism and consistency across runs)
*   **Timeouts:** Standard Calls: `30` seconds | Query Predictor / CPD Evaluator: `90` seconds
*   **Retries:** `2` attempts for all calls

### Primary LLM Functions and Fallbacks

1.  **`kb/searcher_llm.py::search_candidates`**
    *   *Trigger:* Phase 2 Candidate Generation when `PHASE2_SEARCH_MODE == "llm"`.
    *   *Task:* Parses unstructured patient text to map to a list of candidate procedure codes.
    *   *Fallback:* Returns empty list `[]` (falls back to USP pathway) or falls back to fuzzy matching.
2.  **`llm/stg_checker.py::check_stg_eligible`**
    *   *Trigger:* Phase 3 STG validation when the matching STG JSON is present.
    *   *Task:* Evaluates whether clinical findings satisfy all mandatory STG parameters.
    *   *Fallback:* Marks patient as `eligible = True` with `confidence = "low"`, logging a warning.
3.  **`llm/stg_checker.py::check_plausibility`**
    *   *Trigger:* Phase 3 validation when the STG JSON file is missing.
    *   *Task:* Conducts a lightweight clinical plausibility check to verify code relevance.
    *   *Fallback:* Marks the candidate as `plausible = True`.
4.  **`llm/stg_checker.py::resolve_stratum`**
    *   *Trigger:* Phase 3 duplicate resolution for overlapping candidate codes.
    *   *Task:* Analyzes planned approach and clinical variables to choose the single best-fit variant.
    *   *Fallback:* Selects the variant with the highest fuzzy match score (`fuzz.WRatio`).
5.  **`llm/conflict_resolver.py::resolve_conflicts`**
    *   *Trigger:* Phase 4 Multi-package Combination start.
    *   *Task:* Scans validated packages to drop mutual exclusions or sub-included procedures.
    *   *Fallback:* Retains all validated packages.
6.  **`phases/phase6_exclusion.py::_check_exclusion_with_llm`**
    *   *Trigger:* Phase 6 for Group A clinical exclusions (Dental, Cosmetic, Drug rehab).
    *   *Task:* Assesses if clinical details warrant an exception under Annexure 5 rules.
    *   *Fallback:* Retains the packages and appends a warning flag for manual audit review.
7.  **`llm/query_predictor.py::predict_package_queries`**
    *   *Trigger:* Phase 9 Document Gap Analysis.
    *   *Task:* Predicts likelihood of PPD claim queries based on qualifications, vitals, and STG gaps.
    *   *Fallback:* Returns readiness verdict as `"ready"` or `"unknown"` with `"skipped"/"failed"` status.
8.  **`llm/nearest_match.py::get_nearest_match`**
    *   *Trigger:* Orchestrator (`main.py`) when zero packages survive validation.
    *   *Task:* Analyzes blocked candidate logs and identifies the closest package and its main deficit.
    *   *Fallback:* Returns `None`.
9.  **`llm/cpd_evaluator.py::evaluate_claim_with_cpd`**
    *   *Trigger:* Phase 11 Claims Verification.
    *   *Task:* Verifies discharge details against the CPD checklist and drafts justifications for deviations.
    *   *Fallback:* Returns empty checklist results, original deviations, and status `"failed"`.
10. **`llm/cpd_evaluator.py::check_clinical_consistency`**
    *   *Trigger:* Phase 11 Claims Verification Step 0.
    *   *Task:* Assesses whether pre-auth diagnosis and procedures match clinical records at discharge.
    *   *Fallback:* Returns `[]` (empty consistency issues list).

---

## 6. Business Flags & Reason Codes

IRIS records pipeline events as **Business Flags** (user-facing, session-level) or **Reason Codes** (reasons why candidate packages were rejected in Phase 3).

### Business Flag Codes

| Flag Code | Severity | Source Phase | Description / Trigger |
| :--- | :--- | :--- | :--- |
| **`PREFLIGHT_FAILED`** | `block` | Phase 0 | Exception during BIS or HEM stub check |
| **`PATIENT_NOT_IN_BIS`** | `block` | Phase 0 | Patient ID is not present in BIS database |
| **`SCHEME_NOT_SUPPORTED`** | `block` | Phase 0 | Admitting hospital scheme is not `"pmjay"` |
| **`EMERGENCY_PHASE_STUBBED`** | `info` | Phase 1 | Informational flag indicating emergency routing is stubbed |
| **`CANDIDATES_GENERATED`** | `info` | Phase 2 | Shortlist generated from catalog index |
| **`NO_CANDIDATES_FOUND`** | `warning` | Phase 2 | Fuzzy or LLM search yielded zero matching candidates |
| **`CANDIDATE_GENERATION_FAILED`** | `block` | Phase 2 | Exception caught during candidate search |
| **`NO_VALIDATED_PACKAGES`** | `warning` | Phase 3 | All candidate packages were blocked in validation |
| **`USP_RECOMMENDED`** | `warning` | `main.py` | USP referral pathway recommended |
| **`CONFLICT_RESOLVED`** | `info` | Phase 4 | Conflicting package(s) dropped by LLM Conflict Resolver |
| **`SURGICAL_PERDAY_BLOCKED`** | `warning` | Phase 4 | Medical per-day package dropped due to surgical presence |
| **`PERDAY_MULTIPLE_BLOCKED`** | `warning` | Phase 4 | Extra medical per-day package dropped (only 1 allowed) |
| **`STANDALONE_SPLIT`** | `info` | Phase 4 | Standalone packages split into `pre_auth_group=2` |
| **`ADDON_PARENT_UNKNOWN`** | `warning` | Phase 4 | Dropped add-on package lacking parent mappings |
| **`ADDON_PARENT_MISSING`** | `warning` | Phase 4 | Dropped add-on because parents are absent in selection set |
| **`DIAGNOSTIC_ADDON_BLOCKED`** | `warning` | Phase 4 | Dropped high-end diagnostic add-on due to missing per-day primary |
| **`DEDUCTION_APPROXIMATE`** | `info` | Phase 4 | Deduction ordering uses base rate as proxy for final rates |
| **`RATE_NULL_FOR_PERDAY`** | `info` | Phase 5 | Excluded per-day packages lacking rates from cost estimate |
| **`VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS`** | `warning` | Phase 5 | Dual-wallet senior citizen; debit order requires clarification |
| **`WALLET_INSUFFICIENT`** | `warning` | Phase 5 | Total package cost exceeds available balance |
| **`FINANCIAL_ESTIMATE_APPROXIMATE`**| `info` | Phase 5 | Financial estimate excludes HBP modifiers |
| **`EXCLUSION_<CAT>_RISK`** | `warning` | Phase 6 | Keywords trigger risk of specific exclusion category |
| **`EXCLUSION_<CAT>_RISK_BLOCKED`**| `block` | Phase 6 | Group A exclusion verified with no exceptions; package dropped |
| **`COMORBIDITY_REVIEW_NEEDED`** | `info` | Phase 7 | Non-standard comorbidity found; needs manual review |
| **`NEONATAL_ESCALATION_RISK`** | `warning` | Phase 8 | Neonatal patient (age 0); monitoring required |
| **`PAEDIATRIC_DEVICE`** | `info` | Phase 8 | Paediatric patient (age ≤ 14); pediatric sizing applies |
| **`MTB_REQUIRED`** | `warning` | Phase 8 | Oncology procedure; requires Tumour Board approval |
| **`ONCOLOGY_MULTI_STAGE`** | `info` | Phase 8 | Oncology patient; current stage evaluated only |
| **`PORTABILITY_CASE`** | `info` | Phase 8 | Portability alert; longer TAT applies |
| **`NOTTO_DOCS_REQUIRED`** | `warning` | Phase 8 | Transplant procedure; NOTTO IDs required |
| **`DOC_GAP_ANALYSIS`** | `info` | Phase 9 | Informational checklist execution details |
| **`MANDATORY_DOCS_MISSING`** | `warning` | Phase 9 | One or more hard-block documents are missing |
| **`IDENTITY_MISMATCH_PMJAY_ID`** | `block` | Phase 11 | PMJAY ID mismatch between pre-auth approved baseline and discharge JSON |
| **`IDENTITY_MISMATCH_HOSPITAL`** | `block` | Phase 11 | Hospital code mismatch between pre-auth approved baseline and discharge JSON |
| **`IDENTITY_MISMATCH_DOCTOR_REG`** | `block` | Phase 11 | Doctor medical registration mismatch between pre-auth approved baseline and discharge JSON |
| **`PROCEDURE_MISMATCH`** | `block` | Phase 11 | Pre-auth approved procedure code differs from actual discharge procedure code |
| **`CLINICAL_CONSISTENCY_WARNING`** | `warning` | Phase 11 | Diagnosis, admission type or related clinical parameters are inconsistent |

### Blocked Candidate Reason Codes

These reasons are written to `session.phase3_blocked` under the `reason_code` key:

*   **`SPECIALTY_CODE_UNKNOWN`**: Candidate specialty code has no shard mapping.
*   **`SHARD_NOT_FOUND`**: HBP specialty shard JSON file not found in `data/hbp/`.
*   **`PROCEDURE_NOT_IN_SHARD`**: Procedure code not present in specialty HBP shard.
*   **`PUB_RESERVED_BLOCK`**: Public-only reserved package booked by a private hospital.
*   **`STG_REQUIRED`**: STG file missing when `REQUIRE_STG_FOR_VALIDATION` is `True`.
*   **`PLAUSIBILITY_FAILED`**: Failed LLM clinical plausibility check.
*   **`STG_NOT_ELIGIBLE`**: Guidelines specified in the STG JSON were not met.
*   **`STRATUM_NOT_SELECTED`**: Duplicate package variant dropped by stratum tiebreaker.
*   **`INTERNAL_ERROR`**: Unhandled exception caught during candidate validation.

---

## 7. Configuration Parameters (`config.py`)

All variables in `config.py` control the engine's core parameters:

*   **`PROJECT_ROOT` / `DATA_DIR` / `HBP_DIR` / `STG_DIR` / `SCHEMES_DIR` / `DUMMY_DIR`**: Path objects pointing to package masters, STGs, and dummy databases.
*   **`INDEX_FILE`**: Pointer to the flat HBP specialty index file (`data/hbp/_index.json`).
*   **`PMJAY_RULES_FILE`**: Core scheme master path (`data/schemes/pmjay.json`).
*   **`QUERY_TAXONOMY_FILE`**: Rejection taxonomy path (`data/query_taxonomy.json`).
*   **`DUMMY_BIS_FILE` / `DUMMY_HEM_FILE`**: Mock data pointers for BIS and HEM databases.
*   **`TOP_N_CANDIDATES`** (`30`): Maximum number of search candidates passed to Phase 3.
*   **`MIN_FUZZY_SCORE`** (`50`): Minimum RapidFuzz match score to retain a candidate.
*   **`ENHANCEMENT_BATCH_PRIVATE`** (`2`): Extension batch size (days) for private hospitals.
*   **`ENHANCEMENT_BATCH_PUBLIC`** (`5`): Extension batch size (days) for public or NE-region hospitals.
*   **`NE_STATES_AND_ISLANDS`**: Regions qualifying for the public-tier enhancement batch size.
*   **`REQUIRE_STG_FOR_VALIDATION`** (`False`): If `True`, missing STGs block candidates; if `False`, falls back to clinical plausibility check.
*   **`LLM_MODEL`** (`"gemini-2.5-flash"`): Gemini model ID used for all pipeline reasoning.
*   **`LLM_TIMEOUT_SECONDS`** (`30`): Timeout for standard LLM calls.
*   **`QUERY_PREDICTOR_TIMEOUT_SECONDS`** (`90`): Timeout for query predictor / CPD evaluator calls.
*   **`LLM_MAX_RETRIES`** (`2`): Retry limit for transient LLM API issues.
*   **`SENIOR_CITIZEN_AGE`** (`70`): Age threshold triggering Vay Vandana wallet balance rules.
*   **`PAEDIATRIC_AGE_MAX`** (`14`): Age limit for pediatric implant guidelines.
*   **`FAMILY_WALLET_DEFAULT_INR`** (`500000`): Default family wallet limit.
*   **`VAY_VANDANA_WALLET_INR`** (`500000`): Senior citizen top-up limit.
*   **`LOG_LEVEL`** (`"INFO"`) & **`LOG_FORMAT`**: Python logging configurations.
*   **`PHASE2_SEARCH_MODE`** (`"llm"`): Switch between `"fuzzy"` (RapidFuzz) and `"llm"` (Gemini search).

---

## 8. Known Gaps & Stubs

The following segments are stubbed, missing database mappings, or have placeholder implementations:

1.  **Phase 1 (Emergency Routing):** Completely stubbed. Always assumes planned elective admissions (`is_emergency = False`, `needs_specialty_package = True`).
2.  **KB-4 (Query / Deduction Taxonomy):** The production `data/query_taxonomy.json` file is missing. The loader falls back to `data/samples/query_taxonomy.json`.
3.  **KB-5 (State Overrides CMCHIS):** Tamil Nadu state overrides (`cmchis.json`) are not started.
4.  **Phase 4 Deduction Factors Ordering:** Ordering is based on base rates. PM-JAY combination rules require ordering by final calculated rates (multipliers are only resolved in Phase 5).
5.  **Phase 8 Paediatric Device Limits:** Sizing checks are advisory alerts; they do not perform physical range validations on implants.
6.  **LoS Persistence in EnhancementPlan:** `ValidatedPackage` does not persist the raw `los_indicative` integer used during Phase 3. The `los_indicative_days` field in `EnhancementPlan` is returned as a placeholder `0`.
7.  **HEM stub empanelment:** HEM stub in Phase 0 assumes all queried hospitals are empanelled.
8.  **Input Validator (`input_validator.py`):** Structured validation is stubbed; always returns `(True, [])`.
