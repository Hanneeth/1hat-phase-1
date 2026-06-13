"""
stubs/bis_stub.py — Simulated Beneficiary Identification System (BIS) API.

Provides mock patient identification, past claims, and wallet balances by
reading from dummy_bis.json.
"""

import json
import logging
from config import DUMMY_BIS_FILE
from models import PatientContext, WalletBalance, PastClaim

logger = logging.getLogger(__name__)


def verify_bis(patient_id: str) -> PatientContext | None:
    """Load dummy_bis.json and find patient context by patient_id.

    Returns:
        PatientContext if found, or None if patient_id doesn't exist.
        NOTE: Will be replaced by real BIS API call. Return type stays the same.
    """
    logger.info("Verifying patient ID '%s' with BIS...", patient_id)
    try:
        data = json.loads(DUMMY_BIS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("DUMMY_BIS_FILE not found at path: %s", DUMMY_BIS_FILE)
        return None
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in DUMMY_BIS_FILE (%s): %s", DUMMY_BIS_FILE, exc)
        raise

    patient_data = data.get(patient_id)
    if not patient_data:
        logger.warning("Patient ID '%s' not found in BIS record", patient_id)
        return None

    # Parse nested WalletBalance
    wallet_data = patient_data.get("wallet", {})
    wallet = WalletBalance(
        family_balance_inr=wallet_data.get("family_balance_inr", 0),
        vay_vandana_balance_inr=wallet_data.get("vay_vandana_balance_inr"),
        policy_year_start=wallet_data.get("policy_year_start", "")
    )

    # Parse list of PastClaim
    past_claims = []
    for claim in patient_data.get("past_claims", []):
        past_claims.append(
            PastClaim(
                procedure_code=claim.get("procedure_code", ""),
                admission_date=claim.get("admission_date", ""),
                package_amount_inr=claim.get("package_amount_inr", 0),
                status=claim.get("status", "")
            )
        )

    logger.info("Successfully loaded BIS patient context for '%s'", patient_id)
    return PatientContext(
        patient_id=patient_data.get("patient_id", ""),
        family_id=patient_data.get("family_id", ""),
        name=patient_data.get("name", ""),
        age=patient_data.get("age", 0),
        gender=patient_data.get("gender", ""),
        home_state=patient_data.get("home_state", ""),
        home_district=patient_data.get("home_district", ""),
        wallet=wallet,
        past_claims=past_claims
    )


def get_wallet_balance(family_id: str) -> WalletBalance | None:
    """Get wallet balance for a family_id by scanning all patient records in dummy_bis.json.

    Returns:
        WalletBalance or None.
        NOTE: Will be replaced by dedicated BIS wallet endpoint.
    """
    logger.info("Retrieving wallet balance for family ID '%s' from BIS...", family_id)
    try:
        data = json.loads(DUMMY_BIS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("DUMMY_BIS_FILE not found at path: %s", DUMMY_BIS_FILE)
        return None
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in DUMMY_BIS_FILE (%s): %s", DUMMY_BIS_FILE, exc)
        raise

    for patient_data in data.values():
        if patient_data.get("family_id") == family_id:
            wallet_data = patient_data.get("wallet", {})
            logger.info("Successfully retrieved wallet balance for family ID '%s'", family_id)
            return WalletBalance(
                family_balance_inr=wallet_data.get("family_balance_inr", 0),
                vay_vandana_balance_inr=wallet_data.get("vay_vandana_balance_inr"),
                policy_year_start=wallet_data.get("policy_year_start", "")
            )

    logger.warning("Family ID '%s' not found in BIS records", family_id)
    return None
