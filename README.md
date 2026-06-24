# IRIS — PM-JAY Pre-Authorisation & Claims Verification Engine
===========================================================

**IRIS (Intelligence for Rules and Integration of Schemes)** is an advanced, clinical-first validation and package selection engine designed for the Ayushman Bharat PM-JAY national health scheme. In public health administration, pre-authorisation and claim auditing are heavily bottlenecked by manual, error-prone matching of unstructured clinical reports against complex billing packages, Standard Treatment Guidelines (STGs), and financial capping rules. IRIS solves this by integrating a deterministic rules orchestrator with state-of-the-art LLM reasoning (`gemini-2.5-flash`). The engine processes admission data through an 11-phase pre-authorisation pipeline and a 12-step post-discharge claims verification pipeline. It automates patient eligibility routing, conducts semantic package candidate searches, verifies clinical criteria, applies sliding-scale rate deductions, projects wallet sufficiency, and audits discharges to detect clinical or procedural deviations, drafting automated justifications for medical audit reviewers.

---

## 1. Setup & Installation

IRIS requires **Python 3.11+** to support modern static typing and dataclass features.

### Step-by-Step Installation

1.  **Navigate to the project root directory:**
    ```powershell
    cd e:/Code/1hat-phase1
    ```

2.  **Create and activate a virtual environment (recommended):**
    ```powershell
    python -m venv venv
    # On Windows (PowerShell):
    venv\Scripts\Activate.ps1
    # On Windows (cmd):
    venv\Scripts\activate.bat
    # On Unix/macOS:
    source venv/bin/activate
    ```

3.  **Install the required dependencies:**
    ```powershell
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables:**
    Create a file named `.env` in the project root directory and add your Google Gemini API key:
    ```env
    GEMINI_API_KEY=your_actual_gemini_api_key_here
    ```

---

## 2. Configuration Options

All critical engine parameters, thresholds, paths, and search modes are declared in [config.py](file:///e:/Code/1hat-phase1/config.py):
*   **`PHASE2_SEARCH_MODE`**: Toggles candidate generation in Phase 2 between `"llm"` (using Gemini semantic search) and `"fuzzy"` (using RapidFuzz token-set-ratio matching).
*   **`REQUIRE_STG_FOR_VALIDATION`**: Set to `True` to block candidate packages if their corresponding STG files are missing. Defaults to `False` (in which case it falls back to an LLM-based clinical plausibility check).
*   **`LLM_MODEL`**: The target Gemini model. Defaults to `"gemini-2.5-flash"`.
*   **`LLM_TIMEOUT_SECONDS`**: Standard timeout (30 seconds) for standard LLM queries.
*   **`QUERY_PREDICTOR_TIMEOUT_SECONDS`**: Extended timeout (90 seconds) for complex reasoning tasks (e.g., query predictor or CPD claims checker).
*   **`MIN_FUZZY_SCORE`**: The minimum string similarity score (`50`) required by the fuzzy matching search to shortlist a package.
*   **`TOP_N_CANDIDATES`**: The maximum number of candidate packages (`30`) passed from Phase 2 search into Phase 3 clinical validation.

---

## 3. Usage & Executables

### Pre-Authorisation Pipeline (Stage 1 & 2)
The pre-authorisation entry point is [main.py](file:///e:/Code/1hat-phase1/main.py). It reads admission details, runs the 11-phase pipeline (Phases 0 through 10), and outputs a structured pre-auth verdict payload.

*   **Run with a JSON file argument:**
    ```powershell
    python main.py tests/inputs/done/TC01.json
    ```
*   **Pipe JSON data via standard input:**
    ```powershell
    python main.py < tests/inputs/done/TC01.json
    ```
*   **Outputs:** Standard JSON output containing the pre-auth readiness status, selected package lists, missing documents checklist, estimated rates, and business flags is written to `stdout`. Logging outputs are written to `stderr`.

### Claims Verification Pipeline (Stage 3)
The claims verification entry point is [main_claim.py](file:///e:/Code/1hat-phase1/main_claim.py). It accepts a patient discharge summary JSON, compares actual details against the pre-auth approved baseline, runs the 12-step verification rules, and outputs claims audit results.

*   **Run with a discharge JSON file argument:**
    ```powershell
    python main_claim.py tests/inputs/TC14_discharge.json
    ```
*   **Outputs:** A comprehensive JSON object containing claims statuses, detected deviations, drafted LLM justifications, missing claim documents, partial payments (LAMA/death/referral computations), audit flags, and specialty warnings is written to `stdout`, followed by a formatted clinical summary report block.

### Streamlit Debug Panel
For interactive execution, live logs, side-by-side verification, and manual clinical entry testing, run the Streamlit dashboard:
```powershell
streamlit run app.py
```
This launches a local web server (usually at `http://localhost:8501`) offering:
1.  **Test Case Mode**: Select from pre-loaded test cases ([TC01.json](file:///e:/Code/1hat-phase1/tests/inputs/done/TC01.json) to [TC30.json](file:///e:/Code/1hat-phase1/tests/inputs/done/TC30.json)), run them dynamically, inspect phase-by-phase state logs, and see reasons why specific candidates were blocked.
2.  **Manual Input Mode**: Type raw chief complaints, provisional diagnosis, planned procedures, history of present illness, and vitals to execute candidate selection and check validation rules interactively.

---

## 4. Evaluation & Test Suite

IRIS includes a robust automated test runner in [eval.py](file:///e:/Code/1hat-phase1/eval.py) to check package selection accuracy against the official expected answer key.

*   **Run all test cases:**
    ```powershell
    python eval.py
    ```
*   **Run a single test case (e.g. TC17):**
    ```powershell
    python eval.py --tc TC17
    ```

### How the Evaluation Runner Works
1.  Discovers test case files matching `tests/inputs/TC*.json`.
2.  Spawns `main.py` in a separate subprocess with the system python interpreter.
3.  Intercepts `stdout` to parse `IRISOutput` JSON and streams `stderr` logs to the terminal with test case prefixes.
4.  Compares selected procedure codes against the target key in [expected_output.json](file:///e:/Code/1hat-phase1/tests/output/expected_output.json).
5.  Prints an alphanumeric summary table to the console and writes a timestamped execution log report to `tests/output/eval_DD_MM_HH_MM.txt`.

---

## 5. Current Implementation Status

### What Works
*   **Modular Session-State Container**: [IRISSession](file:///e:/Code/1hat-phase1/session.py) orchestrates and mutates state cleanly across 11 pre-auth phases and 12 claims verification steps.
*   **Accreditation & Eligibility Verification (Phase 0)**: Queries mock databases for patient eligibility verification and hospital empanelment status.
*   **Dual-Engine Candidate Search (Phase 2)**: Config-driven routing between RapidFuzz token matching and Gemini semantic parsing.
*   **Clinical STG & Plausibility Engine (Phase 3)**: LLM verification checks clinical documentation against Standard Treatment Guidelines. Missing STGs automatically fallback to an LLM-based clinical plausibility evaluation.
*   **Complex Combo Deductions (Phase 4)**: Enforces surgical sliding-scale rate deductions (100% / 50% / 25%), drops medical packages if surgical packages exist, and handles add-on package rules.
*   **Double-Wallet Financial Routing (Phase 5)**: Processes family wallets and handles senior citizen Vay Vandana wallet top-ups.
*   **Exclusion Guardrails (Phase 6)**: Scans for exclusions (e.g., cosmetic, dental, rehab) using keywords, with an LLM-based exception override processor.
*   **Claims Audit deviations and partial billing (Phase 11)**: Detects deviations in procedures, ward categories, doctors, and length of stay (LoS). Handles partial payment computations for LAMA, death, and referral events. Enforces Burns photo-tracking and Cardiology stent carton rules.

### Known Gaps & Stubs
*   **Phase 1 Emergency Routing**: Currently stubbed. Always defaults to planned elective admissions (`is_emergency = False`).
*   **KB-4 Rejection Taxonomy**: Production file `data/query_taxonomy.json` is missing. The engine falls back to using the sample at [query_taxonomy.json](file:///e:/Code/1hat-phase1/data/samples/query_taxonomy.json).
*   **KB-5 State Overrides (CMCHIS)**: CMCHIS rules (Tamil Nadu variants) are not started.
*   **Input Validation**: [input_validator.py](file:///e:/Code/1hat-phase1/input_validator.py) is a skeleton code block that always returns `(True, [])`.
*   **Physical Device Sizing**: Pediatric sizing rules are informational warnings and do not validate actual physical measurements.

---

## 6. Codebase File Structure & Navigation

### Core Orchestrator files
*   [config.py](file:///e:/Code/1hat-phase1/config.py): Global config constants, path objects, thresholds, and search mode.
*   [models.py](file:///e:/Code/1hat-phase1/models.py): Strongly-typed dataclass representations of patients, hospitals, clinical inputs, and outputs.
*   [session.py](file:///e:/Code/1hat-phase1/session.py): Unified mutable transaction log and business flag session container.
*   [main.py](file:///e:/Code/1hat-phase1/main.py): Pre-authorisation pipeline entry point and phase orchestrator.
*   [main_claim.py](file:///e:/Code/1hat-phase1/main_claim.py): Claims verification pipeline entry point and step orchestrator.
*   [logger_setup.py](file:///e:/Code/1hat-phase1/logger_setup.py): Centralized standard console logging utility.
*   [input_validator.py](file:///e:/Code/1hat-phase1/input_validator.py): Stub for clinical schema validation.
*   [app.py](file:///e:/Code/1hat-phase1/app.py): Interactive Streamlit dashboard.
*   [eval.py](file:///e:/Code/1hat-phase1/eval.py): Automatic subprocess test evaluation runner.

### Pre-authorisation & Claim Verification Phases
Located under [phases/](file:///e:/Code/1hat-phase1/phases/):
*   [phase0_preflight.py](file:///e:/Code/1hat-phase1/phases/phase0_preflight.py): Patient eligibility (BIS) and hospital check (HEM).
*   [phase1_emergency.py](file:///e:/Code/1hat-phase1/phases/phase1_emergency.py): Emergency routing verification (Stubbed).
*   [phase2_candidates.py](file:///e:/Code/1hat-phase1/phases/phase2_candidates.py): Code catalog searching and candidate shortlisting.
*   [phase3_validator.py](file:///e:/Code/1hat-phase1/phases/phase3_validator.py): STG criteria check, implanation rules, and duplicate resolution.
*   [phase4_multipackage.py](file:///e:/Code/1hat-phase1/phases/phase4_multipackage.py): Multi-package combination validation and rate deductions.
*   [phase5_financial.py](file:///e:/Code/1hat-phase1/phases/phase5_financial.py): Wallet sufficiency check and Vay Vandana top-ups.
*   [phase6_exclusion.py](file:///e:/Code/1hat-phase1/phases/phase6_exclusion.py): Clinical exclusions checking and exception processing.
*   [phase7_comorbidity.py](file:///e:/Code/1hat-phase1/phases/phase7_comorbidity.py): Comorbidity parsing and absorption notes.
*   [phase8_special_pop.py](file:///e:/Code/1hat-phase1/phases/phase8_special_pop.py): Neonatal, pediatric, oncology, transplant, and portability rules.
*   [phase9_documents.py](file:///e:/Code/1hat-phase1/phases/phase9_documents.py): Dynamic document checklist compiler and query risk generator.
*   [phase10_output.py](file:///e:/Code/1hat-phase1/phases/phase10_output.py): Output compilation and session serialization.
*   [phase11_claim.py](file:///e:/Code/1hat-phase1/phases/phase11_claim.py): Claims verification engine (discharge summary audit, LoS deviations, partial billing, and audit flags).

### Knowledge Base & Data Catalogs
*   [kb/loader.py](file:///e:/Code/1hat-phase1/kb/loader.py): Speeds up file reading by caching JSON catalog shards using `@lru_cache`.
*   [kb/searcher.py](file:///e:/Code/1hat-phase1/kb/searcher.py): Implements token-set-ratio based fuzzy matching via RapidFuzz.
*   [kb/searcher_llm.py](file:///e:/Code/1hat-phase1/kb/searcher_llm.py): Clinical query mapping and semantic code generation.
*   [kb/searcher_router.py](file:///e:/Code/1hat-phase1/kb/searcher_router.py): Routes search requests between fuzzy and LLM search backends.
*   [data/schemes/pmjay.json](file:///e:/Code/1hat-phase1/data/schemes/pmjay.json): Core master containing rules for PM-JAY limits, multipliers, and SLAs (KB-1).
*   [data/hbp/](file:///e:/Code/1hat-phase1/data/hbp/): Specialty-specific procedure catalog shards and a derived master index (KB-2).
*   [data/stg/](file:///e:/Code/1hat-phase1/data/stg/): Standard Treatment Guideline definitions containing target checklists, diagnostic ranges, and mandatory documents (KB-3).
*   [data/samples/query_taxonomy.json](file:///e:/Code/1hat-phase1/data/samples/query_taxonomy.json): Standardized rejection code taxonomy (KB-4).

### Large Language Model (LLM) Connectors
Located under [llm/](file:///e:/Code/1hat-phase1/llm/):
*   [stg_checker.py](file:///e:/Code/1hat-phase1/llm/stg_checker.py): Checks clinical notes against STGs, performs plausibility checks, and resolves stratum ties.
*   [conflict_resolver.py](file:///e:/Code/1hat-phase1/llm/conflict_resolver.py): Resolves package mutual exclusions and sub-inclusion rules.
*   [query_predictor.py](file:///e:/Code/1hat-phase1/llm/query_predictor.py): Predicts potential queries from medical auditors based on clinical files.
*   [nearest_match.py](file:///e:/Code/1hat-phase1/llm/nearest_match.py): Finds the closest matching code when the pipeline blocks all candidates.
*   [cpd_evaluator.py](file:///e:/Code/1hat-phase1/llm/cpd_evaluator.py): Evaluates claims checklist compliance and drafts medical justifications.

### Mock Databases & Test Inputs
*   [stubs/bis_stub.py](file:///e:/Code/1hat-phase1/stubs/bis_stub.py): Mock patient identity database client reading [dummy_bis.json](file:///e:/Code/1hat-phase1/data/dummy/dummy_bis.json).
*   [stubs/hem_stub.py](file:///e:/Code/1hat-phase1/stubs/hem_stub.py): Mock hospital empanelment database client reading [dummy_hem.json](file:///e:/Code/1hat-phase1/data/dummy/dummy_hem.json).
*   [tests/inputs/done/](file:///e:/Code/1hat-phase1/tests/inputs/done/): 30 standardized patient admission test cases ([TC01.json](file:///e:/Code/1hat-phase1/tests/inputs/done/TC01.json) to [TC30.json](file:///e:/Code/1hat-phase1/tests/inputs/done/TC30.json)).
*   [tests/inputs/TC14_discharge.json](file:///e:/Code/1hat-phase1/tests/inputs/TC14_discharge.json): Reference discharge summary test case for claim verification.
*   [tests/output/expected_output.json](file:///e:/Code/1hat-phase1/tests/output/expected_output.json): Expected package selections for test cases.
