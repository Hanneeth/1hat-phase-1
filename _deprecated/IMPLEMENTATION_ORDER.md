# IRIS — Solo Implementation Order

Build files in this exact order. Each step is buildable and testable before moving to the next. Do not skip ahead.

---

## Phase A — Foundation (everything else depends on this)

1. `config.py`
2. `logger_setup.py`
3. `models.py`
4. `session.py`
5. `input_validator.py` (stub)

After Phase A: you have all data types defined and can import them anywhere.

---

## Phase B — Data Access Layer

6. `kb/__init__.py` (empty)
7. `kb/loader.py`
8. `stubs/__init__.py` (empty)
9. `stubs/bis_stub.py`
10. `stubs/hem_stub.py`

After Phase B: you can load KBs and verify patients/hospitals.

**Test point:** write a 10-line script that loads the index, picks a patient from dummy_bis.json, picks a hospital from dummy_hem.json, prints them. Confirm everything reads correctly.

---

## Phase C — Core Pipeline (Phase 0 → 2)

11. `phases/__init__.py` (empty)
12. `phases/phase0_preflight.py`
13. `phases/phase1_emergency.py`
14. `kb/searcher.py`
15. `phases/phase2_candidates.py`

After Phase C: input JSON → candidates list. Half the pipeline works.

**Test point:** write a temporary `main.py` that runs Phase 0, 1, 2 and prints `session.candidate_packages`. Manually verify candidate quality on 2-3 test inputs.

---

## Phase D — The Hard Part (Phase 3)

16. `llm/__init__.py` (empty)
17. `llm/stg_checker.py`
18. `phases/phase3_validator.py`

After Phase D: candidates → validated packages. This is where most bugs will live. Spend time here.

**Test point:** run end-to-end through Phase 3. Verify on 3-5 test inputs that the right candidates survive and the wrong ones are blocked with correct reasons.

---

## Phase E — Resolution and Output (Phase 4 → 10)

19. `phases/phase4_multipackage.py`
20. `phases/phase5_financial.py`
21. `phases/phase6_exclusion.py`
22. `phases/phase7_comorbidity.py`
23. `phases/phase8_special_pop.py`
24. `phases/phase9_documents.py`
25. `phases/phase10_output.py`

After Phase E: full pipeline works end-to-end.

---

## Phase F — Wire Up

26. `main.py` (final version with full orchestration)

---

## Testing Strategy

After each Phase (A through F), run a quick smoke test before moving on:

- Phase A test: `python -c "from models import *; from session import IRISSession"`
- Phase B test: load + print KB and dummy data
- Phase C test: input JSON → candidates printed
- Phase D test: input JSON → validated packages printed
- Phase E + F test: full input JSON → full IRISOutput JSON

Save 3-5 representative input JSONs in `tests/inputs/` and the expected outputs in `tests/expected/`. Re-run all of them after every change.

---

## What If You Need To Backtrack

If you discover a missing field in `models.py` while writing Phase 3, **fix it in models.py first**, then continue. Do not hack around it. The whole point of this design is that models is the contract.

If a phase file becomes too large (>300 lines), split it into helpers in the same file. Do not create new files mid-build.

---

## Order of Antigravity Prompts

For each numbered file above:

1. Open the file in Antigravity
2. Paste the file's specific prompt (provided separately)
3. Make sure `SYSTEM_DESIGN.md` is in the project root and Antigravity can read it (mention "refer to SYSTEM_DESIGN.md" in the prompt)
4. Review the generated code against the prompt's interface contract
5. Run the smoke test for that phase before moving on
