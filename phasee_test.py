# phasee_test.py — Phase E smoke test (Phases 4 through 10)
import json
from logger_setup import setup_logging
from session import IRISSession
from models import ClinicalInput, Investigation, StructuredValue, DocumentInHand, TreatingDoctor
from phases.phase0_preflight import run_phase0
from phases.phase1_emergency import run_phase1
from phases.phase2_candidates import run_phase2
from phases.phase3_validator import run_phase3
from phases.phase4_multipackage import run_phase4
from phases.phase5_financial import run_phase5
from phases.phase6_exclusion import run_phase6
from phases.phase7_comorbidity import run_phase7
from phases.phase8_special_pop import run_phase8
from phases.phase9_documents import run_phase9
from phases.phase10_output import run_phase10, serialize_output

setup_logging()

# ── Clinical input ──────────────────────────────────────────────────────────
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
    investigations=[
        Investigation(
            type="blood_reports",
            result_summary="CBC within normal limits",
            structured_values=[
                StructuredValue(parameter="Hb", value=12.4, unit="g/dL", flag="N", leads=None)
            ],
            document_available=True,
            report_date="2026-06-13"
        )
    ],
    comorbidities=["type2_diabetes"],
    past_medical_history="Type 2 diabetes, well controlled",
    past_surgical_history=None,
    current_medications=["Metformin 500mg BD"],
    non_clinical_documents_in_hand=[
        DocumentInHand(key="clinical_notes", label="Clinical notes", available=True),
        DocumentInHand(key="patient_photo", label="Patient photo", available=True),
        DocumentInHand(key="mlc_fir", label="MLC copy", available=True),
    ],
    treating_doctor=TreatingDoctor(
        name="Dr. Suresh Babu",
        registration_number="TN-MED-12345",
        qualification="MS General Surgery",
        specialty_code="BM"
    )
)

# ── Run pipeline ─────────────────────────────────────────────────────────────
session = IRISSession(input_data=raw, clinical=clinical)
session = run_phase0(session)
session = run_phase1(session)
session = run_phase2(session)
session = run_phase3(session)

# Phase 3 USP routing (mirrors main.py logic)
if len(session.validated_packages) == 0:
    session.usp_recommended = True
    session.add_flag(
        "USP_RECOMMENDED",
        "No standard PM-JAY packages validated. USP pathway may apply.",
        "warning"
    )
    session = run_phase9(session)
    output = run_phase10(session)
else:
    session = run_phase4(session)
    session = run_phase5(session)
    session = run_phase6(session)
    session = run_phase7(session)
    session = run_phase8(session)
    session = run_phase9(session)
    output = run_phase10(session)

# ── Results ──────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("PHASE E SMOKE TEST RESULTS")
print("="*60)

print(f"\n[PHASE 3] validated={len(session.validated_packages)} | blocked={len(session.phase3_blocked)}")
print(f"[PHASE 3] stg_coverage={session.stg_coverage}")

print(f"\n[PHASE 4] final_package_set={len(session.final_package_set)}")
for fp in session.final_package_set:
    print(f"  {fp.validated.procedure_code} | {fp.validated.package_name[:40]} | "
          f"role={fp.role} | factor={fp.deduction_factor} | group={fp.pre_auth_group}")

print(f"\n[PHASE 5] wallet_sufficient={session.wallet_sufficient} | "
      f"estimated_total={session.estimated_total_inr} | "
      f"copayment_required={session.copayment_required}")
if session.copayment_gap_inr:
    print(f"  copayment_gap=₹{session.copayment_gap_inr}")

print(f"\n[PHASE 7] comorbidity_notes={session.comorbidity_notes}")

print(f"\n[PHASE 9] docs_required={len(session.preauth_docs_required)} | "
      f"docs_missing={len(session.preauth_docs_missing)}")
for doc in session.preauth_docs_required:
    status = "✓" if doc.available else "✗"
    print(f"  {status} [{doc.criticality}] {doc.key} — {doc.label[:50]}")

print(f"\n[PHASE 10] readiness_status={output.readiness_status}")
print(f"[PHASE 10] selected_packages={len(output.selected_packages)}")
print(f"[PHASE 10] blocked_candidates={len(output.blocked_candidates)}")
print(f"[PHASE 10] enhancement_plan={len(output.enhancement_plan)}")

print(f"\n[FLAGS] ({len(output.flags)} total)")
for f in output.flags:
    print(f"  [{f.severity.upper()}] {f.code}")

print(f"\n[ERRORS] ({len(output.errors)} total)")
for e in output.errors:
    print(f"  {e}")

# ── Assertions ───────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("ASSERTIONS")
print("="*60)

checks = [
    ("Phase 4 ran — final_package_set exists",
     session.final_package_set is not None),

    ("Phase 5 ran — wallet_sufficient is bool",
     isinstance(session.wallet_sufficient, bool)),

    ("Phase 9 ran — preauth_docs_required is list",
     isinstance(session.preauth_docs_required, list)),

    ("Phase 10 ran — readiness_status is set",
     output.readiness_status in {"READY", "READY_WITH_WARNINGS", "CONDITIONAL", "BLOCKED"}),

    ("No INTERNAL_ERROR in blocked candidates",
     not any(b.get("reason_code") == "INTERNAL_ERROR"
             for b in session.phase3_blocked)),

    ("No pipeline errors",
     len(output.errors) == 0),

    ("Output serializes to JSON without crash",
     bool(json.dumps(serialize_output(output), default=str))),
]

all_passed = True
for name, result in checks:
    status = "PASS" if result else "FAIL"
    if not result:
        all_passed = False
    print(f"  [{status}] {name}")

print(f"\n{'ALL ASSERTIONS PASSED' if all_passed else 'SOME ASSERTIONS FAILED'}")
print("="*60)