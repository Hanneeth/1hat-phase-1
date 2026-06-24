import logging

logger = logging.getLogger(__name__)


def validate_discharge_schema(discharge_dict: dict) -> tuple[bool, list[str]]:
    """Validate that all hard-required fields are present and valid in the discharge dict.

    Args:
        discharge_dict: Parsed discharge data dictionary.

    Returns:
        A tuple of (is_valid, missing_fields), where:
        - is_valid: True if all hard-required fields are present and valid.
        - missing_fields: List of dot-path strings of fields that failed checks.
        Never raises exceptions.
    """
    missing_fields = []

    # patient.name — must be a non-empty string
    try:
        val = discharge_dict["patient"]["name"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("patient.name")
    except Exception:
        missing_fields.append("patient.name")

    # patient.pmjay_id — must be a non-empty string
    try:
        val = discharge_dict["patient"]["pmjay_id"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("patient.pmjay_id")
    except Exception:
        missing_fields.append("patient.pmjay_id")

    # patient.age — must be an integer greater than 0
    try:
        val = discharge_dict["patient"]["age"]
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            missing_fields.append("patient.age")
    except Exception:
        missing_fields.append("patient.age")

    # patient.gender — must be "M" or "F"
    try:
        val = discharge_dict["patient"]["gender"]
        if val not in ("M", "F"):
            missing_fields.append("patient.gender")
    except Exception:
        missing_fields.append("patient.gender")

    # admission.date_of_admission — must be a non-empty string
    try:
        val = discharge_dict["admission"]["date_of_admission"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("admission.date_of_admission")
    except Exception:
        missing_fields.append("admission.date_of_admission")

    # admission.date_of_discharge — must be a non-empty string
    try:
        val = discharge_dict["admission"]["date_of_discharge"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("admission.date_of_discharge")
    except Exception:
        missing_fields.append("admission.date_of_discharge")

    # admission.actual_los_days — must be an integer greater than 0
    try:
        val = discharge_dict["admission"]["actual_los_days"]
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            missing_fields.append("admission.actual_los_days")
    except Exception:
        missing_fields.append("admission.actual_los_days")

    # admission.ward_category_actual — must be one of: ward, hdu, icu, icu_vent
    try:
        val = discharge_dict["admission"]["ward_category_actual"]
        if val not in ("ward", "hdu", "icu", "icu_vent"):
            missing_fields.append("admission.ward_category_actual")
    except Exception:
        missing_fields.append("admission.ward_category_actual")

    # admission.discharge_status — must be one of: recovered, lama, referred, died
    try:
        val = discharge_dict["admission"]["discharge_status"]
        if val not in ("recovered", "lama", "referred", "died"):
            missing_fields.append("admission.discharge_status")
    except Exception:
        missing_fields.append("admission.discharge_status")

    # clinical.final_diagnosis_at_discharge — must be a non-empty string
    try:
        val = discharge_dict["clinical"]["final_diagnosis_at_discharge"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("clinical.final_diagnosis_at_discharge")
    except Exception:
        missing_fields.append("clinical.final_diagnosis_at_discharge")

    # clinical.final_procedure_performed — must be a non-empty string
    try:
        val = discharge_dict["clinical"]["final_procedure_performed"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("clinical.final_procedure_performed")
    except Exception:
        missing_fields.append("clinical.final_procedure_performed")

    # clinical.discharge_summary_text — must be a non-empty string with at least 50 characters
    try:
        val = discharge_dict["clinical"]["discharge_summary_text"]
        if not isinstance(val, str) or len(val) < 50:
            missing_fields.append("clinical.discharge_summary_text")
    except Exception:
        missing_fields.append("clinical.discharge_summary_text")

    # treating_consultant.name — must be a non-empty string
    try:
        val = discharge_dict["treating_consultant"]["name"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("treating_consultant.name")
    except Exception:
        missing_fields.append("treating_consultant.name")

    # treating_consultant.registration_number — must be a non-empty string
    try:
        val = discharge_dict["treating_consultant"]["registration_number"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("treating_consultant.registration_number")
    except Exception:
        missing_fields.append("treating_consultant.registration_number")

    # hospital.name — must be a non-empty string
    try:
        val = discharge_dict["hospital"]["name"]
        if not isinstance(val, str) or not val.strip():
            missing_fields.append("hospital.name")
    except Exception:
        missing_fields.append("hospital.name")

    if not missing_fields:
        logger.info("discharge schema validation passed")
        return True, []

    logger.warning(
        "discharge schema validation failed: %d missing field(s): %s",
        len(missing_fields),
        ", ".join(missing_fields),
    )
    return False, missing_fields
