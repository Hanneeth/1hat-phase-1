# IRIS — PM-JAY Pre-Authorisation Package Selection Engine
===========================================================

IRIS is a multi-phase validation and selection engine designed to assess patient clinical admissions against the Ayushman Bharat PM-JAY national health scheme criteria. By integrating deterministic rule checks with Large Language Model (LLM) reasoning for complex clinical guidelines, IRIS automates hospital pre-authorisation decisions, helps select valid benefit packages, predicts wallet sufficiency, identifies document gaps, and flags potential claim query risks for medical audit.

---

## 1. Installation

IRIS requires Python 3.11+ due to its usage of modern type hinting syntax.

1. **Clone the repository and navigate to the project directory:**
   ```bash
   cd IRIS-Phase-1
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables:**
   Create a `.env` file in the root directory and provide your Gemini API credentials:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

---

## 2. Configuration

All engine parameters are defined in `config.py`. You can adjust settings directly in this file:
- **`PHASE2_SEARCH_MODE`:** Switch between `"llm"` (Gemini semantic search) and `"fuzzy"` (rapidfuzz string matching).
- **`REQUIRE_STG_FOR_VALIDATION`:** Set to `True` to treat missing STG files as a hard block; set to `False` to fallback to clinical plausibility validation.
- **`LLM_MODEL`:** Default is `"gemini-2.5-flash"`.

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

The output is written to `stdout` as a formatted JSON payload containing the final pre-auth readiness status, selected package lists, missing documents checklist, estimated rates, and business flags. Logs are output alongside the JSON (separated by standard log formats).

### Streamlit Debug Console
For interactive testing, case validation, and visual log analysis, launch the Streamlit debug dashboard:
```bash
streamlit run app.py
```
This launches a browser window showing a console where you can:
- Load built-in test cases (`TC01` - `TC24`) to examine phase-by-phase execution and view full logs side-by-side.
- Manually type clinical details (chief complaints, diagnosis, vitals) to test candidate matching and rules processing interactively.
- Review nearby clinical matches and explain why candidates were blocked.

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

The evaluation script:
- Discovers files matching `tests/inputs/TC*.json`.
- Runs each test case through the orchestrator in a separate subprocess.
- Compares selected codes against the answer key at `tests/output/expected_output.json`.
- Prints an alphanumeric summary table to terminal and saves a detailed timestamped text report inside `tests/output/eval_DD_MM_HH_MM.txt`.
