"""Phase 1 — Emergency routing. STUBBED for MVP."""

import logging
from session import IRISSession

logger = logging.getLogger(__name__)


def run_phase1(session: IRISSession) -> IRISSession:
    """Phase 1 — Emergency routing. STUBBED for MVP.

    Always assumes non-emergency planned admission.

    Steps:
    1. session.is_emergency = False
    2. session.er_package_code = None
    3. session.needs_specialty_package = True
    4. add_flag("EMERGENCY_PHASE_STUBBED",
                "Emergency routing not implemented — assuming planned elective admission",
                "info")
    5. Return session

    TODO (real implementation):
    - Use vitals (BP, GCS, SpO2) + chief_complaints to determine emergency
    - Select ER package (ER001A/ER002A/ER002B/ER003A) based on severity
    - If hospitalisation expected >12h: needs_specialty_package=True, two pre-auths
    - Animal bite → ER003A with payment-after-5th-dose flag
    - Set is_emergency=True and er_package_code accordingly
    """
    logger.info("Phase 1 — Emergency routing: start (stubbed)")

    session.is_emergency = False
    session.er_package_code = None
    session.needs_specialty_package = True

    session.add_flag(
        "EMERGENCY_PHASE_STUBBED",
        "Emergency routing not implemented — assuming planned elective admission",
        "info",
    )

    return session
