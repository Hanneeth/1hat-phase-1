# IRIS — PM-JAY Pre-Authorisation Engine

IRIS is a clinical pre-authorisation decision engine for India's national health assurance scheme (**PM-JAY / Ayushman Bharat**). Given a patient's clinical presentation, wallet balance history, and hospital details, IRIS runs a deterministic 11-phase pipeline that selects valid Health Benefit Package (HBP) codes, verifies clinical eligibility against Standard Treatment Guidelines (STGs) using Gemini LLM, resolves multi-procedure billing combination rules, scans for Annexure 6 exclusions, comorbid conditions, and special populations, and performs a pre-auth document gap analysis. It yields a structured pre-auth readiness status (`READY`, `READY_WITH_WARNINGS`, `CONDITIONAL`, or `BLOCKED`) that enables internal medical coordinators to make instant, compliant decisions.

---

## 1. Setup and Installation

### Prerequisites
- **Python 3.11+**
- **Gemini API Key** (for candidate search, STG guidelines eligibility checks, and stratum tiebreaking)

### Step 1: Clone and Enter Workspace
```powershell
cd e:\Code\1hat-phase1
```

### Step 2: Set Up Virtual Environment (Recommended)
```powershell
python -m venv venv
venv\Scripts\activate      # Windows (PowerShell)
# source venv/bin/activate  # Linux / macOS
```

### Step 3: Install Dependencies
```powershell
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables
Create a file named `.env` in the project root directory:
```env
GEMINI_API_KEY=your_gemini_api_key_here
```

---

## 2. Running the Pipeline

IRIS takes an input JSON payload, processes it through the pipeline, and prints the serialized `IRISOutput` structure as JSON to **stdout**. All debug logs are printed to **stdout** or **stderr** via the standard logging module.

### Running on a File:
```powershell
python main.py tests/inputs/TC02.json
```

### Running via STDIN (PowerShell):
```powershell
cat tests/inputs/TC02.json | python main.py
```

### Pipeline Exit Codes:
- **`0`**: Successful pipeline execution (regardless of whether the final status is `READY` or `BLOCKED`).
- **`1`**: Schema validation failed on input payload (or file not found).

---

## 3. Running the Test Cases

IRIS includes 21 pre-built test cases under `tests/inputs/` designed to exercise every major pipeline gate and logical branch. The expected answer key for these test cases is defined in `tests/output/expected_output.json`.

### Executing the Evaluation Script

To run all 21 test cases and verify pipeline outputs against the answer key:
```powershell
python eval.py
```

To run a single test case by its ID (e.g. `TC17`):
```powershell
python eval.py --tc TC17
```

### Summary of Test Cases (`tests/inputs/`):
- **`TC01.json`**: Shard not built (Infectious Diseases) -> specialty shard missing; triggers Unspecified Surgical Package (USP) pathway.
- **`TC02.json`**: Standard surgical package (Cardiology, Pt P001, Hosp H001) -> baseline success path for private hospital (requires 2 universal docs).
- **`TC03.json`**: STG ineligible (Cardiology, PTCA guideline check) -> LLM eligibility failure; candidate is blocked.
- **`TC04.json`**: Public reservation block (General Surgery, public-only procedure at private hospital) -> candidates blocked due to public-reservation rule.
- **`TC05.json`**: Multi-package surgical + fixed_medical -> multi-procedure combination rules; verifies standalone split.
- **`TC06.json`**: Per-day medical + surgical package conflict -> Rule 5: drops medical per-day package in presence of surgical.
- **`TC07.json`**: Senior citizen dual-wallet (Age 72, Kamala Devi) -> dual family & Vay Vandana cards; raises debit order ambiguity warning.
- **`TC08.json`**: Wallet insufficient (family wallet balance ₹8,000) -> compares base rates to balance; raises insufficient wallet warning.
- **`TC09.json`**: Oncology case -> specialty MO/MR/SC; triggers MTB and oncology multi-stage flags.
- **`TC10.json`**: Pediatric boundary case (Age 5, Arjun) -> patient age ≤ 14; raises pediatric device flag.
- **`TC11.json`**: Interstate portability (Maharashtra patient in TN hospital) -> state mismatch; raises portability case flag.
- **`TC12.json`**: Medico-legal case (MLC) -> `is_medico_legal: true`; adds MLC FIR and self-declaration to required docs.
- **`TC13.json`**: Organ transplant case -> specialty OT; raises NOTTO documentation requirements.
- **`TC14.json`**: Add-on without valid parent procedure -> orphan add-on detection; package is dropped.
- **`TC15.json`**: Standalone package split -> standalone procedure separated into separate preauth group (Group 2).
- **`TC16.json`**: Dual General Surgery (Cholecystectomy + Umbilical hernia repair) -> multi-surgical 100-50 deduction rule and dual selection.
- **`TC17.json`**: General Surgery (Open cholecystectomy) -> single validated package baseline.
- **`TC18.json`**: Dual surgery with different specialties -> applies multi-procedure rules and deduction.
- **`TC19.json`**: Medical + surgical package combination -> tests billing roles assignment.
- **`TC20.json`**: Public hospital document check -> verifies document relaxation for public hospitals.
- **`TC21.json`**: Complex multi-package setup -> exercises combination rules across three selected packages.

### Standalone Integration Test Scripts:
- **Verify Knowledge Base Loaders**:
  ```powershell
  python phaseb_test.py
  ```
- **Test Candidate Generation (Phases 0–2)**:
  ```powershell
  python phasec_test.py
  ```
- **Test LLM STG Eligibility Verification (Phase 3)**:
  ```powershell
  python phased_test.py
  ```
- **Full Pipeline Smoke Test (Phases 0–10)**:
  ```powershell
  python phasee_test.py
  ```

---

## 4. Running the Streamlit Debug Console

For manual interactive inspection, run the built-in Streamlit developer console:
```powershell
streamlit run app.py
```

### Console Features:
1. **Test Case Selector**: Select any of the pre-built test cases to load their raw parameters.
2. **Clinical Input Editor**: Manually edit complaints, diagnosis, vitals, comorbidities, and non-clinical documents in hand.
3. **Pipeline Runner**: Click "Run IRIS Pipeline" to execute all phases.
4. **Execution Summary**: Visualizes the final pre-auth readiness status, selected package combination details, wallet balances, document gap checklists, comorbidity absorption logs, and full debug printout.

---

## 5. File Structure Overview

- `main.py`: Pipeline coordinator. Sequentially executes Phases 0 through 10.
- `config.py`: Configuration thresholds, search modes, and credentials.
- `session.py`: `IRISSession` dataclass tracking the mutable run state.
- `models.py`: Immutable plain Python dataclasses for all inputs/outputs.
- `kb/`: Knowledge base loading, search indexing, and routing logic.
- `llm/`: Gemini SDK integration logic for clinical verification and tiebreakers.
- `phases/`: Individual implementations of the 11 pipeline phases.
- `stubs/`: Simulated APIs for Beneficiary Identification (BIS) and Hospital Empanelment (HEM).
- `data/`: Local Knowledge Base files (HBP Specialty shards, STG guidelines, scheme rules).
- `tests/`: Raw input JSONs for verification scenarios and expected output answer keys.
- `eval.py`: Bulk evaluation test runner.

---

## 6. Current Implementation Status

### What Works:
- **Deterministic 11-Phase Sequence**: Complete pipeline execution path with strict order, early-exit flags, and Unspecified Surgical Package (USP) redirection.
- **Gemini STG Eligibility Check**: API integration with fail-open safety, clinical indication evaluation, vitals checks, and doctor qualification validation.
- **LLM Stratum Tiebreaker**: Deduplicates package listings using clinical reasoning or local fuzzy fallback.
- **HBP Multi-procedure Deductions**: Implements descending 100-50-25 surgical price rules, standalone group splits, and medical per-day conflict resolution.
- **Document Gap Analysis**: Compiles required documents (universal and conditional) and enforces public hospital document relaxation.
- **Interactive Streamlit Console**: Complete developer workbench for custom runs and logs inspect.

### Stubbed / Not Implemented:
- **Phase 1 (Emergency Routing)**: Currently stubbed; always defaults toelective admissions.
- **Phase 5 pricing multipliers**: City-tier uplifts (up to 25%) and quality incentives (up to 15%) are not implemented in the wallet calculation.
- **Input Validation**: Schema checks are currently bypassed in `input_validator.py`.
- **Missing Shards**: 13 out of 32 specialty shards are missing (triggers `SHARD_NOT_FOUND` and routes to the USP pathway).
- **Query Taxonomy File**: `query_taxonomy.json` is missing from its production location (a reference exists in samples).
- **State Variant (CMCHIS)**: State-specific rules and hospital specialty validation grids are not built.
