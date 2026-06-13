# IRIS — Solo Implementation Order

Build files in this exact order. Each step is buildable and testable before the next.

---

## Phase A — Foundation

Build these first. Everything else imports from here.

1. `config.py`
2. `logger_setup.py`
3. `models.py`              ← most important file; all other files depend on these dataclasses
4. `session.py`
5. `input_validator.py`     ← stub, returns True always

After Phase A: `python -c "from models import *; from session import IRISSession; print('OK')"` must pass.

---

## Phase B — Data Access Layer

6. `kb/__init__.py`         ← empty
7. `kb/loader.py`
8. `stubs/__init__.py`      ← empty
9. `stubs/bis_stub.py`
10. `stubs/hem_stub.py`

After Phase B: write a 10-line smoke script:
```python
from kb.loader import load_index, load_stg
from stubs.bis_stub import verify_bis
from stubs.hem_stub import check_empanelment
index = load_index()
print(f"Index loaded: {len(index)} entries")
patient = verify_bis("P001")
print(f"Patient: {patient.name if patient else 'NOT FOUND'}")
hospital = check_empanelment("H001")
print(f"Hospital: {hospital.name}")
stg = load_stg("BM001A")
print(f"STG BM001A: {'found' if stg else 'missing'}")
```

---

## Phase C — Core Pipeline: Phase 0 → 2

11. `phases/__init__.py`    ← empty
12. `phases/phase0_preflight.py`
13. `phases/phase1_emergency.py`
14. `kb/searcher.py`
15. `phases/phase2_candidates.py`

After Phase C: run `python main.py tests/inputs/TC01_happy_path.json` (basic version of main.py that only runs Phase 0-2 and prints candidates).

Manually check: do the top candidates make clinical sense for the input?

---

## Phase D — The LLM + Validation Core

16. `llm/__init__.py`       ← empty
17. `llm/stg_checker.py`
18. `phases/phase3_validator.py`

After Phase D: run end-to-end through Phase 3. Verify on 3-5 test inputs:
- Right candidates survive
- Wrong candidates blocked with correct reason codes
- STG check behaves sensibly (or gracefully falls back when STG missing)
- stg_coverage populated

**Expect to iterate on Phase 3 and llm/stg_checker.py. This is where most bugs live.**

---

## Phase E — Resolution and Output

19. `phases/phase4_multipackage.py`
20. `phases/phase5_financial.py`
21. `phases/phase6_exclusion.py`
22. `phases/phase7_comorbidity.py`
23. `phases/phase8_special_pop.py`
24. `phases/phase9_documents.py`
25. `phases/phase10_output.py`

Build these one at a time. After each one, do a quick import check:
`python -c "from phases.phaseX import run_phaseX; print('OK')"`

---

## Phase F — Wire Up

26. `main.py` (final version with full orchestration)

After Phase F: full end-to-end run:
```bash
python main.py tests/inputs/TC01_happy_path.json
```
Expect: valid IRISOutput JSON printed to stdout.

---

## Test Inputs

Save test case JSONs in `tests/inputs/`:
- `TC01_happy_path_surgical.json`
- `TC02_cardiology_stemi.json`
- `TC03_senior_cataract.json`
- `TC04_portability.json`
- `TC05_wallet_insufficient.json`
- `TC06_special_conditions_rule.json`
- `TC07_oncology_mtb.json`
- `TC08_neonatal_public_hospital.json`
- `TC09_paediatric_chd.json`
- `TC10_cmchis_fast_fail.json`
- `TC11_specialty_not_empanelled.json`
- `TC12_dual_wallet_exhausted.json`
- `TC13_dental_exclusion.json`
- `TC14_burns_multipackage.json`
- `TC15_zero_candidates_usp.json`

Run all after every major change. Re-run is: `for f in tests/inputs/*.json; do python main.py $f | python -m json.tool > /dev/null && echo "$f: OK"; done`

---

## Backtracking Rule

If you discover a missing field in `models.py` while writing any later file — fix `models.py` first, then continue. Never work around a missing model field. The dataclass is the contract.

If a phase file grows beyond 300 lines, split into private helpers inside the same file. Do not create new files mid-build.
