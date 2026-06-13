"""
stubs/hem_stub.py — Simulated Hospital Empanelment Module (HEM) API.

Provides mock hospital context and accreditation details by reading from dummy_hem.json,
with a fallback to a default configuration when a hospital_id is not found.
"""

import json
import logging
from config import DUMMY_HEM_FILE
from models import HospitalContext

logger = logging.getLogger(__name__)


def check_empanelment(hospital_id: str) -> HospitalContext:
    """Load dummy_hem.json and find hospital by hospital_id.

    ALWAYS returns a HospitalContext (never None) — MVP assumes always empanelled.

    If hospital_id is not found, a fallback HospitalContext is returned and a
    WARNING is logged.

    NOTE: Real HEM API will return None if not empanelled. Caller (Phase 0) must
    handle that when real API is wired in.
    """
    logger.info("Checking empanelment for hospital ID '%s' with HEM...", hospital_id)
    try:
        data = json.loads(DUMMY_HEM_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("DUMMY_HEM_FILE not found at %s. Proceeding to fallback.", DUMMY_HEM_FILE)
        data = {}
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in DUMMY_HEM_FILE (%s): %s", DUMMY_HEM_FILE, exc)
        raise

    hospital_data = data.get(hospital_id)
    if not hospital_data:
        logger.warning(
            "Hospital ID '%s' not found in HEM database. Using unknown hospital fallback context.",
            hospital_id,
        )
        return HospitalContext(
            hospital_id=hospital_id,
            name="Unknown Hospital",
            type="private",
            city_tier="tier1",
            state="Tamil Nadu",
            district="Chennai",
            is_aspirational_district=False,
            accreditation="nabh_full",
            scheme="pmjay",
            empanelled_specialties=["SG", "MC", "SB", "MG", "SO", "SU", "BM", "SN", "MR", "SC", "MO"],
        )

    logger.info("Successfully loaded HEM hospital context for '%s'", hospital_id)
    return HospitalContext(
        hospital_id=hospital_data.get("hospital_id", ""),
        name=hospital_data.get("name", ""),
        type=hospital_data.get("type", ""),
        city_tier=hospital_data.get("city_tier", ""),
        state=hospital_data.get("state", ""),
        district=hospital_data.get("district", ""),
        is_aspirational_district=bool(hospital_data.get("is_aspirational_district", False)),
        accreditation=hospital_data.get("accreditation", ""),
        scheme=hospital_data.get("scheme", ""),
        empanelled_specialties=hospital_data.get("empanelled_specialties", []),
    )
