# IRIS Phase 1 — System Design Reference

**Read this file fully before generating any code in this project.** It is the single source of truth for architecture, data flow, and interface contracts.

---

## What IRIS Is

IRIS is a **PM-JAY package selection engine**. Given clinical input about a patient at hospital admission, IRIS recommends the correct PM-JAY package code(s), validates eligibility, lists required documents, and outputs a pre-auth readiness status.

PM-JAY is India's government health assurance scheme. Hospitals file pre-authorisation requests against specific package codes. Wrong package selection is the top cause of pre-auth rejection. IRIS automates this decision.

**This is Phase 1 of the IRIS project.** Scope is pre-authorisation only. Claims-side logic is out of scope.

---

## Pipeline Architecture

IRIS is a **deterministic pipeline of 10 phases** plus pre-flight stubs. Each phase is a single function that reads from a shared `IRISSession` object and writes back to it. The orchestrator (`main.py`) calls phases in fixed sequence.

```
Input JSON
    ↓
[validate_input] — placeholder, returns True for now
    ↓
[Phase 0] Pre-flight — populate patient + hospital context
    ↓
[Phase 1] Emergency routing — stubbed to always non-emergency
    ↓
[Phase 2] Candidate generation — fuzzy match on _index.json
    ↓
[Phase 3] Package validation — rules + LLM-based STG check
    ↓
[Phase 4] Multi-package resolution — combination rules
    ↓
[Phase 5] Financial check — wallet sufficiency
    ↓
[Phase 6] Exclusion verification
    ↓
[Phase 7] Comorbidity resolution
    ↓
[Phase 8] Special populations
    ↓
[Phase 9] Document gap analysis
    ↓
[Phase 10] Output assembly
    ↓
IRISOutput JSON
```

---

## Directory Structure

```
iris/
├── main.py                          # Entry point + orchestrator
├── config.py                        # All tunable constants
├── session.py                       # IRISSession dataclass
├── models.py                        # All other dataclasses
├── input_validator.py               # Stub for now
├── logger_setup.py                  # Logging configuration
│
├── kb/
│   ├── __init__.py
│   ├── loader.py                    # JSON loading + caching
│   └── searcher.py                  # Fuzzy match against _index.json
│
├── stubs/
│   ├── __init__.py
│   ├── bis_stub.py                  # verify_bis() + get_wallet_balance()
│   └── hem_stub.py                  # check_empanelment()
│
├── llm/
│   ├── __init__.py
│   └── stg_checker.py               # LLM call for STG eligibility
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
    │   └── <specialty>.json files
    ├── stg/
    │   └── <procedure_code>.json files
    ├── schemes/
    │   └── pmjay.json
    ├── query_taxonomy.json
    └── dummy/
        ├── dummy_bis.json
        └── dummy_hem.json
```

---

## Knowledge Base Structure

| KB | File | Contents |
|---|---|---|
| KB-1 | `data/schemes/pmjay.json` | Scheme-wide rules: pricing, multipliers, combination rules, exclusions, enhancement batch sizes, NE states list, special case payments |
| KB-2 | `data/hbp/<specialty>.json` | Per-specialty procedure records: rates, stratification, implants, mandatory docs, billing unit, auto-approval status, procedure label |
| KB-2 | `data/hbp/_index.json` | Thin index of all 1,962 procedures with aliases for fuzzy search |
| KB-3 | `data/stg/<code>.json` | Standard Treatment Guidelines per procedure: clinical indications, thresholds, mandatory docs, common queries |
| KB-4 | `data/query_taxonomy.json` | PPD query reasons (Table 3) + rejection reasons (Table 4) from CAM 2026 |

CMCHIS (KB-5) is **out of scope for now**. Engine fast-fails if `hospital.scheme != "pmjay"`.

---

## The Session Object — Pipeline Spine

`IRISSession` is one dataclass that flows through every phase. Each phase reads what it needs and writes its outputs back. Defined in `session.py`.

**Key fields populated by each phase:**

| Phase | Writes to session |
|---|---|
| Phase 0 | `patient`, `hospital`, `patient_eligible`, `hospital_empanelled`, `mlc_required` |
| Phase 1 | `is_emergency`, `er_package_code`, `needs_specialty_package` |
| Phase 2 | `candidate_packages` |
| Phase 3 | `validated_packages`, `phase3_blocked` |
| Phase 4 | `final_package_set` |
| Phase 5 | `wallet_sufficient`, `copayment_required`, `copayment_gap_inr` |
| Phase 6-8 | Appends to `flags` |
| Phase 9 | `preauth_docs_required`, `preauth_docs_missing` |
| Phase 10 | Returns `IRISOutput` (does not modify session) |

**Two list fields every phase can append to:**
- `session.flags: list[Flag]` — expected business outcomes (block, warning, info) for MEDCO to see
- `session.errors: list[str]` — technical failures (file not found, API timeout, parse error) for developer to fix

---

## Input Schema

The pipeline accepts a JSON object with three top-level keys: `patient`, `hospital`, `clinical`. See `iris_input_schema.json` for the full reference example.

**Key fact:** `patient` and `hospital` are populated by stubs (Phase 0) from `patient_id` and `hospital_id`. The MEDCO only fills `clinical`.

The `clinical` object contains:
- `is_emergency`, `is_medico_legal` (booleans)
- `chief_complaints`, `provisional_diagnosis`, `planned_procedure` (free text)
- `vitals` (BP, pulse, SpO2, GCS, etc.)
- `investigations[]` — each has `type` (canonical key like "ecg"), `result_summary` (free text), `document_available` (bool)
- `comorbidities[]` (string list)
- `non_clinical_documents_in_hand[]` — each has `key`, `label`, `available`
- `treating_doctor` (name, registration, qualification, specialty_code)
- `notes` (free text)

---

## Output Schema

`IRISOutput` is produced by Phase 10 and serialised to JSON for the user.

**Four readiness states:**

| State | Meaning |
|---|---|
| `READY` | No flags, no missing docs. Submit immediately. |
| `READY_WITH_WARNINGS` | Passes all hard checks but flagged conditions exist (special_conditions_rule, stratification undeterminable, etc.) |
| `CONDITIONAL` | Non-critical documents missing, PPD may raise queries |
| `BLOCKED` | Hard stop — cannot submit |

**Output contents:**
- `readiness_status` (one of the four states)
- `selected_packages[]` (FinalPackage list)
- `blocked_candidates[]` (procedures eliminated in Phase 3 with reasons)
- `preauth_docs_required[]`, `preauth_docs_missing[]`
- `enhancement_plan[]` (per per-day package: estimated requests needed — **always with caveat that LoS is indicative**)
- `copayment_required`, `copayment_gap_inr`
- `flags[]` (all accumulated flags)
- `stg_coverage` ({validated: n, stg_missing: n})

---

## LLM Usage Policy

**Where LLM is used:**

- **Phase 3 — STG eligibility check.** Per candidate that has an STG file, one LLM call sends the STG `clinical_indications` + `clinical_thresholds` along with the patient's clinical input. LLM responds with `{eligible: bool, missing_criteria: [], reasoning: str}`.

**That is the only LLM call in the MVP.** Everything else is deterministic rules.

**Phase 2 entity extraction is deferred** — for now uses raw text fuzzy search. Later upgrade adds LLM pre-processing.

---

## Configuration

All tunable values live in `config.py`. **No magic numbers in any other file.** Phase files import from config.

Examples of values that must be in config:
- `TOP_N_CANDIDATES = 30`
- `MIN_FUZZY_SCORE = 60`
- `ENHANCEMENT_BATCH_PRIVATE = 2`
- `ENHANCEMENT_BATCH_PUBLIC = 5`
- `NE_STATES_AND_ISLANDS = [...]`
- File paths to `data/` directories
- LLM model name and API timeout
- `REQUIRE_STG_FOR_VALIDATION = False` (if True, candidates without STG file are blocked; if False, warned)

---

## Logging Policy

`logger_setup.py` configures Python `logging` with a consistent format. Every phase file imports a logger:

```python
import logging
logger = logging.getLogger(__name__)
```

**Log levels:**
- `DEBUG` — verbose tracing, only enabled when debugging
- `INFO` — phase entry/exit, key decisions, counts (e.g. "Generated 28 candidates")
- `WARNING` — STG missing, fuzzy score below threshold, deferred logic triggered
- `ERROR` — exceptions caught, appended to `session.errors`

---

## Flags vs Errors

**`session.flags: list[Flag]`** — business outcomes the MEDCO needs to see.
- Examples: "Package blocked: public-reserved", "Stratification undeterminable", "Wallet insufficient"
- Severity: `info` | `warning` | `block`
- Always carry a code (e.g. `PUB_RESERVED_BLOCK`) and a human-readable message

**`session.errors: list[str]`** — technical failures the developer needs to fix.
- Examples: "Failed to load STG file BM001A.json: JSONDecodeError", "LLM API timeout after 30s"
- Plain string messages with enough context to debug

The orchestrator does **not** stop on errors. It catches per-phase exceptions, appends to errors, and continues. The pipeline only stops when a `severity=block` flag is set.

---

## Data Flow Through Phases (Reference)

**Candidate package** (thin, from `_index.json` after fuzzy search):
- `procedure_code`, `package_code`, `specialty_code`, `package_name`, `procedure_name`
- `billing_unit`, `reserved_public_only`, `procedure_label`, `auto_approved`
- `base_rate_inr`
- `match_score` (from fuzzy match — carried through to output)

**Validated package** (rich, after Phase 3):
- Everything from CandidatePackage
- `billing_type` ("surgical" | "fixed_medical" | "per_day" | "day_care")
- `stratification: StratificationResult`
- `implant: ImplantResult`
- `enhancement_applicable`, `enhancement_requests_needed`
- `special_conditions_popup`, `special_conditions_rule`
- `stg_eligible`, `stg_missing_criteria`
- `is_addon_to`, `addon_type`
- `flags: list[str]` (per-package warnings)

**Final package** (after Phase 4):
- Wraps ValidatedPackage
- `role` ("primary" | "secondary" | "tertiary" | "addon" | "standalone")
- `deduction_factor` (1.0 | 0.5 | 0.25)
- `pre_auth_group` (integer — which pre-auth this belongs to)

---

## Critical Rules and Edge Cases

These must be implemented correctly. Reference the file's prompt for specifics.

1. **Public reservation** — hard block if `reserved_public_only=True` and `hospital.type=="private"`.

2. **STG missing** — if STG file doesn't exist for a procedure_code, do NOT block. Pass with warning flag `STG_MISSING`.

3. **Cross-specialty duplicates** — same procedure can appear under multiple specialties. After Phase 2 fuzzy search, deduplicate by `package_code`, keep highest `match_score`.

4. **Auto-approval three states** — Excel column R is Y/N only. Derive: `Y + per_day → "day1_only"`, `Y + not per_day → "full"`, `N → "none"`.

5. **Enhancement batch sizes** — Private hospital = 2 days/request. Public = 5. NE states + islands (Assam, Tripura, Arunachal Pradesh, Meghalaya, Nagaland, Mizoram, Sikkim, Andaman & Nicobar, Lakshadweep) private = 5.

6. **Enhancement count formula** — `ceil((expected_LoS - 1) / batch_size)`. Always present with caveat: "Estimated based on indicative LoS — actual may vary."

7. **Public hospital document relaxation** — at pre-auth, public hospitals only need `clinical_notes`. Private hospitals need full STG mandatory doc list. (CAM Annexure 7).

8. **Vay Vandana wallet** — patient age ≥70 has TWO wallets (family floater + Vay Vandana). NHA does not specify debit order. IRIS flags both balances and notes ambiguity. Does NOT pick one.

9. **Combination rules** —
   - Surgical + Surgical → 100-50-25 (sorted desc by base_rate as MVP proxy — flag as approximate)
   - Surgical + Fixed Medical → 100% each
   - Surgical + Per-day Medical → NOT ALLOWED (block with flag, no SHA exception path in MVP)
   - Per-day + Per-day → NOT ALLOWED (block with flag)
   - Add-on + Primary → 100% on top, no deduction

10. **Standalone packages** — if any standalone is in the validated set with other packages, isolate it into its own pre-auth group. Other packages go in a separate group.

11. **Add-on parent must survive Phase 3** — if a candidate is an add-on (`is_addon_to` not null) and its parent did not survive Phase 3, drop the add-on with flag `ADDON_PARENT_MISSING`.

12. **Diagnostic add-ons** — HD* high-end diagnostics are only allowed when primary is per_day medical. If primary is surgical, drop the diagnostic add-on.

13. **Empty Phase 3 result** — if zero candidates survive Phase 3, flag USP path (`USP_RECOMMENDED`), skip Phase 4 entirely, go directly to Phase 9.

14. **Implant age check** — paediatric implants for age ≤14, adult ≥15. Mismatch = warning flag, not block (TMS allows override with audit).

15. **Special Conditions Rule** — if `special_conditions_rule=True` in KB-2, scan `patient.past_claims` for same `procedure_code` in same policy year. If found, flag `SPECIAL_CONDITIONS_RULE_TRIGGERED`.

16. **CMCHIS fast-fail** — Phase 0 checks `session.hospital.scheme`. If not "pmjay", set BLOCK flag and skip remaining phases.

---

## Coding Conventions

- Python 3.11+
- Use `@dataclass` for all models (not Pydantic — keep dependencies minimal for MVP)
- Use type hints everywhere
- Money: integer INR, variable suffix `_inr`
- Percentages: integer, variable suffix `_pct`
- Enums: lowercase snake_case strings (no Enum class — use Literal type hints if needed)
- Every function has a docstring with: purpose, inputs, outputs, side effects (if any)
- No `print()` calls — use logger
- File paths: always use `pathlib.Path`, never string concatenation
- JSON load: always use `json.load(open(path, encoding="utf-8"))` or `Path.read_text(encoding="utf-8")`

---

## What Not To Do

- Do NOT call LLM anywhere except Phase 3 STG check
- Do NOT add Pydantic, FastAPI, SQLAlchemy, or any web framework — this is CLI only for now
- Do NOT use `print()` — use logger
- Do NOT hardcode constants in phase files — put them in `config.py`
- Do NOT silently swallow exceptions — catch, log to `session.errors`, continue
- Do NOT introduce new file dependencies without updating this doc first
- Do NOT use Enums — use Literal type hints or plain string constants

---

## How To Use This Document (For Antigravity / Claude Code)

1. This document is the **universal context**. Read it before every prompt in this project.
2. For each file, a separate prompt will be provided that specifies:
   - The file's role in the pipeline
   - Its exact public interface (function signatures)
   - Its dependencies (which other files it imports from)
   - Its internal logic in prose
3. When generating code for a file, refer back to this document for:
   - Edge case rules (Critical Rules section)
   - Coding conventions
   - Data model definitions
4. If a prompt conflicts with this document, the prompt wins for that specific file, but flag the conflict.
