"""Phase 8 — special population routing flags.

Adds flags for:
- Neonatal (age == 0 / ≤ 28 days)
- Paediatric (age <= PAEDIATRIC_AGE_MAX)
- Oncology (specialty code in {"MO", "MR", "SC"})
- Transplant (specialty code == "OT")
- Portability (patient.home_state != hospital.state)
"""

import logging
from config import PAEDIATRIC_AGE_MAX
from session import IRISSession

logger = logging.getLogger(__name__)


def _check_age_routing(session: IRISSession) -> None:
    """Check patient age and flag neonatal or paediatric cases."""
    if not session.patient:
        return

    age = session.patient.age
    if age == 0:  # age in PatientContext is integer years; 0 indicates < 1 year (potential neonate)
        session.add_flag(
            "NEONATAL_ESCALATION_RISK",
            "Neonatal case (age ≤28 days): if condition deteriorates, current package must be "
            "UNBLOCKED and higher-level neonatal package booked. Monitor vitals closely.",
            "warning",
        )
    if age <= PAEDIATRIC_AGE_MAX:
        session.add_flag(
            "PAEDIATRIC_DEVICE",
            f"Patient age {age} ≤{PAEDIATRIC_AGE_MAX}: paediatric implants/devices apply where relevant. "
            "TMS auto-detects; overriding triggers medical audit.",
            "info",
        )


def _check_oncology(session: IRISSession) -> None:
    """Check if any oncology package is selected and add warnings/info."""
    oncology_specialties = {"MO", "MR", "SC"}
    has_oncology = any(
        pkg.validated.specialty_code in oncology_specialties
        for pkg in session.final_package_set
    )
    if has_oncology:
        session.add_flag(
            "MTB_REQUIRED",
            "Oncology package selected. Multidisciplinary Tumour Board (MTB) decision is mandatory "
            "before finalising package. If hospital lacks MTB, refer to nearest Regional Cancer Centre (RCC).",
            "warning",
        )
        session.add_flag(
            "ONCOLOGY_MULTI_STAGE",
            "Oncology treatment involves multiple stages (staging/surgery/chemo/radiation). "
            "This IRIS run handles ONE stage only. Each subsequent stage requires a separate run.",
            "info",
        )


def _check_portability(session: IRISSession) -> None:
    """Check for interstate portability cases."""
    if not session.patient or not session.hospital:
        return

    if session.patient.home_state.lower() != session.hospital.state.lower():
        session.add_flag(
            "PORTABILITY_CASE",
            f"Portability case: patient from {session.patient.home_state} treated in {session.hospital.state}. "
            "Claims processing TAT is 30 days (vs 15 for standard). "
            "Home state may reject public-reserved packages booked by private hospitals.",
            "info",
        )


def _check_transplant(session: IRISSession) -> None:
    """Check if any transplant package is selected and add warnings."""
    has_transplant = any(
        pkg.validated.specialty_code == "OT"
        for pkg in session.final_package_set
    )
    if has_transplant:
        session.add_flag(
            "NOTTO_DOCS_REQUIRED",
            "Organ transplant package selected. Both recipient AND donor NOTTO IDs required. "
            "Also required: donor work-up summary, recipient work-up summary, cross-match report, "
            "signed donor undertaking, hospital authorisation letter.",
            "warning",
        )


def run_phase8(session: IRISSession) -> IRISSession:
    """Phase 8 — special population routing flags.

    Checks neonatal/paediatric cases, oncology, portability, and transplant status
    to assign critical guidance flags.

    Args:
        session: IRISSession context

    Returns:
        IRISSession
    """
    logger.info("Phase 8 — special population: start")

    _check_age_routing(session)
    _check_oncology(session)
    _check_portability(session)
    _check_transplant(session)

    logger.info("Phase 8 — special population: complete")
    return session
