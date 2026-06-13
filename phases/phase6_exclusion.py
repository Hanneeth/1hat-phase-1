"""Phase 6 — exclusion category check.

Keyword matching on clinical text to detect Annexure 6 exclusions.
Adds WARNING flags only — no blocking.
Exceptions require manual review.
"""

import logging
import re
from session import IRISSession

logger = logging.getLogger(__name__)


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    """Check if any of the keywords are present in the text with word boundaries."""
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, text):
            return True
    return False


def run_phase6(session: IRISSession) -> IRISSession:
    """Phase 6 — exclusion category check.

    8 exclusion categories from PM-JAY HBP Guidelines Annexure 6.
    For MVP: keyword matching on clinical text. Adds WARNING flags only — no blocking.
    Exceptions are complex; require manual review.

    Build clinical_text = (chief_complaints + " " + provisional_diagnosis +
                           " " + (notes or "") + " " + (history_of_present_illness or "")).lower()

    Check each category:

    1. OPD-only: "outpatient", "opd", "clinic visit" → flag EXCLUSION_OPD_ONLY_RISK
       Exception: none

    2. Dental: "dental", "tooth", "teeth", "cavity", "root canal" → flag EXCLUSION_DENTAL_RISK
       Exception: "trauma/injury requiring bone treatment — verify exception applies"

    3. Infertility/ART: "infertility", "ivf", "art", "assisted reproductive" → flag EXCLUSION_INFERTILITY_RISK
       Exception: "unless procedure is listed in HBP — verify"

    4. Vaccination: "vaccine", "vaccination", "immunisation", "immunization" → flag EXCLUSION_VACCINATION_RISK
       Exception: none

    5. Cosmetic/Aesthetic: "cosmetic", "aesthetic", "augmentation", "rhinoplasty", "liposuction" → flag EXCLUSION_COSMETIC_RISK
       Exception: "trauma deformity or congenital functional impairment — verify exception applies"

    6. Circumcision under 2 years: "circumcision" AND session.patient.age < 2 → flag EXCLUSION_CIRCUMCISION_RISK
       Exception: "disease or accident — verify exception applies"

    7. Persistent Vegetative State: "persistent vegetative", "pvs" → flag EXCLUSION_PVS_RISK
       Exception: none

    8. Drug rehabilitation: "rehabilitation", "de-addiction", "deaddiction", "detox" → flag EXCLUSION_DRUG_REHAB_RISK
       Exception: "life-threatening condition until stabilisation — verify exception applies. Suicide attempt/alcohol overdose: cover until stable."

    For each match, add_flag(code, message_with_exception_note, "warning").

    Returns:
        IRISSession

    TODO: replace keyword matching with LLM call that can handle exception conditions properly.
    """
    logger.info("Phase 6 — exclusion check: start")

    # Guard: if patient context is not loaded (e.g. preflight blocked), skip age checks and log
    patient_age = session.patient.age if session.patient else 99

    clinical = session.clinical
    parts = [
        clinical.chief_complaints or "",
        clinical.provisional_diagnosis or "",
        clinical.notes or "",
        clinical.history_of_present_illness or "",
    ]
    clinical_text = " ".join(parts).lower()

    # 1. OPD-only
    if _contains_keyword(clinical_text, ["outpatient", "opd", "clinic visit"]):
        session.add_flag(
            "EXCLUSION_OPD_ONLY_RISK",
            "OPD-only treatments are excluded. Case clinical details suggest outpatient care.",
            "warning",
        )

    # 2. Dental
    if _contains_keyword(clinical_text, ["dental", "tooth", "teeth", "cavity", "root canal"]):
        session.add_flag(
            "EXCLUSION_DENTAL_RISK",
            "Dental treatment detected in clinical text. Exception: trauma/injury requiring bone treatment — verify exception applies.",
            "warning",
        )

    # 3. Infertility/ART
    if _contains_keyword(clinical_text, ["infertility", "ivf", "art", "assisted reproductive"]):
        session.add_flag(
            "EXCLUSION_INFERTILITY_RISK",
            "Infertility or assisted reproductive technology (ART) treatment detected. Exception: unless procedure is listed in HBP — verify.",
            "warning",
        )

    # 4. Vaccination
    if _contains_keyword(clinical_text, ["vaccine", "vaccination", "immunisation", "immunization"]):
        session.add_flag(
            "EXCLUSION_VACCINATION_RISK",
            "Vaccination/immunisation detected. Exception: none.",
            "warning",
        )

    # 5. Cosmetic/Aesthetic
    if _contains_keyword(clinical_text, ["cosmetic", "aesthetic", "augmentation", "rhinoplasty", "liposuction"]):
        session.add_flag(
            "EXCLUSION_COSMETIC_RISK",
            "Cosmetic or aesthetic procedure detected. Exception: trauma deformity or congenital functional impairment — verify exception applies.",
            "warning",
        )

    # 6. Circumcision under 2 years
    if _contains_keyword(clinical_text, ["circumcision"]):
        if patient_age < 2:
            session.add_flag(
                "EXCLUSION_CIRCUMCISION_RISK",
                "Circumcision under age 2 detected. Exception: disease or accident — verify exception applies.",
                "warning",
            )

    # 7. Persistent Vegetative State
    if _contains_keyword(clinical_text, ["persistent vegetative", "pvs"]):
        session.add_flag(
            "EXCLUSION_PVS_RISK",
            "Persistent vegetative state (PVS) detected. Exception: none.",
            "warning",
        )

    # 8. Drug rehabilitation
    if _contains_keyword(clinical_text, ["rehabilitation", "de-addiction", "deaddiction", "detox"]):
        session.add_flag(
            "EXCLUSION_DRUG_REHAB_RISK",
            "Drug rehabilitation/de-addiction detected. Exception: life-threatening condition until stabilisation — verify exception applies. Suicide attempt/alcohol overdose: cover until stable.",
            "warning",
        )

    logger.info("Phase 6 — exclusion check: complete")
    return session
