# IRIS Phase 1 — System Design Reference

**Read this file fully before generating any code in this project.**
It is the single source of truth for architecture, data flow, interface contracts, and KB schemas.

---

## What IRIS Is

IRIS is a PM-JAY package selection engine. Given clinical input about a patient at hospital admission, IRIS recommends the correct PM-JAY package code(s), validates eligibility, lists required pre-auth documents, and outputs a pre-auth readiness status.

PM-JAY is India's government health assurance scheme. Hospitals file pre-authorisation requests against specific package codes. Wrong package selection is the top cause of pre-auth rejection. IRIS automates this decision.

**This is Phase 1.** Scope is pre-authorisation only. Claims-side logic is out of scope. CMCHIS and state-specific schemes are out of scope — PM-JAY (national) only.

---

## Pipeline Architecture

IRIS is a **deterministic pipeline** of phases. Each phase is a function that reads from a shared `IRISSession` object and writes back to it. `main.py` calls phases in fixed sequence.

```
Input JSON
    ↓
[validate_input]       — placeholder, always returns True
    ↓
[Phase 0]              — populate patient + hospital from stubs
    ↓
[Phase 1]              — emergency routing (stubbed: always non-emergency)
    ↓
[Phase 2]              — fuzzy candidate generation from _index.json
    ↓
[Phase 3]              — per-package validation: rules + LLM STG check
    ↓
[Phase 4]              — multi-package combination rules
    ↓
[Phase 5]              — wallet sufficiency check
    ↓
[Phase 6]              — exclusion verification
    ↓
[Phase 7]              — comorbidity resolution
    ↓
[Phase 8]              — special populations
    ↓
[Phase 9]              — document gap analysis
    ↓
[Phase 10]             — output assembly
    ↓
IRISOutput JSON
```

**Early exit:** after every phase, `main.py` checks `session.has_block_flag()`. If True, skip to Phase 10. After Phase 3, if `session.validated_packages` is empty, set `session.usp_recommended = True`, skip Phases 4-8, go directly to Phase 9 then 10.

---

## Directory Structure

```
iris/
├── main.py
├── config.py
├── session.py
├── models.py
├── input_validator.py
├── logger_setup.py
│
├── kb/
│   ├── __init__.py
│   ├── loader.py
│   └── searcher.py
│
├── stubs/
│   ├── __init__.py
│   ├── bis_stub.py
│   └── hem_stub.py
│
├── llm/
│   ├── __init__.py
│   └── stg_checker.py
│
├── phases/
│   ├── __init__.py
│   ├── phase0_preflight.py
│   ├── phase1_emergency.py
│   ├── phase2_candidates.py
│   ├── phase3_validator.py
│   ├── phase4_multipackage.py
│   ├── phase5_financial.py
│   ├── phase6_exclusion.py
│   ├── phase7_comorbidity.py
│   ├── phase8_special_pop.py
│   ├── phase9_documents.py
│   └── phase10_output.py
│
└── data/
    ├── hbp/
    │   ├── _index.json
    │   └── <specialty>.json        (one file per specialty)
    ├── stg/
    │   └── <procedure_code>.json   (one file per procedure)
    ├── schemes/
    │   └── pmjay.json
    ├── query_taxonomy.json
    └── dummy/
        ├── dummy_bis.json
        └── dummy_hem.json
```

---

## Knowledge Base Overview

| KB | File(s) | What it contains |
|---|---|---|
| KB-1 | `data/schemes/pmjay.json` | Scheme-wide rules: pricing multipliers, combination rules, exclusions, enhancement batch sizes, bed rates, NE states list |
| KB-2 | `data/hbp/<specialty>.json` | Per-specialty procedure records — full clinical and billing detail |
| KB-2 index | `data/hbp/_index.json` | Thin index of all procedures with aliases — used by fuzzy search only |
| KB-3 | `data/stg/<code>.json` | Standard Treatment Guidelines per procedure — used by LLM STG checker |
| KB-4 | `data/query_taxonomy.json` | PPD query reasons (Table 3) + rejection reasons (Table 4) from CAM 2026 |

---

## KB-2: HBP Shard Schema (EXACT — from actual files)

One JSON file per specialty. Path: `data/hbp/<specialty_filename>.json`

### Top-level structure
```json
{
  "_comment": "human note string",
  "scheme_id": "pmjay",
  "specialty": "Emergency Room Packages",
  "specialty_code": "ER",
  "packages": [ ... ]
}
```

### Package object
```json
{
  "package_code": "ER001",
  "package_name": "Laceration - Suturing / Dressing",
  "procedures": [ ... ]
}
```

### Procedure object (COMPLETE — all fields)
```json
{
  "procedure_code": "ER001A",
  "procedure_name": "Laceration - Suturing / Dressing",
  "billing_unit": "one_time",
  "quantity_basis": "none",
  "rates_inr": {
    "tier3": 2000,
    "tier2": 2300,
    "tier1": 2300
  },
  "implant": null,
  "pricing": {
    "base_rate_inr": 2000,
    "bed_rates_inr": null,
    "increment": null,
    "quantity_cap": null,
    "sequence": null
  },
  "procedure_label": "regular",
  "is_addon_to": null,
  "addon_type": null,
  "addon_mapping_status": null,
  "reserved_public_only": false,
  "auto_approved": "full",
  "enhancement_applicable": false,
  "special_conditions_popup": false,
  "special_conditions_rule": false,
  "level_of_care": "secondary",
  "day_care": false,
  "los_indicative": 3,
  "stratification_required": false,
  "num_stratifications": 0,
  "stratification_criteria": null,
  "stg_ref": "ER001A",
  "medical_or_surgical": "medical",
  "mandatory_documents": {
    "preauth": [
      {"key": "clinical_notes", "label": "Clinical notes with planned line of treatment"},
      {"key": "clinical_photo_initial", "label": "pre-clinical photograph"}
    ],
    "claim": [
      {"key": "discharge_summary", "label": "Clinical notes / discharge summary"},
      {"key": "clinical_photo_posttreat", "label": "post clinical photograph"}
    ]
  },
  "aliases": [
    "Wound suturing",
    "Laceration repair",
    "ICD-10 T14.1"
  ],
  "additional_information": {
    "alos": "3 days",
    "mlc_note": "MLC required if laceration is due to accident or assault."
  },
  "source_refs": {
    "rates_inr": "HBP-2022.pdf — row ER001A, Tier3/Tier2/Tier1 columns",
    "billing_unit": "HBP_2022_Package_Master1.xlsx col Y (Medical) + specialty logic",
    "reserved_public_only": "HBP_2022_Package_Master1.xlsx col O",
    "auto_approved": "col R (Yes) → full",
    "procedure_label": "col U (Regular Procedure)",
    "mandatory_documents.preauth": "HBP_2022_Package_Master1.xlsx col S",
    "mandatory_documents.claim": "HBP_2022_Package_Master1.xlsx col T"
  }
}
```

### Critical field notes

**`medical_or_surgical`** — `"surgical"` | `"medical"`. Added to KB-2 schema. Used by Phase 3 to classify billing type for combination rules. All existing files will have this field added.

**`billing_unit`** — `"one_time"` | `"per_day"` | `"per_session"` | `"per_fraction"` | `"per_visit"` | `"per_cycle"` | `"per_dose"` | `"per_month"`

**`los_indicative`** — integer (days) OR string `"daycare"`. Handle both.

**`rates_inr`** — can be `null` for per_day packages (rate comes from bed category in pmjay.json).

**`stratification_criteria`** — `null` when `stratification_required=false`. When `true`, is an array of stratum objects. For per_day packages, ALWAYS has these four bed-category strata:
```json
[
  {"stratum_id": "ward",       "description": "Ward",                   "criterion": "Admission to Ward",                             "source": "domain_knowledge"},
  {"stratum_id": "hdu",        "description": "HDU",                    "criterion": "Admission to High Dependency Unit",             "source": "domain_knowledge"},
  {"stratum_id": "icu_no_vent","description": "ICU without ventilator", "criterion": "Admission to ICU without mechanical ventilation","source": "domain_knowledge"},
  {"stratum_id": "icu_vent",   "description": "ICU with ventilator",    "criterion": "Admission to ICU requiring mechanical ventilation","source": "domain_knowledge"}
]
```
For per_day stratification: match `session.clinical.bed_category` against `stratum["stratum_id"]`.

**`implant`** — `null` OR `{"name": "...", "cost_inr": int}` OR array of those objects.

**`procedure_label`** — `"regular"` | `"add_on"` | `"standalone"` | `"follow_up"`

**`auto_approved`** — `"none"` | `"full"` | `"day1_only"`

**`aliases`** — also present in `_index.json`. Both sources are valid; searcher uses `_index.json`.

### Specialty code → shard filename mapping
```python
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
```

---

## KB-2 Index Schema (`_index.json`)

Thin rows only — used exclusively by fuzzy search. One row per procedure.

```json
{
  "procedure_code": "ER001A",
  "package_code": "ER001",
  "specialty": "Emergency Room Packages",
  "specialty_code": "ER",
  "package_name": "Laceration - Suturing / Dressing",
  "procedure_name": "Laceration - Suturing / Dressing",
  "aliases": ["Wound suturing", "Laceration repair", "ICD-10 T14.1"],
  "billing_unit": "one_time",
  "reserved_public_only": false,
  "procedure_label": "regular",
  "auto_approved": "full",
  "day_care": false,
  "base_rate_inr": 2000,
  "stg_ref": "ER001A"
}
```

---

## KB-3: STG Schema (EXACT — from actual files)

One JSON file per procedure_code. Path: `data/stg/<procedure_code>.json`

```json
{
  "_comment": "human note string",
  "stg_id": "MM010B",
  "procedure_code": "MM010B",
  "condition": "Neuro-Developmental Disorders (NDD) Other than Intellectual Disability",
  "procedure_name": "Mixed Developmental Disorder",
  "specialty": "Mental Disorders",
  "version": "1.1",
  "source_files": [
    "Condition_Medco.pdf",
    "Condition_PPD.CPD.pdf"
  ],
  "alos": "3-4 weeks",
  "min_doctor_qualification": [
    "MD/DNB/ equivalent (Psychiatry)"
  ],
  "clinical_indications": [
    "Disorders characterized by a combination of features from various neuro-developmental disorders...",
    "Manifestations are observed during the developmental period..."
  ],
  "clinical_thresholds": [
    {"field": "bcva", "operator": "<=", "value": "6/9", "note": "not improving with pinhole"}
  ],
  "mandatory_documents": {
    "preauth": [
      {"key": "clinical_notes_detailed_history_chronicity", "label": "Clinical notes with detailed history and chronicity"},
      {"key": "admission_document_signed_by_empanelled_psychiatrist", "label": "Admission document signed by empanelled psychiatrist"}
    ],
    "claim": [
      {"key": "detailed_treatment_notes", "label": "Detailed treatment notes"},
      {"key": "detailed_discharge_summary", "label": "Detailed Discharge Summary"}
    ]
  },
  "checklist": {
    "ppd_preauth": [
      {"q": "Clinical notes - detailed history, mini mental status test, indication for treatment and need of hospitalization", "expected": true},
      {"q": "Was the admission document signed by an empanelled psychiatrist?", "expected": true}
    ],
    "cpd_claim": [
      {"q": "Are the detailed treatment notes submitted?", "expected": true}
    ]
  },
  "common_queries": [
    "Admission document not signed by an empanelled psychiatrist.",
    "Clinical notes missing or lacking detailed history, mini mental status test, indication for treatment, or need for hospitalization."
  ],
  "additional_information": {
    "icd_10_code": null,
    "special_empanelment": "As per provisions of the Mental Health Act 2017...",
    "min_doctor_qualification_desirable": null,
    "clinical_key_pointers": [
      "The provisions under Mental Healthcare Act 2017 be referred for details on Admission & Discharge criteria.",
      "Neurodevelopmental disorders other than intellectual disorders come under ICD 11 and DSM-5..."
    ],
    "surgical_options": [],
    "pre_op_preparation": null,
    "post_op_care": null,
    "fitness_criteria": null,
    "comorbidity_management": null,
    "referral_criteria": null,
    "quality_assessment_parameters": [],
    "contraindications": []
  }
}
```

### Critical STG field notes

**`min_doctor_qualification`** — array of strings, NOT a single string.

**`clinical_thresholds`** — array of `{field, operator, value, note}`. NO `unit` field. The `value` is a string (can be numeric like "6/9" or text).

**`checklist.*.expected`** — boolean `true` or `false`. NOT strings "Yes"/"No".

**`common_queries`** — failure-mode problem statements phrased as they would appear on a PPD query/rejection notice. Procedure-specific only — not generic.

**`additional_information.clinical_key_pointers`** — can be very long detailed clinical descriptions. This is the richest clinical content in the STG and should be sent to the LLM in Phase 3 for STG eligibility assessment.

**`additional_information`** — always present with all 13 keys. Some will be null or empty array — never omit.

---

## Billing Type Classification (Phase 3)

`_classify_billing_type(procedure)` → `"surgical"` | `"fixed_medical"` | `"per_day"` | `"day_care"`

**Primary logic (after `medical_or_surgical` field is added to KB-2):**
```python
if procedure["billing_unit"] == "per_day":
    return "per_day"
if procedure["day_care"] == True:
    return "day_care"
if procedure["medical_or_surgical"] == "surgical":
    return "surgical"
return "fixed_medical"
```

**Fallback (if `medical_or_surgical` field is missing from file):**
```python
# Parse source_refs["billing_unit"] for "(Surgical)" or "(Medical)" hint
source_hint = procedure.get("source_refs", {}).get("billing_unit", "")
if "(Surgical)" in source_hint:
    return "surgical"
return "fixed_medical"  # default to medical if ambiguous
```

Always try primary logic first. Fall to fallback only if `medical_or_surgical` key is absent. Log a WARNING when fallback is used.

---

## The Session Object

`IRISSession` defined in `session.py`. Every phase reads from and writes to this object.

### Fields written by each phase

| Phase | Writes to session |
|---|---|
| Phase 0 | `patient`, `hospital`, `patient_eligible`, `hospital_empanelled`, `mlc_required` |
| Phase 1 | `is_emergency`, `er_package_code`, `needs_specialty_package` |
| Phase 2 | `candidate_packages` |
| Phase 3 | `validated_packages`, `phase3_blocked`, `stg_coverage` |
| Phase 4 | `final_package_set` |
| Phase 5 | `wallet_sufficient`, `copayment_required`, `copayment_gap_inr`, `estimated_total_inr` |
| Phase 6-8 | append to `flags` |
| Phase 9 | `preauth_docs_required`, `preauth_docs_missing` |
| Phase 10 | returns `IRISOutput` (does not write to session) |

### Two special boolean fields

**`session.usp_recommended: bool`** — set True by main.py when Phase 3 returns zero validated packages. Causes main.py to skip Phases 4-8 and jump to Phase 9.

**`session.stg_coverage: dict`** — `{"validated": 0, "stg_missing": 0}`. Phase 3 increments during its loop. Phase 10 reads for output.

### Flags vs Errors policy

**`session.flags: list[Flag]`** — business outcomes the MEDCO sees. Each has `code` (UPPER_SNAKE), `message` (human-readable), `severity` (`"info"` | `"warning"` | `"block"`). Pipeline stops (goes to Phase 10) when any `severity="block"` flag is set.

**`session.errors: list[str]`** — technical failures the developer sees. Plain strings. Pipeline continues on errors — never stops.

---

## Input Schema (v2)

The pipeline accepts a JSON with three top-level keys. In test mode, `patient` and `hospital` only need their ID — the stub loads the rest.

```json
{
  "patient": {"patient_id": "P001"},
  "hospital": {"hospital_id": "H001"},
  "clinical": {
    "admission_date": "2026-06-12",
    "bed_category": null,
    "is_emergency": true,
    "is_medico_legal": false,
    "chief_complaints": "Chest pain radiating to left arm, sweating...",
    "duration_days": 0,
    "history_of_present_illness": "Patient was apparently well 4 hours ago...",
    "provisional_diagnosis": "Acute STEMI — inferior wall",
    "planned_procedure": null,
    "weight_kg": 72,
    "height_cm": 168,
    "vitals": {
      "bp_systolic_mmhg": 88,
      "bp_diastolic_mmhg": 58,
      "pulse_bpm": 115,
      "spo2_pct": 91,
      "temperature_f": 98.4,
      "rr_per_min": 24,
      "gcs": 15,
      "blood_glucose_mgdl": 210
    },
    "examination_findings": {
      "general": "Conscious, oriented, pallor present",
      "cvs": "S1 S2 heard, JVP raised",
      "rs": "Clear air entry bilateral",
      "abdomen": "Soft, non-tender",
      "cns": "No focal deficits",
      "local": null
    },
    "investigations": [
      {
        "type": "ecg",
        "result_summary": "ST elevation in leads II, III, aVF — inferior STEMI",
        "structured_values": [
          {"parameter": "ST_elevation", "value": "present", "unit": null, "leads": "II, III, aVF", "flag": "H"}
        ],
        "document_available": true,
        "report_date": "2026-06-12"
      },
      {
        "type": "blood_reports",
        "result_summary": "Troponin I 2.8 ng/mL elevated, CK-MB 48 U/L elevated",
        "structured_values": [
          {"parameter": "Troponin I", "value": 2.8, "unit": "ng/mL", "flag": "H"},
          {"parameter": "CK-MB", "value": 48, "unit": "U/L", "flag": "H"},
          {"parameter": "Hb", "value": 12.4, "unit": "g/dL", "flag": "N"}
        ],
        "document_available": false,
        "report_date": null
      }
    ],
    "comorbidities": ["type2_diabetes", "hypertension"],
    "past_medical_history": "Hypertension 10 years, T2DM 5 years, on regular medication.",
    "past_surgical_history": "Appendicectomy 15 years ago, uneventful.",
    "current_medications": ["Metformin 500mg BD", "Amlodipine 5mg OD"],
    "allergies": ["Penicillin"],
    "personal_history": {
      "smoking": "ex-smoker, 20 pack years, stopped 5 years ago",
      "alcohol": "occasional",
      "diet": "mixed"
    },
    "family_history": null,
    "non_clinical_documents_in_hand": [
      {"key": "clinical_notes", "label": "Admission / clinical notes", "available": true},
      {"key": "patient_photo", "label": "Photo of patient on hospital bed", "available": true}
    ],
    "treating_doctor": {
      "name": "Dr. Suresh Babu",
      "registration_number": "TN-MED-12345",
      "qualification": "MD DM Cardiology",
      "specialty_code": "MC"
    },
    "notes": "Patient brought by family. BP crashing on arrival."
  }
}
```

### Field notes

**`admission_date`** — ISO date string. Used in Phase 0 backdated booking check.

**`bed_category`** — `null` for surgical/fixed-medical. `"ward"` | `"hdu"` | `"icu_no_vent"` | `"icu_vent"` for per-day packages. Used in Phase 3 per_day stratification.

**`investigations[].structured_values`** — array of `{parameter, value, unit, flag}` objects. Produced by OCR. Can be `null` if document not available or not yet processed. Used by Phase 3 LLM for precise threshold matching.

**`investigations[].type`** — canonical enum: `"ecg"` | `"echo"` | `"xray"` | `"ct"` | `"mri"` | `"usg"` | `"blood_reports"` | `"urine_report"` | `"stool_report"` | `"cag_report"` | `"eeg"` | `"abg_chart"` | `"csf"` | `"hpe"` | `"fnac"` | `"other"`

**`non_clinical_documents_in_hand[].key`** — canonical enum: `"clinical_notes"` | `"patient_photo"` | `"mlc_fir"` | `"self_declaration"` | `"treating_doctor_prescription"` | `"referral_letter"` | `"informed_consent"` | `"notto_recipient_id"` | `"notto_donor_id"` | `"tumour_board_approval"` | `"past_hospitalisation_records"` | `"implant_sticker"`

---

## Output Schema

`IRISOutput` produced by Phase 10.

### Readiness states (four)

| State | Meaning |
|---|---|
| `READY` | No flags, no missing docs. Submit immediately. |
| `READY_WITH_WARNINGS` | Passes hard checks but flagged conditions exist (special_conditions_rule, stratification undeterminable, etc.) |
| `CONDITIONAL` | Non-critical documents missing — PPD may raise queries |
| `BLOCKED` | Hard stop — cannot submit |

### Status determination (first match wins)
1. Any `severity="block"` flag → `BLOCKED`
2. Any missing doc with `criticality="hard_block"` → `BLOCKED`
3. `session.final_package_set` is empty → `BLOCKED`
4. Any missing doc with `criticality="ppd_query_risk"` → `CONDITIONAL`
5. Any `severity="warning"` flag → `READY_WITH_WARNINGS`
6. Otherwise → `READY`

### Output fields
- `readiness_status` (string)
- `selected_packages` (list of FinalPackage)
- `blocked_candidates` (list of `{procedure_code, reason_code, message}`)
- `preauth_docs_required`, `preauth_docs_missing` (list of DocumentItem)
- `enhancement_plan` (list of EnhancementPlan — always includes LoS caveat)
- `copayment_required` (bool), `copayment_gap_inr` (int | None)
- `comorbidity_notes` (list of str — which comorbidities are absorbed vs need review)
- `flags` (all accumulated)
- `stg_coverage` (`{validated: n, stg_missing: n}`)
- `errors` (technical failures)

---

## LLM Usage Policy

**Only one LLM call in MVP: Phase 3 STG eligibility check.**

For each candidate package that has an STG file: one LLM call sends STG clinical criteria + patient clinical input. LLM returns `{eligible, missing_criteria, reasoning, confidence}`.

Context sent to LLM (from STG file):
- `stg["clinical_indications"]`
- `stg["clinical_thresholds"]` — `{field, operator, value, note}` — NO unit field
- `stg["min_doctor_qualification"]` — array of strings
- `stg["alos"]`
- `stg["additional_information"]["clinical_key_pointers"]` — richest clinical detail, always include

Context sent to LLM (from clinical input):
- `provisional_diagnosis`
- `chief_complaints`
- `history_of_present_illness`
- `duration_days`
- `vitals` (non-null values only)
- `examination_findings`
- `investigations` (type + result_summary + structured_values if present)
- `comorbidities`
- `past_medical_history`
- `current_medications`

On LLM failure (timeout, malformed JSON, API error): retry up to `LLM_MAX_RETRIES`. If all fail, return `{eligible: True, missing_criteria: [], reasoning: "LLM failed — passed by default", confidence: "low"}` and add error to `session.errors`.

**Phase 2 entity extraction is deferred** — raw text fuzzy search for now, LLM pre-processing added later.

---

## Configuration

All tunable constants in `config.py`. No magic numbers in any other file.

```python
TOP_N_CANDIDATES = 30
MIN_FUZZY_SCORE = 60          # 0-100 scale, rapidfuzz
ENHANCEMENT_BATCH_PRIVATE = 2
ENHANCEMENT_BATCH_PUBLIC = 5
NE_STATES_AND_ISLANDS = [
    "Assam", "Tripura", "Arunachal Pradesh", "Meghalaya",
    "Nagaland", "Mizoram", "Sikkim",
    "Andaman and Nicobar Islands", "Lakshadweep"
]
REQUIRE_STG_FOR_VALIDATION = False
LLM_MODEL = "gemini-2.5-flash"
LLM_TIMEOUT_SECONDS = 30
LLM_MAX_RETRIES = 2
SENIOR_CITIZEN_AGE = 70
PAEDIATRIC_AGE_MAX = 14
FAMILY_WALLET_DEFAULT_INR = 500000
VAY_VANDANA_WALLET_INR = 500000
LOG_LEVEL = "INFO"
```

---

## Logging Policy

Every phase file:
```python
import logging
logger = logging.getLogger(__name__)
```

Levels: `DEBUG` = verbose tracing | `INFO` = phase entry, counts, key decisions | `WARNING` = STG missing, fallback triggered, score below threshold | `ERROR` = exceptions caught

---

## Critical Rules

### Blocking rules
1. `reserved_public_only=True` AND `hospital.type=="private"` → block (PUB_RESERVED_BLOCK)
2. `hospital.scheme != "pmjay"` → block at Phase 0 (SCHEME_NOT_SUPPORTED)
3. STG check fails (LLM returns eligible=False) → block candidate (STG_NOT_ELIGIBLE)
4. If `REQUIRE_STG_FOR_VALIDATION=True` and STG missing → block (STG_REQUIRED)

### Combination rules (Phase 4)
5. Surgical + Per-day → NOT ALLOWED same pre-auth → drop per_day, flag SURGICAL_PERDAY_BLOCKED
6. Per-day + Per-day → NOT ALLOWED → keep first only, flag PERDAY_MULTIPLE_BLOCKED
7. Surgical + Fixed Medical → 100% each, allowed
8. Surgical + Surgical → 100-50-25 (sorted by base_rate_inr desc — approximate, flag as such)
9. Add-on + Primary → 100% on top, no deduction
10. Standalone → must be in separate pre_auth_group (pre_auth_group=2)

### Add-on rules
11. If add-on's parent (`is_addon_to`) not in validated set → drop add-on (ADDON_PARENT_MISSING)
12. HD* diagnostic add-ons only allowed with per_day medical primary — if primary is surgical, drop (DIAGNOSTIC_ADDON_BLOCKED)

### Enhancement rules
13. `los_indicative` can be integer OR string "daycare". If "daycare" → enhancement_requests_needed = None
14. Formula: `ceil((los_indicative - 1) / batch_size)` where batch = ENHANCEMENT_BATCH_PUBLIC if public/NE, else ENHANCEMENT_BATCH_PRIVATE
15. Always present with caveat: "Estimated based on indicative LoS — actual may vary"

### Per-day stratification
16. For per_day packages, stratification is ALWAYS bed category. Match `session.clinical.bed_category` against `stratum["stratum_id"]` in `stratification_criteria`. If `bed_category` is null in clinical input → StratificationResult(determinable=False, note="bed_category not provided")

### Financial
17. Vay Vandana wallet (age ≥70): NHA doesn't specify debit order. Show both balances, flag ambiguity (VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS). Never silently pick one.
18. `rates_inr` can be null for per_day packages. Use bed rates from pmjay.json instead. If both null → estimated_total_inr += 0 for that package, flag rate unavailable.

### Documents
19. Public hospital document relaxation (CAM Annexure 7): at pre-auth, public hospitals only need `clinical_notes`. Private hospitals need full KB-2 `mandatory_documents.preauth` list.
20. Universal docs (private): `clinical_notes` (hard_block) + `patient_photo` (hard_block)
21. Conditional: MLC case → `mlc_fir` + `self_declaration` (both hard_block). Transplant → NOTTO IDs (hard_block). Oncology → `tumour_board_approval` (hard_block).

### Special populations
22. Neonatal (age ≤28 days): flag NEONATAL_ESCALATION_RISK
23. Paediatric (age ≤14): flag PAEDIATRIC_DEVICE
24. Portability (home_state ≠ hospital.state): flag PORTABILITY_CASE
25. Oncology (specialty MC/MR/SC): flag MTB_REQUIRED (hard warning)
26. Transplant (specialty OT): flag NOTTO_DOCS_REQUIRED (hard warning)

### Empty Phase 3 result
27. If zero candidates survive Phase 3: set `session.usp_recommended=True`, add WARNING flag USP_RECOMMENDED, skip Phases 4-8, proceed to Phase 9 and 10.

---

## Coding Conventions

- Python 3.11+
- `@dataclass` for all models — no Pydantic
- Type hints everywhere
- Money: integer INR, suffix `_inr`
- Enums: lowercase snake_case string literals — no Enum class
- Every function has a docstring: purpose, inputs, outputs, side effects
- No `print()` — use logger
- File paths: `pathlib.Path` only
- JSON load: `json.loads(Path(path).read_text(encoding="utf-8"))`
- Per-candidate exceptions in Phase 3: catch, log ERROR, append to `session.errors`, continue loop

---

## What Not To Do

- Do NOT call LLM anywhere except Phase 3 STG check
- Do NOT add Pydantic, FastAPI, or any web framework
- Do NOT use `print()` — use logger
- Do NOT hardcode constants — use `config.py`
- Do NOT silently swallow exceptions — catch, log, append to errors
- Do NOT use Enum class — use string literals
- Do NOT invent KB fields — use only fields documented in this file's schemas

---

## How To Use This Document (Antigravity)

1. This is the universal context. Read it before every prompt.
2. Each file has a specific prompt in `PROMPTS.md` with exact function signatures and logic.
3. When the prompt and this document conflict, the prompt wins for that specific file — flag the conflict.
4. Never invent field names when accessing KB data — use the exact schemas above.
