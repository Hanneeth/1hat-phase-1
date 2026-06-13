"""Phase 7 — comorbidity resolution.

PM-JAY rule: Management of comorbidities during a surgical admission is INCLUDED
in the surgical package. Separate packages for management conditions are NOT raised.
"""

import logging
from session import IRISSession

logger = logging.getLogger(__name__)

MANAGEMENT_CONDITIONS = {
    "diabetes",
    "type2_diabetes",
    "type1_diabetes",
    "dm",
    "t2dm",
    "t1dm",
    "hypertension",
    "htn",
    "anaemia",
    "anemia",
    "dyslipidaemia",
    "dyslipidemia",
    "hypothyroidism",
    "copd",
    "asthma",
    "chronic_kidney_disease",
    "ckd",
    "obesity",
    "hyperlipidaemia",
    "hyperlipidemia",
}


def _is_management_condition(comorbidity: str) -> bool:
    """Check if the comorbidity is a standard management condition."""
    return comorbidity.strip().lower() in MANAGEMENT_CONDITIONS


def run_phase7(session: IRISSession) -> IRISSession:
    """Phase 7 — comorbidity resolution.

    PM-JAY rule: management of comorbidities during a surgical admission is INCLUDED
    in the surgical package. Separate packages for management conditions are NOT raised.

    Only separate packages when: a comorbidity requires its own surgical intervention
    (handled by Phase 4 combination, not here).

    Steps:
    1. If no comorbidities → return session
    2. Determine if primary admission is surgical:
       surgical_admission = any(pkg.validated.billing_type in {"surgical", "day_care"}
                                for pkg in session.final_package_set
                                if pkg.role == "primary")
    3. For each comorbidity:
       if _is_management_condition(comorbidity):
           if surgical_admission:
               session.comorbidity_notes.append(
                   f"Comorbidity '{comorbidity}' is absorbed in the surgical package — do not raise separately.")
           else:
               session.comorbidity_notes.append(
                   f"Comorbidity '{comorbidity}' managed under current medical package.")
       else:
           session.add_flag("COMORBIDITY_REVIEW_NEEDED",
                            f"Comorbidity '{comorbidity}' may require separate clinical review — not a standard management condition.",
                            "info")
    4. Return session
    """
    logger.info("Phase 7 — comorbidity resolution: start")

    comorbidities = session.clinical.comorbidities
    if not comorbidities:
        logger.info("Phase 7 — no comorbidities present: skipping.")
        return session

    # Determine if primary admission is surgical
    surgical_admission = any(
        pkg.validated.billing_type in {"surgical", "day_care"}
        for pkg in session.final_package_set
        if pkg.role == "primary"
    )

    for c in comorbidities:
        if _is_management_condition(c):
            if surgical_admission:
                session.comorbidity_notes.append(
                    f"Comorbidity '{c}' is absorbed in the surgical package — do not raise separately."
                )
            else:
                session.comorbidity_notes.append(
                    f"Comorbidity '{c}' managed under current medical package."
                )
        else:
            session.add_flag(
                "COMORBIDITY_REVIEW_NEEDED",
                f"Comorbidity '{c}' may require separate clinical review — not a standard management condition.",
                "info",
            )

    logger.info("Phase 7 — comorbidity resolution: complete")
    return session
