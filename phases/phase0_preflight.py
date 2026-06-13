"""
phases/phase0_preflight.py — IRIS Phase 0: Pre-flight gates.

Validates patient eligibility via BIS and hospital empanelment via HEM.
Hard-fails on missing patient, unsupported scheme.
Writes: patient, hospital, patient_eligible, hospital_empanelled, mlc_required.
"""

import logging

from session import IRISSession
from stubs.bis_stub import verify_bis
from stubs.hem_stub import check_empanelment

logger = logging.getLogger(__name__)


def run_phase0(session: IRISSession) -> IRISSession:
    """Phase 0 — Pre-flight gates.

    Performs two sequential BIS / HEM stub calls and applies scheme fast-fail.

    Steps:
        1.  Extract patient_id from session.input_data["patient"]["patient_id"].
        2.  Call verify_bis(patient_id) → set session.patient.
            If None → flag PATIENT_NOT_IN_BIS (block) → return session.
        3.  Set session.patient_eligible = True.
        4.  Extract hospital_id from session.input_data["hospital"]["hospital_id"].
        5.  Call check_empanelment(hospital_id) → set session.hospital.
        6.  Set session.hospital_empanelled = True.
        7.  If session.hospital.scheme != "pmjay" →
                flag SCHEME_NOT_SUPPORTED (block) → return session.
        8.  Set session.mlc_required from session.clinical.is_medico_legal.
        9.  Log INFO summary.
        10. Return session.

    Exception handling:
        Steps 1–2 are wrapped in try/except independently of steps 4–5.
        Steps 4–5 are wrapped in their own try/except.
        Any exception → append "Phase0 error: <e>" to session.errors,
        flag PREFLIGHT_FAILED (block), return session.

    Args:
        session: Shared IRISSession populated with input_data and clinical.

    Returns:
        IRISSession with patient, hospital, patient_eligible,
        hospital_empanelled, and mlc_required set (or a block flag on failure).

    Side effects:
        Writes session.patient, session.hospital, session.patient_eligible,
        session.hospital_empanelled, session.mlc_required.
        Appends to session.flags and session.errors on failure paths.
    """
    logger.info("Phase 0 — Pre-flight gates: start")

    # ------------------------------------------------------------------
    # Steps 1–2: Patient BIS verification
    # ------------------------------------------------------------------
    try:
        patient_id: str = session.input_data["patient"]["patient_id"]
        patient = verify_bis(patient_id)
        session.patient = patient
    except Exception as e:
        session.errors.append(f"Phase0 error: {e}")
        session.add_flag("PREFLIGHT_FAILED", str(e), "block")
        return session

    if session.patient is None:
        session.add_flag("PATIENT_NOT_IN_BIS", "Patient ID not found in BIS", "block")
        return session

    # Step 3: Mark patient as eligible
    session.patient_eligible = True

    # ------------------------------------------------------------------
    # Steps 4–5: Hospital HEM empanelment check
    # ------------------------------------------------------------------
    try:
        hospital_id: str = session.input_data["hospital"]["hospital_id"]
        hospital = check_empanelment(hospital_id)
        session.hospital = hospital
    except Exception as e:
        session.errors.append(f"Phase0 error: {e}")
        session.add_flag("PREFLIGHT_FAILED", str(e), "block")
        return session

    # Step 6: Mark hospital as empanelled
    session.hospital_empanelled = True

    # ------------------------------------------------------------------
    # Step 7: Scheme fast-fail (Critical Rule #2 — PM-JAY only)
    # ------------------------------------------------------------------
    if session.hospital.scheme != "pmjay":
        session.add_flag(
            "SCHEME_NOT_SUPPORTED",
            f"Scheme '{session.hospital.scheme}' not supported. IRIS handles PM-JAY only.",
            "block",
        )
        return session

    # ------------------------------------------------------------------
    # Step 8: MLC flag from clinical input
    # ------------------------------------------------------------------
    session.mlc_required = session.clinical.is_medico_legal

    # ------------------------------------------------------------------
    # Step 9: INFO summary log
    # ------------------------------------------------------------------
    logger.info(
        "Phase 0 complete — Patient: '%s' | Hospital: '%s' | Scheme: '%s' | MLC required: %s",
        session.patient.name,
        session.hospital.name,
        session.hospital.scheme,
        session.mlc_required,
    )

    # Step 10: Return session
    return session
