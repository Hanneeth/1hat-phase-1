"""Phase 6 — exclusion category check.

Keyword matching on clinical text to detect Annexure 6 exclusions.
Adds WARNING flags only — no blocking.
Exceptions require manual review.
"""

import json
import logging
import os
import re

from dotenv import load_dotenv  # pyrefly: ignore [missing-import]
from google import genai  # pyrefly: ignore [missing-import]

from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES
from session import IRISSession

load_dotenv()

EXCLUSION_CONFIG = {
    1: {
        "name": "OPD-only",
        "flag_code": "EXCLUSION_OPD_ONLY_RISK",
        "keywords": ["outpatient", "opd", "clinic visit"],
        "group": "C",
        "exception_text": None,
        "exception_note": (
            "No exception — OPD-only conditions are always excluded."
        ),
    },
    2: {
        "name": "Dental",
        "flag_code": "EXCLUSION_DENTAL_RISK",
        "keywords": [
            "dental", "tooth", "teeth", "cavity", "root canal",
            "periodontal", "dental implant", "molar", "incisor",
            "premolar", "gingival", "pulp chamber", "periapical",
        ],
        "group": "A",
        "exception_text": (
            "Exception: treatment needs arising from trauma / injury, "
            "neoplasia / tumour / cyst requiring hospitalization for bone "
            "treatment. Source: PM-JAY Annexure 5 (CAM 2026 and HBP "
            "Guidelines 2026)."
        ),
        "exception_note": (
            "Exception applies for trauma/injury OR neoplasia/tumour/cyst "
            "requiring bone treatment — LLM evaluated."
        ),
    },
    3: {
        "name": "Infertility/ART",
        "flag_code": "EXCLUSION_INFERTILITY_RISK",
        "keywords": [
            "infertility", "ivf", "assisted reproductive",
            "in vitro fertilisation", "in vitro fertilization",
            "embryo transfer", "ovarian stimulation",
        ],
        "group": "B",
        "exception_text": (
            "Exception: procedure is featured in the National Health Benefit "
            "Package list. Source: PM-JAY Annexure 5."
        ),
        "exception_note": (
            "Exception applies if procedure is listed in HBP — verify manually."
        ),
    },
    4: {
        "name": "Vaccination",
        "flag_code": "EXCLUSION_VACCINATION_RISK",
        "keywords": [
            "vaccine", "vaccination", "immunisation", "immunization",
            "booster dose", "inoculation",
        ],
        "group": "C",
        "exception_text": None,
        "exception_note": (
            "No exception — vaccination and immunisation are always excluded."
        ),
    },
    5: {
        "name": "Cosmetic/Aesthetic",
        "flag_code": "EXCLUSION_COSMETIC_RISK",
        "keywords": [
            "cosmetic", "aesthetic", "augmentation", "rhinoplasty",
            "liposuction", "fat grafting", "neck lift", "tattoo removal",
            "facelift", "blepharoplasty",
        ],
        "group": "A",
        "exception_text": (
            "Exception: treatment arising from disease or injury requiring "
            "hospitalisation for treatment. Source: CAM 2026 section l "
            "(PPD guidelines), PM-JAY Annexure 5."
        ),
        "exception_note": (
            "Exception applies for disease or injury requiring "
            "hospitalisation — LLM evaluated."
        ),
    },
    6: {
        "name": "Circumcision under 2 years",
        "flag_code": "EXCLUSION_CIRCUMCISION_RISK",
        "keywords": ["circumcision"],
        "group": "B",
        "exception_text": (
            "Exception: necessary for treatment of a disease not excluded "
            "hereunder, or as may be necessitated due to any accident. "
            "Source: PM-JAY Annexure 5."
        ),
        "exception_note": (
            "Exception applies if circumcision is necessitated by disease "
            "or accident — verify."
        ),
    },
    7: {
        "name": "Persistent Vegetative State",
        "flag_code": "EXCLUSION_PVS_RISK",
        "keywords": ["persistent vegetative", "pvs", "vegetative state"],
        "group": "C",
        "exception_text": None,
        "exception_note": "No exception — PVS is always excluded.",
    },
    8: {
        "name": "Drug rehabilitation",
        "flag_code": "EXCLUSION_DRUG_REHAB_RISK",
        "keywords": [
            "rehabilitation", "de-addiction", "deaddiction", "detox",
            "substance abuse treatment", "alcohol rehabilitation",
            "drug rehabilitation",
        ],
        "group": "A",
        "exception_text": (
            "Exception: for life-threatening cases e.g. suicide attempt or "
            "accident due to excess consumption of alcohol, treatment shall "
            "be provided by the hospital till the patient's condition "
            "stabilises. Source: CAM 2026 section l (PPD guidelines)."
        ),
        "exception_note": (
            "Exception applies for life-threatening cases until stabilisation "
            "— LLM evaluated."
        ),
    },
    9: {
        "name": "Hormone replacement/sex change",
        "flag_code": "EXCLUSION_SEX_CHANGE_RISK",
        "keywords": [
            "hormone replacement therapy", "sex change",
            "gender reassignment", "gender affirmation surgery",
            "transgender surgery",
        ],
        "group": "C",
        "exception_text": None,
        "exception_note": (
            "No exception — hormone replacement therapy for sex change and "
            "treatments related to sex change are excluded. "
            "Source: CAM 2026 section l (PPD guidelines)."
        ),
    },
}

logger = logging.getLogger(__name__)


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    """Check if any of the keywords are present in the text with word boundaries."""
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, text):
            return True
    return False


def run_phase6(session: IRISSession) -> IRISSession:
    pass


def _parse_llm_exclusion_response(raw_text: str) -> dict | None:
    logger.warning("Phase 6 LLM raw response text: %r", raw_text)

    parsed = None
    strategy_number = 0

    # STRATEGY 1 — Direct parse
    text_s1 = raw_text.strip()
    try:
        res = json.loads(text_s1)
        if isinstance(res, dict):
            parsed = res
            strategy_number = 1
    except json.JSONDecodeError:
        pass

    # STRATEGY 2 — Strip markdown fences
    if parsed is None:
        lines = raw_text.splitlines()
        clean_lines = [line for line in lines if not line.strip().startswith("```")]
        text_s2 = "\n".join(clean_lines).strip()
        try:
            res = json.loads(text_s2)
            if isinstance(res, dict):
                parsed = res
                strategy_number = 2
        except json.JSONDecodeError:
            pass

    # STRATEGY 3 — Extract JSON object by brace scanning
    if parsed is None:
        first_brace = raw_text.find('{')
        last_brace = raw_text.rfind('}')
        if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
            text_s3 = raw_text[first_brace:last_brace + 1]
            try:
                res = json.loads(text_s3)
                if isinstance(res, dict):
                    parsed = res
                    strategy_number = 3
            except json.JSONDecodeError:
                pass

    if parsed is None:
        return None

    logger.info("Phase 6 LLM: JSON extracted via strategy %d", strategy_number)

    # VALIDATION
    if "exclusion_applies" not in parsed or not isinstance(parsed["exclusion_applies"], bool):
        return None

    if "exception_applies" not in parsed or not isinstance(parsed["exception_applies"], bool):
        return None

    if "affected_package_codes" not in parsed or not isinstance(parsed["affected_package_codes"], list):
        return None

    parsed.setdefault("reason", "")
    return parsed


def _check_exclusion_with_llm(
    category_name: str,
    exception_text: str,
    clinical_text: str,
    final_packages: list[dict],
) -> dict | None:
    """Evaluate whether a PM-JAY exclusion applies and which packages are 
    affected.

    Called only for Group A categories (Dental, Cosmetic, Drug rehab) when 
    keyword detection fires and session.final_package_set is non-empty.

    Args:
        category_name: Human-readable exclusion category name.
        exception_text: Official PM-JAY Annexure 5 exception text for this 
            category.
        clinical_text: Full patient clinical text (mixed case, not lowercased).
        final_packages: List of dicts with procedure_code and package_name 
            for each package currently in session.final_package_set.

    Returns:
        dict with keys:
            exclusion_applies (bool): True if root condition is excluded.
            exception_applies (bool): True if official exception is met.
            affected_package_codes (list[str]): Codes of packages treating 
                the excluded condition or its direct complication.
            reason (str): One sentence explanation, max 150 characters.
        None if all LLM retries exhausted — caller treats as fail-open.
    """
    logger.info(
        "Phase 6 LLM exclusion check: category=%s, packages=%d",
        category_name,
        len(final_packages),
    )

    SYSTEM_PROMPT = (
        "You are a clinical exclusion analyst for IRIS, an AI-powered "
        "PM-JAY pre-authorization engine.\n\n"
        "PM-JAY defines certain exclusion categories under Annexure 5. Some "
        "exclusions have official exceptions. Your task is to determine whether "
        "an identified exclusion applies to a patient's ROOT clinical condition, "
        "whether the official exception applies, and which specific packages in "
        "the pre-authorization are treating the excluded condition or its direct "
        "complication.\n\n"
        "Rules:\n"
        "1. Evaluate the ROOT condition — not the presenting complication. "
        "Example: a patient with dental caries whose abscess spread to the neck "
        "has a dental condition as the root cause. The neck drainage procedure "
        "is treating a direct complication of the excluded dental condition. "
        "Both the root and the complication are excluded.\n"
        "2. Apply the exception PRECISELY as written. Do not extend or narrow it. "
        "If the exception requires bone treatment and there is no documented bone "
        "involvement, the exception does not apply.\n"
        "3. For affected_package_codes: only include packages from the provided "
        "list that directly treat the excluded condition OR a direct complication "
        "of it. Do not include packages treating unrelated conditions in the same "
        "admission.\n"
        "4. If the exclusion does NOT apply to this patient's root condition, "
        "return exclusion_applies=false and an empty affected_package_codes.\n"
        "5. Respond with valid JSON only. No prose outside the JSON.\n\n"
        "Response schema:\n"
        "{\n"
        '  "exclusion_applies": true or false,\n'
        '  "exception_applies": true or false,\n'
        '  "affected_package_codes": ["CODE1", "CODE2"],\n'
        '  "reason": "one sentence max 150 characters"\n'
        "}"
    )

    user_prompt = (
        f"EXCLUSION CATEGORY: {category_name}\n\n"
        f"OFFICIAL EXCEPTION TEXT:\n{exception_text}\n\n"
        f"PATIENT CLINICAL SUMMARY:\n{clinical_text}\n\n"
        "PACKAGES CURRENTLY IN PRE-AUTHORIZATION:\n"
    )
    for pkg in final_packages:
        user_prompt += f"- {pkg['procedure_code']}: {pkg['package_name']}\n"
    user_prompt += (
        "\nDoes this exclusion apply to the root clinical condition? "
        "Does the official exception apply? Which packages are treating "
        "the excluded condition or its direct complication? "
        "Respond with JSON only."
    )

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0,
                    max_output_tokens=1024,
                    response_mime_type="application/json",
                    http_options=genai.types.HttpOptions(
                        timeout=LLM_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )

            raw_text = response.text
            logger.debug("Phase 6 raw LLM response (attempt %d): %r", attempt, raw_text)

            result = _parse_llm_exclusion_response(raw_text)
            if result is not None:
                logger.info(
                    "Phase 6 LLM result: exclusion_applies=%s, exception_applies=%s, affected=%s",
                    result["exclusion_applies"],
                    result["exception_applies"],
                    result["affected_package_codes"],
                )
                return result

            logger.warning(
                "Phase 6 LLM: invalid response on attempt %d/%d",
                attempt,
                LLM_MAX_RETRIES,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Phase 6 LLM: API error on attempt %d/%d: %s",
                attempt,
                LLM_MAX_RETRIES,
                exc,
            )

    logger.error(
        "Phase 6 LLM: all %d retries exhausted for %s — returning None",
        LLM_MAX_RETRIES,
        category_name,
    )
    return None


def run_phase6(session: IRISSession) -> IRISSession:
    """Phase 6 — exclusion category check.

    Uses EXCLUSION_CONFIG to drive detection. Calls LLM for Group A,
    removes packages from session.final_package_set when exclusion applies without exception.
    """
    logger.info("Phase 6 — exclusion check: start")
    patient_age = session.patient.age if session.patient else 99
    clinical = session.clinical
    parts = [
        clinical.chief_complaints or "",
        clinical.provisional_diagnosis or "",
        clinical.notes or "",
        clinical.history_of_present_illness or "",
        clinical.planned_procedure or "",
    ]
    clinical_text_lower = " ".join(parts).lower()
    clinical_text_original = " ".join(parts)

    for cat_id, config in EXCLUSION_CONFIG.items():
        # STEP A — keyword check:
        if not _contains_keyword(clinical_text_lower, config["keywords"]):
            continue

        # STEP B — age gate for circumcision (cat_id == 6 only):
        if cat_id == 6 and patient_age >= 2:
            continue

        # STEP C — route by group:
        if config["group"] == "C":
            session.add_flag(
                config["flag_code"],
                f"{config['name']} detected in clinical text. "
                f"{config['exception_note']}",
                "warning",
            )
            logger.info("Phase 6 — GROUP C flag: %s", config["flag_code"])
            continue

        elif config["group"] == "B":
            if cat_id == 3:  # Infertility
                session.add_flag(
                    config["flag_code"],
                    f"{config['name']} detected. {config['exception_note']}",
                    "warning",
                )
                logger.info("Phase 6 — GROUP B flag: %s", config["flag_code"])
                continue
            elif cat_id == 6:  # Circumcision
                has_exception_signal = _contains_keyword(
                    clinical_text_lower, ["disease", "accident", "injury"]
                )
                if has_exception_signal:
                    msg = (
                        f"Circumcision under age 2 detected. "
                        f"Exception signal present (disease/accident keyword found) "
                        f"— verify: {config['exception_note']}"
                    )
                else:
                    msg = (
                        f"Circumcision under age 2 detected. No exception signal "
                        f"found. {config['exception_note']}"
                    )
                session.add_flag(config["flag_code"], msg, "warning")
                logger.info("Phase 6 — GROUP B circumcision flag (age=%d)", patient_age)
                continue

        elif config["group"] == "A":
            # SUB-STEP A1 — check if there is anything to block:
            if not session.final_package_set:
                session.add_flag(
                    config["flag_code"],
                    f"{config['name']} detected but no packages in pre-auth "
                    f"to evaluate. {config['exception_note']}",
                    "warning",
                )
                logger.info(
                    "Phase 6 — GROUP A keyword match but final_package_set empty: %s",
                    config["flag_code"],
                )
                continue

            # SUB-STEP A2 — build package list for LLM:
            final_packages = [
                {
                    "procedure_code": fp.validated.procedure_code,
                    "package_name": fp.validated.package_name,
                }
                for fp in session.final_package_set
            ]
            logger.info(
                "Phase 6 — calling LLM for %s exclusion check (%d package(s))",
                config["name"],
                len(final_packages),
            )

            # SUB-STEP A3 — call LLM:
            llm_result = _check_exclusion_with_llm(
                category_name=config["name"],
                exception_text=config["exception_text"],
                clinical_text=clinical_text_original,
                final_packages=final_packages,
            )

            # SUB-STEP A4 — handle LLM result:
            if llm_result is None:
                logger.warning(
                    "Phase 6 — LLM failed for %s — fail-open, packages retained",
                    config["name"],
                )
                session.add_flag(
                    config["flag_code"],
                    f"{config['name']} detected — LLM evaluation failed, "
                    f"manual review required. {config['exception_note']}",
                    "warning",
                )
                continue

            if llm_result.get("exclusion_applies") is False:
                logger.info(
                    "Phase 6 — %s exclusion does not apply: %s",
                    config["name"],
                    llm_result.get("reason", ""),
                )
                continue

            # Case: exclusion_applies True AND exception_applies True:
            if llm_result.get("exception_applies") is True:
                logger.info(
                    "Phase 6 — %s exclusion detected but exception applies: %s",
                    config["name"],
                    llm_result.get("reason", ""),
                )
                session.add_flag(
                    config["flag_code"],
                    f"{config['name']} exclusion detected — exception verified: "
                    f"{llm_result.get('reason', '')} — packages retained.",
                    "warning",
                )
                continue

            # Case: exclusion_applies True AND exception_applies False:
            affected_codes = set(llm_result.get("affected_package_codes", []))

            if not affected_codes:
                logger.warning(
                    "Phase 6 — %s exclusion confirmed but no packages identified "
                    "— warning only",
                    config["name"],
                )
                session.add_flag(
                    config["flag_code"],
                    f"{config['name']} exclusion applies: "
                    f"{llm_result.get('reason', '')} — no packages identified, "
                    f"manual review required.",
                    "warning",
                )
                continue

            # Otherwise — remove affected packages:
            before_count = len(session.final_package_set)
            session.final_package_set = [
                fp for fp in session.final_package_set
                if fp.validated.procedure_code not in affected_codes
            ]
            removed_count = before_count - len(session.final_package_set)

            removed_names = [
                pkg["package_name"]
                for pkg in final_packages
                if pkg["procedure_code"] in affected_codes
            ]

            logger.warning(
                "Phase 6 — %s exclusion confirmed, removed %d package(s): %s. "
                "Reason: %s",
                config["name"],
                removed_count,
                affected_codes,
                llm_result.get("reason", ""),
            )

            session.add_flag(
                config["flag_code"] + "_BLOCKED",
                (
                    f"{config['name']} exclusion applies — no exception. "
                    f"Removed from pre-auth: {', '.join(removed_names)}. "
                    f"Reason: {llm_result.get('reason', '')}"
                ),
                "block",
            )
            continue

    logger.info("Phase 6 — exclusion check: complete")
    return session
