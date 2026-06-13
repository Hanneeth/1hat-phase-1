# test_phase_c.py
from logger_setup import setup_logging
from session import IRISSession
from models import ClinicalInput
from phases.phase0_preflight import run_phase0
from phases.phase1_emergency import run_phase1
from phases.phase2_candidates import run_phase2

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
    is_medico_legal=False,
    chief_complaints="chest pain radiating to left arm",
    duration_days=1,
    history_of_present_illness=None,
    provisional_diagnosis="acute STEMI inferior wall",
    planned_procedure=None,
    weight_kg=None,
    height_cm=None,
    vitals={},
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

print(f"Candidates: {len(session.candidate_packages)}")
for c in session.candidate_packages[:5]:
    print(f"  {c.procedure_code} | {c.package_name} | score={c.match_score:.1f}")
print(f"Flags: {[f.code for f in session.flags]}")