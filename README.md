# IRIS — PM-JAY Pre-Authorisation Engine

IRIS is a clinical pre-authorisation decision engine for India's national health assurance scheme (**PM-JAY / Ayushman Bharat**). Given a patient's clinical presentation, wallet balance history, and hospital details, IRIS runs a deterministic 11-phase pipeline that selects valid Health Benefit Package (HBP) codes, verifies clinical eligibility against Standard Treatment Guidelines (STGs) using Gemini LLM, resolves multi-procedure billing combination rules, scans for Annexure 6 exclusions, comorbid conditions, and special populations, and performs a pre-auth document gap analysis. It yields a structured pre-auth readiness status (`READY`, `READY_WITH_WARNINGS`, `CONDITIONAL`, or `BLOCKED`) that enables internal medical coordinators to make instant, compliant decisions.

---

## 1. Setup and Installation

### Prerequisites:
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

IRIS takes an input JSON payload, processes it through the pipeline, and prints the serialized `IRISOutput` structure as JSON to **stdout**. All debug logs are printed to **stderr** (or stdout if redirected).

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
- **`1`**: Schema validation failed on input payload.

---

## 3. Running the Test Cases

IRIS includes 16 pre-built test cases under `tests/inputs/` designed to exercise every major pipeline gate and logical branch:

| Test Case | Scenario | Focus Area |
|---|---|---|
| **`TC01.json`** | Shard not built (Infectious Diseases) | Specialty shard missing; triggers Unspecified Surgical Package (USP) pathway |
| **`TC02.json`** | Standard surgical package (Cardiology, Pt P001, Hosp H001) | Baseline success path for private hospital (requires 2 universal docs) |
| **`TC03.json`** | STG ineligible (Cardiology, PTCA guideline check) | LLM eligibility failure; candidate is blocked |
| **`TC04.json`** | Public reservation block (General Surgery, public-only procedure at private hospital) | Candidates blocked due to public-reservation rule |
| **`TC05.json`** | Multi-package surgical + fixed_medical | Multi-procedure combination rules; verifies standalone split |
| **`TC06.json`** | Per-day medical + surgical package conflict | Rule 5: drops medical per-day package in presence of surgical |
| **`TC07.json`** | Senior citizen dual-wallet (Age 72, Kamala Devi) | Dual family & Vay Vandana cards; raises debit order ambiguity warning |
| **`TC08.json`** | Wallet insufficient (family wallet balance ₹8,000) | Compares base rates to balance; raises insufficient wallet warning |
| **`TC09.json`** | Oncology case | Specialty MO/MR/SC; triggers MTB and oncology multi-stage flags |
| **`TC10.json`** | Paediatric boundary case (Age 5, Arjun) | Patient age ≤ 14; raises paediatric device flag |
| **`TC11.json`** | Interstate portability (Maharashtra patient in TN hospital) | State mismatch; raises portability case flag |
| **`TC12.json`** | Medico-legal case (MLC) | `is_medico_legal: true`; adds MLC FIR and self-declaration to required docs |
| **`TC13.json`** | Organ transplant case | Specialty OT; raises NOTTO documentation requirements |
| **`TC14.json`** | Add-on without valid parent procedure | Orphan add-on detection; package is dropped |
| **`TC15.json`** | Standalone package split | Standalone procedure separated into separate pre-auth group (Group 2) |
| **`TC16.json`** | Dual General Surgery (Cholecystectomy + Umbilical hernia repair) | Multi-surgical 100-50 deduction rule and dual selection |

### Executing Standalone Integration Test Scripts:
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
1. **Test Case Selector**: Select any of the 16 pre-built test cases to load their raw parameters.
2. **Clinical Input Editor**: Manually edit complaints, diagnosis, vitals, comorbidities, and non-clinical documents in hand.
3. **Pipeline Runner**: Click "Run IRIS Pipeline" to execute all phases.
4. **Execution Summary**: Visualizes the final pre-auth readiness status, selected package combination details, wallet balances, document gap checklists, comorbidity absorption logs, and full debug printout.
5. **Aesthetics**: Functional and developer-centric console designed for rapid diagnosis and rule tuning.
