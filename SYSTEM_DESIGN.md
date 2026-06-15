# IRIS System Design — PM-JAY Pre-Authorisation Engine

> **Source of truth:** This document is generated from the actual codebase. Where code and older documentation conflict, the code wins.

---

## 1. What IRIS Does

IRIS is a clinical pre-authorisation decision engine for India's national health assurance scheme (**PM-JAY**). Given a patient's clinical presentation, home state, home district, wallet history, and admitting hospital profile, IRIS executes an 11-phase deterministic pipeline to select valid health benefit package (HBP) procedure codes, verify clinical eligibility against Standard Treatment Guidelines (STGs) using Gemini LLM, enforce combination rules, check document completeness, and output a structured readiness status:
- **`READY`**: Clean pre-auth request, meets all rules and clinical guidelines, no document gaps.
- **`READY_WITH_WARNINGS`**: Eligible, but raises clinical, financial, or administrative warning flags that require attention.
- **`CONDITIONAL`**: Missing non-blocking supporting documentation that PPD (Pre-Auth Reviewer) is likely to query.
- **`BLOCKED`**: Hard rule violation, fails clinical guideline check, or missing critical required documents (results in immediate rejection or block).

---

## 2. Pipeline Overview (Phases 0–10)

The pipeline executes sequentially in `main.py` using `run_pipeline()`. It passes a central `IRISSession` object through all phases.

```
                    Input JSON
                        │
                        ▼
Phase 0 — Preflight Gates       [Read: patient_id, hospital_id, is_medico_legal]
                        │       [Write: patient, hospital, patient_eligible, hospital_empanelled, mlc_required]
                        ▼
Phase 1 — Emergency Routing     [STUBBED — Always sets is_emergency=False]
                        │       [Write: is_emergency, er_package_code, needs_specialty_package]
                        ▼
Phase 2 — Candidate Generation  [Read: clinical, empanelled_specialties, type]
                        │       [Write: candidate_packages] (Fuzzy vs LLM Searcher)
                        ▼
Phase 3 — Per-Package Validator [Read: candidate_packages, hospital, patient, clinical]
                        │       [Write: validated_packages, phase3_blocked, stg_coverage]
                        ▼
Phase 4 — Multi-Package Rules   [Read: validated_packages]
                        │       [Write: final_package_set] (Deduction factors/combination rules)
                        ▼
Phase 5 — Wallet Sufficiency    [Read: final_package_set, patient]
                        │       [Write: estimated_total_inr, wallet_sufficient, copayment_required, copayment_gap_inr]
                        ▼
Phase 6 — Exclusion Check       [Read: clinical text (complaints, diagnosis, history, notes)]
                        │       [Write: flags (OPD, cosmetic, dental, etc.)]
                        ▼
Phase 7 — Comorbidity Resolution[Read: comorbidities, final_package_set]
                        │       [Write: comorbidity_notes, flags]
                        ▼
Phase 8 — Special Populations   [Read: patient, final_package_set, hospital]
                        │       [Write: flags (paediatric, neonate, oncology, transplant, portability)]
                        ▼
Phase 9 — Document Gap Analysis [Read: final_package_set, clinical, hospital, mlc_required, flags]
                        │       [Write: preauth_docs_required, preauth_docs_missing]
                        ▼
Phase 10 — Output Assembly      [Read: all session fields]
                        │       [Write: IRISOutput]
                        ▼
                   IRISOutput
```

### Early-Exit / Skip Rules:
1. **Block Early Exit:** After each of Phases 0–3, `session.has_block_flag()` is checked. If any block-severity flag is present, the pipeline immediately jumps to Phase 10 (Output Assembly), skipping all intermediate phases.
2. **USP Jump:** After Phase 3, if `session.validated_packages` is empty and no block flags exist, the engine triggers the **Unspecified Surgical Package (USP)** path. It sets `session.usp_recommended = True`, emits the `USP_RECOMMENDED` warning flag, skips Phases 4–8 entirely, and jumps directly to Phase 9 (Document Gap Analysis) then Phase 10.

---

## 3. Complete File Structure

- **`main.py`**: CLI entry point and pipeline orchestrator. Contains `run_pipeline()` that coordinates Phase 0 through Phase 10, handles early exits, catches errors, and prints serialized output to stdout.
- **`config.py`**: Central repository for all static parameters, file paths, fuzzy thresholds, default age limits, wallet ceilings, LLM timeout/retry settings, and the Phase 2 search mode router.
- **`session.py`**: Declares the `@dataclass IRISSession` state object. Serves as the single mutable thread passing through all phases, tracking state variables, accumulated warnings, errors, and validation results.
- **`models.py`**: Contains 20 plain dataclasses (no Pydantic dependencies) with absolute type annotations that represent the structured clinical input, packages, documents, flags, and outputs.
- **`input_validator.py`**: Stubs schema validation on raw input payloads; currently always returns `(True, [])`.
- **`logger_setup.py`**: Configures root logging format and sends logs to stderr/stdout.
- **`requirements.txt`**: Standard dependencies list (`google-genai`, `rapidfuzz`, `python-dotenv`).
- **`iris_input_schema_v2.json`**: JSON Schema validating the structure of raw clinical pre-auth requests.
- **`app.py`**: Streamlit developer console for selecting pre-built JSON test cases, editing input parameters, and visualizing pipeline outputs, logs, and financial details.
- **`stubs/`**: Mock external systems APIs:
  - **`bis_stub.py`**: Stub for Beneficiary Identification System. Searches `dummy_bis.json` by `patient_id` to retrieve profile details, family IDs, wallet balances, and claim histories.
  - **`hem_stub.py`**: Stub for Hospital Empanelment Module. Searches `dummy_hem.json` to verify empanelment status, tier, state, district, and empanelled specialty codes.
- **`kb/`**: Knowledge base retrieval and search logic:
  - **`loader.py`**: Handles low-level cached I/O for `_index.json`, specialty shards, STGs, scheme rules, and query taxonomies.
  - **`searcher.py`**: Fuzzy candidate searcher using `rapidfuzz.fuzz.token_set_ratio` against the index aliases.
  - **`searcher_llm.py`**: LLM-based candidate searcher querying Gemini with the patient presentation and compact index.
  - **`searcher_router.py`**: Delegates candidate search requests to either the fuzzy or LLM backend depending on `config.PHASE2_SEARCH_MODE`.
- **`llm/`**: LLM gateway:
  - **`stg_checker.py`**: Houses Gemini API templates and parsers for STG eligibility check, plausibility checks (when STG is missing), and same-package stratum resolution.
- **`phases/`**: Modular implementations of the 11 pipeline phases:
  - **`phase0_preflight.py`**: Verifies patient eligibility, hospital empanelment, and scheme compatibility.
  - **`phase1_emergency.py`**: Stubbed phase; routes patient to emergency packages (always elective in MVP).
  - **`phase2_candidates.py`**: Triggers Phase 2 search backend and populates candidate shortlist.
  - **`phase3_validator.py`**: Evaluates public reservations, classifies billing types, makes LLM clinical guideline checks, determines stratification/implants, calculates enhancement requests, and resolves same-package duplicates.
  - **`phase4_multipackage.py`**: Applies multi-package rules: drops per-day packages if surgical/day-care exists, drops invalid add-ons, isolates standalones, and calculates deduction factors (100-50-25).
  - **`phase5_financial.py`**: Computes estimated total pre-auth cost, compares against family wallet balance, and handles senior citizen dual-wallets.
  - **`phase6_exclusion.py`**: Scans clinical text for Annexure 6 exclusion keyword matches.
  - **`phase7_comorbidity.py`**: Resolves comorbidity absorption rules (e.g. absorbing standard management conditions).
  - **`phase8_special_pop.py`**: Applies warning/info flags for neonates, paediatric devices, oncology, transplant, and portability.
  - **`phase9_documents.py`**: Performs document gap analysis by compiling universal, conditional, and per-package requirements.
  - **`phase10_output.py`**: Compiles final execution records and computes the overall `readiness_status`.

---

## 4. Data Models (`models.py`)

All models are plain Python `@dataclass` structures:

### 1. `WalletBalance`
- `family_balance_inr: int` — Active balance of the primary ₹5-lakh family wallet.
- `vay_vandana_balance_inr: int | None` — Senior citizen (age ≥70) card balance, or `None` if ineligible.
- `policy_year_start: str` — ISO date string of policy cycle start (e.g. `"2025-04-01"`).

### 2. `PastClaim`
- `procedure_code: str` — Procedure code claimed.
- `admission_date: str` — ISO admission date.
- `package_amount_inr: int` — Cost approved/claimed.
- `status: str` — Claim status (`"approved" | "rejected" | "pending"`).

### 3. `PatientContext`
- `patient_id: str` — Unique beneficiary ID.
- `family_id: str` — Family unit ID.
- `name: str` — Patient name.
- `age: int` — Patient age in years.
- `gender: str` — `"M" | "F"`.
- `home_state: str` — Home state of registration.
- `home_district: str` — Home district of registration.
- `wallet: WalletBalance` — Current wallet balances.
- `past_claims: list[PastClaim]` — Past claims list.

### 4. `HospitalContext`
- `hospital_id: str` — Unique hospital ID.
- `name: str` — Hospital name.
- `type: str` — `"private" | "public"`.
- `city_tier: str` — `"tier1" | "tier2" | "tier3"`.
- `state: str` — Hospital state.
- `district: str` — Hospital district.
- `is_aspirational_district: bool` — True if district is designated as aspirational by NITI Aayog.
- `accreditation: str` — `"none" | "bronze" | "nabh_entry" | "nabh_full" | "nqas"`.
- `scheme: str` — Underwriting scheme (must be `"pmjay"`).
- `empanelled_specialties: list[str]` — List of 2-letter specialty codes empanelled.

### 5. `StructuredValue`
- `parameter: str` — Parameter name (e.g. `"LVEF"`, `"Troponin_I"`).
- `value: float | str | None` — Extracted value.
- `unit: str | None` — Unit of measurement (e.g. `"%"`, `"ng/mL"`).
- `flag: str | None` — `"H" | "L" | "N" | None` (High, Low, Normal).
- `leads: str | None` — ECG leads involved (ECG only).

### 6. `Investigation`
- `type: str` — Category (e.g. `"ecg"`, `"echo"`, `"blood_reports"`, `"usg"`).
- `result_summary: str | None` — Freeform clinical summary of reports.
- `structured_values: list[StructuredValue] | None` — OCR-extracted key-value parameters.
- `document_available: bool` — True if report document is uploaded.
- `report_date: str | None` — ISO report date.

### 7. `DocumentInHand`
- `key: str` — Unique document key (e.g. `"clinical_notes"`, `"mlc_fir"`).
- `label: str` — Human-readable document name.
- `available: bool` — True if the document has been collected.

### 8. `ExaminationFindings`
- `general: str | None`, `cvs: str | None`, `rs: str | None`, `abdomen: str | None`, `cns: str | None`, `local: str | None` — Freeform observations.

### 9. `PersonalHistory`
- `smoking: str | None`, `alcohol: str | None`, `diet: str | None` — Lifestyle context.

### 10. `TreatingDoctor`
- `name: str` — Doctor name.
- `registration_number: str` — Medical registration number.
- `qualification: str` — Qualification text (e.g. `"MS General Surgery"`).
- `specialty_code: str` — Admitting specialty code (e.g. `"SG"`).

### 11. `ClinicalInput`
- `admission_date: str | None` — ISO admission date.
- `bed_category: str | None` — `"ward" | "hdu" | "icu_no_vent" | "icu_vent" | None`.
- `is_emergency: bool` — Emergency status.
- `is_medico_legal: bool` — Medico-legal case indicator.
- `chief_complaints: str` — Chief complaints.
- `duration_days: int` — Duration in days.
- `history_of_present_illness: str | None` — HPI narrative.
- `provisional_diagnosis: str` — Provisional diagnosis.
- `planned_procedure: str | None` — Planned procedure text.
- `weight_kg: float | None`, `height_cm: float | None` — Vitals height/weight.
- `vitals: dict` — Dict of vital values (bp_systolic_mmhg, pulse_bpm, spo2_pct, etc.).
- `examination_findings: ExaminationFindings | None` — Systemic examination details.
- `investigations: list[Investigation]` — Diagnostics.
- `comorbidities: list[str]` — List of comorbidities.
- `past_medical_history: str | None`, `past_surgical_history: str | None` — Past histories.
- `current_medications: list[str]`, `allergies: list[str]` — Medications & allergies.
- `personal_history: PersonalHistory | None` — Lifestyle history.
- `family_history: str | None` — Family history details.
- `non_clinical_documents_in_hand: list[DocumentInHand]` — Attached administrative files.
- `treating_doctor: TreatingDoctor | None` — Admitting physician.
- `notes: str | None` — General notes.

### 12. `CandidatePackage`
- `procedure_code: str`, `package_code: str`, `specialty_code: str`, `specialty: str`, `package_name: str`, `procedure_name: str`, `billing_unit: str`, `reserved_public_only: bool`, `procedure_label: str`, `auto_approved: str`, `day_care: bool`, `base_rate_inr: int | None` — Inherited from index.
- `match_score: float` — Index search relevance score.

### 13. `StratificationResult`
- `determinable: bool` — True if required clinical variables are present.
- `selected_stratum: str | None` — Selected stratum tier.
- `note: str | None` — Detail when stratification is indeterminate.

### 14. `ImplantResult`
- `required: bool` — True if procedure requires separate implant billing.
- `name: str | None` — Implant name.
- `cost_inr: int | None` — Implant base cost.
- `age_appropriate: bool` / `gender_appropriate: bool` — Placeholders for validation.
- `quantity: int | None` — Required units count.

### 15. `ValidatedPackage`
- Inherits all fields from `CandidatePackage`, plus:
- `billing_type: str` — classified into `"surgical" | "fixed_medical" | "per_day" | "day_care"`.
- `enhancement_applicable: bool` — True if LoS extensions apply.
- `enhancement_requests_needed: int | None` — Pre-calculated LoS request count.
- `stratification: StratificationResult` — Selected stratum.
- `implant: ImplantResult` — Implant details.
- `special_conditions_popup: bool` / `special_conditions_rule: bool` — Special popup triggers.
- `stg_eligible: bool` — True if patient meets guidelines (or fail-open).
- `stg_missing_criteria: list[str]` — Unmet criteria list.
- `stg_reasoning: str | None` — Clinical explanation.
- `is_addon_to: list[str] | None` / `addon_type: str | None` — Parent add-on relationship.
- `match_score: float` — Candidate relevance score.
- `flags: list[str]` — Warnings specific to this package.

### 16. `FinalPackage`
- `validated: ValidatedPackage` — Wrapped validated package.
- `role: str` — Combinatorial role (`"primary" | "secondary" | "tertiary" | "addon" | "standalone"`).
- `deduction_factor: float` — Payment factor (`1.0 | 0.5 | 0.25`).
- `pre_auth_group: int` — Batch ID (`1` for main pre-auth, `2` for separate submission).

### 17. `DocumentItem`
- `key: str` — Canonical doc ID.
- `label: str` — Human readable label.
- `package_code: str | None` — Parent package association (`None` for universal).
- `available: bool` — Checked against available files.
- `criticality: str` — `"hard_block"` (rejection risk) or `"ppd_query_risk"` (query risk).

### 18. `Flag`
- `code: str` — Snake-case code.
- `message: str` — Narrative description.
- `severity: str` — `"info" | "warning" | "block"`.

### 19. `EnhancementPlan`
- `procedure_code: str` — Target package code.
- `estimated_requests: int` — Requests to pre-file.
- `batch_size_used: int` — Days per request (`2` or `5`).
- `los_indicative_days: int` — Indicative LoS (0 if daycare).
- `caveat: str` — Standard liability text.

### 20. `IRISOutput`
- `readiness_status: str` — `"READY" | "READY_WITH_WARNINGS" | "CONDITIONAL" | "BLOCKED"`.
- `selected_packages: list[FinalPackage]` / `blocked_candidates: list[dict]` / `preauth_docs_required: list[DocumentItem]` / `preauth_docs_missing: list[DocumentItem]` / `enhancement_plan: list[EnhancementPlan]` / `copayment_required: bool` / `copayment_gap_inr: int | None` / `comorbidity_notes: list[str]` / `flags: list[Flag]` / `stg_coverage: dict` / `errors: list[str]` — Sub-modules.

---

## 5. Knowledge Base (KB) Architecture

The knowledge base is divided into five layers:

| Layer | Path | Status | Details / Purpose |
|---|---|---|---|
| **KB-1 (Fuzzy Index)** | `data/hbp/_index.json` | ✅ Built | Contains 763 rows mapping procedures to specialties, base rates, aliases, billing units, and auto-approval rules. Used by Phase 2 candidate fuzzy search. |
| **KB-2 (Specialty Masters)** | `data/hbp/<specialty_name>.json` | ⚠️ Partial | Detailed package masters. Shards exist for: burns management, cardiology, CTVS, emergency room, ENT, general medicine, general surgery, high-end diagnostics, high-end medicine, and high-end procedures. The remaining specialties are not yet built. |
| **KB-3 (Standard Treatment Guidelines)** | `data/stg/<procedure_code>.json` | ⚠️ Partial | Machine-readable STG JSON files containing indications, thresholds, checklist parameters, mandatory documents, and expected length of stay. Over 1,000 files are present. |
| **KB-4 (Scheme Rules)** | `data/schemes/pmjay.json` | ✅ Built | Authority, versioning, city tier uplifts (tier-1: 25%/15%, tier-2: 17%/15%), quality incentives (Bronze: 5%, NABH Entry: 10%, NABH Full: 15%), bed rates, multi-surgical rule multipliers (100%, 50%, 25%), exclusions, and Aadhaar relaxation limits. |
| **KB-5 (Query Taxonomy)** | `data/query_taxonomy.json` | ❌ Missing | PPD query reasons catalog. `load_query_taxonomy()` is declared in `kb/loader.py`, but the physical JSON file is not yet built (triggers `FileNotFoundError` if called). |

---

## 6. LLM Usage Policy

IRIS enforces a strict, zero-temperature clinical LLM usage policy using the `gemini-2.5-flash` model. All calls use a retry mechanism and are backed by **fail-safe/fail-open policies** to prevent pipeline failures.

### LLM Gateways (defined in `llm/stg_checker.py` and `kb/searcher_llm.py`):

1. **`check_plausibility(procedure_code, procedure_name, clinical)`**
   - **Trigger:** Candidate procedure has no STG file in KB-3.
   - **Prompt Context:** Patient chief complaints, provisional diagnosis, planned procedure.
   - **Output:** `{"plausible": bool, "reason": str}`.
   - **Fallback on Failure:** `{"plausible": True, "reason": "Passed by default (LLM failure)"}` (fail-open).
2. **`check_stg_eligibility(procedure_code, stg, clinical)`**
   - **Trigger:** Candidate procedure has an STG file in KB-3.
   - **Prompt Context:** STG indications, thresholds, qualifications, ALOS, clinical key pointers vs. patient vitals, diagnosis, chief complaints, investigations, and examination findings.
   - **Output:** `{"eligible": bool, "missing_criteria": list, "reasoning": str, "confidence": str}`.
   - **Fallback on Failure:** `{"eligible": True, "missing_criteria": [], "reasoning": "LLM check failed — passed by default", "confidence": "low"}` (fail-open).
3. **`resolve_stratum(package_code, survivors, clinical, patient, stgs, shard_procedures)`**
   - **Trigger:** Multiple ValidatedPackages share a `package_code` and need duplicate stratum resolution in Phase 3.
   - **Prompt Context:** Candidate list summaries (names + admission criteria) vs. patient demographics and clinical numerics.
   - **Output:** `{"selected": "PROCEDURE_CODE", "reason": "one sentence explanation"}`.
   - **Fallback on Failure:** Fuzzy WRatio search matching patient complaints/diagnosis against procedure names.
4. **`search_candidates(clinical, empanelled_specialties, hospital_is_public)`** (in `kb/searcher_llm.py`)
   - **Trigger:** `PHASE2_SEARCH_MODE == "llm"`.
   - **Prompt Context:** Serialized list of available HBP index procedures matching hospital empanelment gates vs. patient chief complaints, HPI, and diagnosis.
   - **Output:** `{"procedure_codes": ["CODE1", "CODE2", ...]}`.
   - **Fallback on Failure:** Returns an empty candidate list `[]` (fail-safe).

---

## 7. Business Flags and Reason Codes

### Block Flags (`severity="block"`):
- **`PREFLIGHT_FAILED`**: Error querying BIS/HEM stubs in Phase 0.
- **`PATIENT_NOT_IN_BIS`**: Patient ID not found in the BIS database.
- **`SCHEME_NOT_SUPPORTED`**: Hospital scheme is not `"pmjay"` (e.g. `"cmchis"`).
- **`CANDIDATE_GENERATION_FAILED`**: Exception raised during candidate search.

### Warning Flags (`severity="warning"`):
- **`NO_CANDIDATES_FOUND`**: Zero packages survived empanelment gates in Phase 2.
- **`USP_RECOMMENDED`**: Phase 3 returned zero validated packages (recommends unspecified surgical route).
- **`SURGICAL_PERDAY_BLOCKED`**: Drop medical per-day packages due to presence of surgical/daycare packages (Rule 5).
- **`PERDAY_MULTIPLE_BLOCKED`**: Drop lower-scoring per-day packages when multiples exist (Rule 6).
- **`ADDON_PARENT_UNKNOWN`**: Add-on package lacks parent listing.
- **`ADDON_PARENT_MISSING`**: Parent package of the add-on is missing from the validated set.
- **`DIAGNOSTIC_ADDON_BLOCKED`**: High-end diagnostic add-on dropped because no per-day package is present.
- **`RATE_NULL_FOR_PERDAY`**: Base rate of a selected per-day package is null in the index (pricing is ward-category dependent).
- **`VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS`**: Senior citizen dual-wallet (family + Vay Vandana) active; debit order unclear.
- **`WALLET_INSUFFICIENT`**: Estimated pre-auth cost exceeds available patient wallet.
- **`EXCLUSION_*_RISK`**: Match detected against Annexure 6 exclusions:
  - `EXCLUSION_OPD_ONLY_RISK`, `EXCLUSION_DENTAL_RISK`, `EXCLUSION_INFERTILITY_RISK`, `EXCLUSION_VACCINATION_RISK`, `EXCLUSION_COSMETIC_RISK`, `EXCLUSION_CIRCUMCISION_RISK` (if age < 2), `EXCLUSION_PVS_RISK`, `EXCLUSION_DRUG_REHAB_RISK`.
- **`NEONATAL_ESCALATION_RISK`**: Admitted patient is a neonate (age 0).
- **`MTB_REQUIRED`**: Oncology package selected; requires Tumour Board approval.
- **`NOTTO_DOCS_REQUIRED`**: Organ transplant package selected; requires National Organ and Tissue Transplant Organisation approvals.
- **`MANDATORY_DOCS_MISSING`**: Missing one or more hard-block supporting files.

### Info Flags (`severity="info"`):
- **`EMERGENCY_PHASE_STUBBED`**: Emitted during Phase 1 (emergency routing stub).
- **`CANDIDATES_GENERATED`**: Emitted after candidate Generation finishes in Phase 2.
- **`DEDUCTION_APPROXIMATE`**: Surgical package present in selection (indicates 100-50-25 calculations are based on index base rates, not final calculated prices).
- **`STANDALONE_SPLIT`**: Standalone surgical package isolated into separate pre-auth group (Rule 10).
- **`FINANCIAL_ESTIMATE_APPROXIMATE`**: Wallet check calculation is approximate (multipliers not applied).
- **`COMORBIDITY_REVIEW_NEEDED`**: Patient comorbidity not matching standard list (requires audit).
- **`PAEDIATRIC_DEVICE`**: Patient age is ≤14 years.
- **`ONCOLOGY_MULTI_STAGE`**: Oncology treatment selected (multi-stage management implied).
- **`PORTABILITY_CASE`**: Patient home state is different from hospital state (interstate portability).
- **`DOC_GAP_ANALYSIS`**: Emitted after document checklist evaluation in Phase 9.

### Phase 3 Blocked Reason Codes:
- **`SHARD_NOT_FOUND`**: Missing detailed KB-2 shard for specialty.
- **`PROCEDURE_NOT_IN_SHARD`**: Code not found in the specialty shard master list.
- **`PUB_RESERVED_BLOCK`**: Procedure reserved for public institutions; private hospital claims blocked.
- **`STG_PLAUSIBILITY_FAILED`**: Guidelines absent and LLM plausibility checks failed.
- **`STG_INELIGIBLE`**: Guidelines present and LLM clinical checks failed.
- **`STRATUM_NOT_SELECTED`**: Duplicate package stratum rejected in favor of LLM-selected code.

---

## 8. Configuration Constants (`config.py`)

- **`TOP_N_CANDIDATES`** (`int`): Max candidate procedures to pass to Phase 3 (`30`).
- **`MIN_FUZZY_SCORE`** (`int`): Minimum rapidfuzz score required for fuzzy matching (`50`).
- **`ENHANCEMENT_BATCH_PRIVATE`** (`int`): LoS extension requests block size for private hospitals (`2` days).
- **`ENHANCEMENT_BATCH_PUBLIC`** (`int`): LoS extension requests block size for public/NE hospitals (`5` days).
- **`NE_STATES_AND_ISLANDS`** (`list[str]`): List of North Eastern states and union territories.
- **`REQUIRE_STG_FOR_VALIDATION`** (`bool`): If True, blocks candidate packages that have no STG guidelines file (currently `False`, defaults to LLM plausibility checks).
- **`LLM_MODEL`** (`str`): Model used for prompts (`"gemini-2.5-flash"`).
- **`LLM_TIMEOUT_SECONDS`** / **`LLM_MAX_RETRIES`**: API timeout (`30`s) and retry cap (`2`).
- **`SENIOR_CITIZEN_AGE`** (`int`): Threshold for senior citizen dual-wallets (`70`).
- **`PAEDIATRIC_AGE_MAX`** (`int`): Threshold for paediatric device warnings (`14`).
- **`FAMILY_WALLET_DEFAULT_INR`** / **`VAY_VANDANA_WALLET_INR`**: Wallet default reference values (₹5,000,000).
- **`LOG_LEVEL`** / **`LOG_FORMAT`**: System logger settings.
- **`PHASE2_SEARCH_MODE`** (`str`): Switches candidate search between `"fuzzy"` (rapid local search) and `"llm"` (Gemini selection).

---

## 9. Known Gaps and Stubs

1. **`input_validator.py`**: Stubs input validation; does not enforce schemas. Malformed inputs could trigger exceptions during phase processing.
2. **`phase1_emergency.py`**: Fully stubbed; always sets `is_emergency=False` and skips ER package selection.
3. **Phase 5 Pricing Multipliers**: Financial estimation uses raw `base_rate_inr` and deduction factors only. City-tier uplifts (up to 25%), hospital quality incentives (up to 15%), and geographic modifiers are not applied.
4. **`EnhancementPlan.los_indicative_days`**: Always returns `0` due to LoS integers not being persisted on `ValidatedPackage`.
5. **`load_query_taxonomy()`**: Function is present in the loader, but `query_taxonomy.json` is missing from the directory, causing `FileNotFoundError` if called.
6. **22 Missing KB-2 Specialty Shards**: Specialty masters for infectious diseases, interventional radiology, surgical oncology, etc., are missing. Procedures in these specialties trigger `SHARD_NOT_FOUND` and fail back to the USP path.
7. **Exclusion checks**: Simple keyword boundary scans only; cannot evaluate clinical exceptions (e.g. trauma exceptions for dental procedures).
