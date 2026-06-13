# phased_test.py
from logger_setup import setup_logging
from session import IRISSession
from models import ClinicalInput
from phases.phase0_preflight import run_phase0
from phases.phase1_emergency import run_phase1
from phases.phase2_candidates import run_phase2
from phases.phase3_validator import run_phase3

setup_logging()

raw = {
    "patient": {"patient_id": "P001"},
    "hospital": {"hospital_id": "H001"},
    "clinical": {}
}

clinical = ClinicalInput(
    admission_date="2026-06-13",
    bed_category=None,
    is_emergency=False,
    is_medico_legal=True,
    chief_complaints="burns on chest and abdomen, 15 percent TBSA thermal burns",
    duration_days=0,
    history_of_present_illness="Patient sustained thermal burns from kitchen fire",
    provisional_diagnosis="thermal burns 15 percent TBSA",
    planned_procedure="burns dressing and management",
    weight_kg=65.0,
    height_cm=165.0,
    vitals={"bp_systolic_mmhg": 110, "pulse_bpm": 98, "spo2_pct": 97},
    examination_findings=None,
    investigations=[],
    comorbidities=[],
    past_medical_history=None,
    past_surgical_history=None,
)

session = IRISSession(input_data=raw, clinical=clinical)
session = run_phase0(session)
session = run_phase1(session)
session = run_phase2(session)
session = run_phase3(session)

print(f"\n=== PHASE D RESULTS ===")
print(f"Candidates going in:  {len(session.candidate_packages)}")
print(f"Validated:            {len(session.validated_packages)}")
print(f"Blocked:              {len(session.phase3_blocked)}")
print(f"STG coverage:         {session.stg_coverage}")
print(f"\nValidated packages:")
for v in session.validated_packages:
    print(f"  {v.procedure_code} | {v.package_name} | billing_type={v.billing_type} | stg_eligible={v.stg_eligible} | flags={v.flags}")
print(f"\nBlocked candidates:")
for b in session.phase3_blocked:
    print(f"  {b['procedure_code']} | {b['reason_code']} | {b['message'][:80]}")
print(f"\nAll flags: {[f.code for f in session.flags]}")
print(f"Errors: {session.errors}")