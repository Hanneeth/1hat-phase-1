# IRIS — PM-JAY Pre-Authorisation Package Selection Engine
===========================================================

IRIS is a multi-phase validation and selection engine designed to assess patient clinical admissions against the Ayushman Bharat PM-JAY national health scheme criteria. By integrating deterministic rule checks with Large Language Model (LLM) reasoning for complex clinical guidelines, IRIS automates hospital pre-authorisation decisions, helps select valid benefit packages, predicts wallet sufficiency, identifies document gaps, and flags potential claim query risks for medical audit.

---

## 1. Installation

IRIS requires Python 3.11+ due to its usage of modern type hinting syntax.

1.  **Clone the repository and navigate to the project directory:**
    ```bash
    cd 1hat-phase1
    ```

2.  **Configure a virtual environment (optional but recommended):**
    ```bash
    python -m venv venv
    venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables:**
    Create a `.env` file in the root directory and provide your Gemini API credentials:
    ```env
    GEMINI_API_KEY=your_gemini_api_key_here
    ```

---

## 2. Configuration

All engine parameters are defined in `config.py`. You can adjust settings directly in this file:
*   **`PHASE2_SEARCH_MODE`:** Switch between `"llm"` (Gemini semantic search) and `"fuzzy"` (rapidfuzz string matching).
*   **`REQUIRE_STG_FOR_VALIDATION`:** Set to `True` to treat missing STG files as a hard block; set to `False` (default) to fallback to clinical plausibility validation.
*   **`LLM_MODEL`:** Default is `"gemini-2.5-flash"`.
*   **`LLM_TIMEOUT_SECONDS`:** Timeout limit for standard LLM calls (30 seconds).
*   **`QUERY_PREDICTOR_TIMEOUT_SECONDS`:** Timeout limit for query predictor LLM calls (90 seconds).

---

## 3. Usage

### Command Line Interface (CLI)
Run the pipeline orchestrator by passing a patient pre-auth input JSON file path:
```bash
python main.py tests/inputs/TC01.json
```

Or pipe inputs from standard input:
```bash
python main.py < tests/inputs/TC01.json
```

The output is written to `stdout` as a formatted JSON payload containing the final pre-auth readiness status, selected package lists, missing documents checklist, estimated rates, and business flags. Logs are output to stderr.

### Streamlit Debug Console
For interactive testing, case validation, and visual log analysis, launch the Streamlit debug dashboard:
```bash
streamlit run app.py
```
This launches a browser window showing a console where you can:
*   Load built-in test cases (`TC01` - `TC24`) to examine phase-by-phase execution and view full logs side-by-side.
*   Manually type clinical details (chief complaints, diagnosis, vitals) to test candidate matching and rules processing interactively.
*   Review nearby clinical matches and explain why candidates were blocked.

---

## 4. Evaluation Framework

To run the automated test suite and check the engine's package selection accuracy against the official answer key:
```bash
python eval.py
```

### Specific Test Cases
To run a single test case (e.g. TC17) to debug validation gates:
```bash
python eval.py --tc TC17
```

### How the Evaluation Script Works
The `eval.py` script automatically:
*   Discovers input files matching `tests/inputs/TC*.json`.
*   Spawns each test case through `main.py` in a separate subprocess with the system python interpreter.
*   Intercepts standard output to extract the final `IRISOutput` JSON and stderr for real-time console tracing.
*   Compares actual selected codes against the target key at `tests/output/expected_output.json`.
*   Generates an alphanumeric summary table printed to the terminal, and saves a detailed timestamped text report inside `tests/output/eval_DD_MM_HH_MM.txt`.

---

## 5. Implementation Status

The pre-authorisation engine implements the following status details:
*   **Core Architecture:** Active and fully structured around a shared mutable state container (`IRISSession`) executing 11 sequential phases.
*   **Fuzzy and LLM Candidate Generation:** Fully implemented with a config-driven router switching between RapidFuzz token-set-ratio matching and Gemini-based query generation.
*   **Clinical Validation & STGs:** Gemini-based validation checks patient diagnostic reports and clinical presentations against Standard Treatment Guidelines. Missing STGs fallback to clinical plausibility validation.
*   **Multi-Package Combos:** Implemented rules for surgical sliding-scale rate deductions, medical/surgical overlap blockings, add-on validation, and standalone pre-auth splits.
*   **Exclusion Engine:** Scans clinical details against keywords for 9 standard exclusions, executing LLM-based exceptions checks for Group A exclusions.
*   **Document Gap & Claim Query Prediction:** Dynamic document requirement compilation with public hospital relaxation guidelines. Features Gemini-based claim query risk forecasts.
*   **Known Gaps:** Phase 1 emergency routing, state overrides, and state variant rules are currently stubbed or not started.

---

## 6. Codebase Navigation

*   [`main.py`](file:///e:/Code/1hat-phase1/main.py): Pipeline orchestrator and entry point.
*   [`models.py`](file:///e:/Code/1hat-phase1/models.py): Domain dataclasses representing clinical inputs, candidates, and outputs.
*   [`session.py`](file:///e:/Code/1hat-phase1/session.py): State container carrying transaction logs and business flags across phases.
*   [`config.py`](file:///e:/Code/1hat-phase1/config.py): Core thresholds, age configurations, model settings, and paths.
*   [`phases/`](file:///e:/Code/1hat-phase1/phases/): Modules executing Phase 0 through Phase 10 rules.
*   [`llm/`](file:///e:/Code/1hat-phase1/llm/): Gemini prompt generation, conflict resolution, and nearest match selectors.
*   [`kb/`](file:///e:/Code/1hat-phase1/kb/): Package masters, index loaders, and fuzzy search algorithms.
*   [`stubs/`](file:///e:/Code/1hat-phase1/stubs/): Mock clients querying patient identity (BIS) and hospital profiles (HEM).
*   [`data/`](file:///e:/Code/1hat-phase1/data/): Catalog subdirectory including HBP specialty masters, STGs, and dummy databases.
