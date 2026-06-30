# IRIS: PM-JAY Pre-Auth & Claims Selection Engine

IRIS is an AI-powered healthcare pre-authorisation and claim selection engine designed for hospital MEDCOs (Medical Officers) in India. It streamlines the selection of PM-JAY (Pradhan Mantri Jan Arogya Yojana) Health Benefit Packages (HBP) by mapping unstructured clinical text to procedure codes, verifying Standard Treatment Guidelines (STG) eligibility, detecting clinical exclusions and comorbidities, and matching discharge documents against approved pre-authorisations to flag audit risks and compute partial billing events (such as LAMA, death, or referral).

---

## 1. How to Run the Pipeline

### Prerequisites
1. Install dependencies from [requirements.txt](file:///e:/Code/1hat-phase1/requirements.txt):
   ```bash
   pip install -r requirements.txt
   ```
2. Create a `.env` file in the project root containing your Gemini API key:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

### Stage 1 & 2: Pre-Authorisation Pipeline
To run the pre-authorisation selector for an elective admission case, run [main.py](file:///e:/Code/1hat-phase1/main.py) with the input JSON file path:
```bash
python main.py tests/inputs/TC17.json
```
Alternatively, feed the input JSON through standard input:
```bash
python main.py < tests/inputs/TC17.json
```

This will run Phases 0–10, print the serialized output JSON to stdout, and output a human-readable 3–5 line summary block at the end. It also caches the output to `tests/outputs/TC17_output.json` for Stage 3 claim checks.

### Stage 3: Claims Verification Pipeline
To run claims verification comparing actual discharge data against the approved pre-auth baseline, pass the discharge JSON path to [main_claim.py](file:///e:/Code/1hat-phase1/main_claim.py):
```bash
python main_claim.py tests/inputs/TC17_discharge.json
```
If you pass a folder path containing unstructured medical files (e.g. PDFs or DOCXs), [main_claim.py](file:///e:/Code/1hat-phase1/main_claim.py) will automatically execute the intake parser layer:
```bash
python main_claim.py tests/inputs/TC_CASE1/
```

This will load the cached pre-auth baseline, perform cross-consistency checking, evaluate discharge summary completeness, build the required claim document checklist, compute LoS deviations, trigger audit flags, calculate partial payments if applicable, and generate CPD checklists and deviation justifications via Gemini.

### Running the Visual Dashboard
To run the Streamlit dashboard:
```bash
streamlit run app.py
```
This launches a browser tab showing Tab 1 (Pre-auth Simulator), Tab 2 (Claims Auditor), and Tab 3 (System Reference).

---

## 2. How to Run Tests

IRIS includes a validation test runner [eval.py](file:///e:/Code/1hat-phase1/eval.py) that discovers all test cases in the `tests/inputs/` directory, runs them in parallel, compares selected packages against the answer key `tests/output/expected_output.json`, and outputs a formatted table.

To run the entire test suite:
```bash
python eval.py
```
To run a single test case (e.g. `TC17`):
```bash
python eval.py --tc TC17
```

All test outcomes are saved to a timestamped file in the `tests/output/` directory (e.g. `eval_29_06_18_45.txt`).

---

## 3. Clinical & Financial Rules Summary

### Clinical Rules and Validation Gates
*   **Public-Reserved Blocks:** Private hospitals are blocked from booking packages flagged as `reserved_public_only = true` (Phase 3).
*   **STG Eligibility:** Patients must meet standard treatment criteria. If the STG file is missing, the system conducts an LLM clinical plausibility check. If it fails, the candidate is blocked (Phase 3).
*   **Deduction Ordering:** Mutiple surgical procedures are discounted using a sliding scale: primary (100% of base rate), secondary (50%), and tertiary (25%) (Phase 4).
*   **Medical Per-Day Restrictions:** If any surgical package is present, medical per-day packages are blocked. If multiple medical per-day packages are selected, only the highest rate package is retained (Phase 4).
*   **Clinical Exclusions:** Keyword screening triggers LLM evaluations for Group A exclusions (Dental, Cosmetic, Drug rehab). Packages treating excluded conditions without verified exceptions are blocked (Phase 6).
*   **Comorbidity Absorption:** Medical comorbidities are absorbed under primary surgical packages where HBP guidelines specify (Phase 7).

### Financial Rules
*   **Wallet Entitlements:** The default family wallet is ₹5,00,000 per year (Phase 5).
*   **Vay Vandana Top-up:** Senior citizens (age ≥ 70) receive an additional ₹5,00,000 Vay Vandana wallet. Primary family wallet balances are debited first, followed by the Vay Vandana balance (Phase 5).
*   **Wallet Sufficiency:** If the total estimated cost of the selected packages exceeds the available balance, the case triggers a `WALLET_INSUFFICIENT` warning flag.

### Stage 3 Claims Partial Billing Rules
Partial billing percentages are applied based on HBP Guidelines Section 6:
*   **LAMA / DAMA:** 
    *   No surgery: Billed at per-day rate × actual days.
    *   After surgery: 75% of the total package rate.
*   **Death:**
    *   Within 24 hours of admission: 50% of the package rate.
    *   Before surgery: Billed at per-day rate × actual days.
    *   On-table (during surgery): 75% of the package rate.
    *   After surgery: 100% of the package rate (subject to medical audit).
*   **Referred Cases:**
    *   Before PAC/surgery: Billed at per-day rate × actual days.
    *   After surgery: 75% of the package rate.
