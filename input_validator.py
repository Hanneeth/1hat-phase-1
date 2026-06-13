def validate_input(raw_json: dict) -> tuple[bool, list[str]]:
    """
    Validates IRIS input JSON structure.

    Returns (True, []) if valid.
    Returns (False, [error_messages]) if invalid.

    STUB: always returns (True, []) for now.
    Full validation to be implemented later.

    TODO — real validation will check:
    - Required top-level keys present: patient, hospital, clinical
    - patient.patient_id is a non-empty string
    - hospital.hospital_id is a non-empty string
    - clinical.chief_complaints is a non-empty string
    - clinical.provisional_diagnosis is a non-empty string
    - clinical.is_emergency is a boolean
    - clinical.investigations is a list (can be empty)
    - clinical.bed_category is null or one of: ward, hdu, icu_no_vent, icu_vent
    - clinical.vitals is a dict (can be empty)
    """
    return (True, [])
