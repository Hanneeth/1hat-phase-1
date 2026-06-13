"""
phases/phase3_validator.py — IRIS Phase 3: Per-Candidate Validation
=====================================================================
For each candidate produced by Phase 2, this phase loads the full KB-2
procedure record, applies all hard eligibility rules, calls the LLM STG
checker, resolves stratification and implant details, and pre-computes
enhancement requests.

Survivors are written to session.validated_packages.
Blocked candidates are written to session.phase3_blocked.

References (SYSTEM_DESIGN.md):
  - "KB-2 HBP Shard Schema" — all field names used here
  - "Billing Type Classification" — primary + fallback logic
  - "Critical Rules" — blocking rules 1, 3, 4 (rules 5-10 are Phase 4)
  - "KB-3 STG Schema" — STG field types used in LLM call
  - "LLM Usage Policy" — fail-open on LLM failure
"""

from math import ceil
import logging

from session import IRISSession
from models import (
    CandidatePackage,
    ClinicalInput,
    Flag,
    ImplantResult,
    StratificationResult,
    ValidatedPackage,
)
from kb.loader import load_specialty_shard, get_procedure_from_shard, load_stg
from llm.stg_checker import check_stg_eligibility
from config import (
    ENHANCEMENT_BATCH_PRIVATE,
    ENHANCEMENT_BATCH_PUBLIC,
    NE_STATES_AND_ISLANDS,
    PAEDIATRIC_AGE_MAX,
    REQUIRE_STG_FOR_VALIDATION,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Specialty → shard filename mapping (from SYSTEM_DESIGN.md)
# ---------------------------------------------------------------------------

SPECIALTY_CODE_TO_SHARD: dict[str, str] = {
    "BM": "burns_management",
    "MC": "cardiology",
    "SV": "ctvs",
    "ER": "emergency_room_packages",
    "MG": "general_medicine",
    "SG": "general_surgery",
    "ID": "infectious_diseases",
    "IR": "interventional_radiology",
    "MO": "medical_oncology",
    "MM": "mental_disorders",
    "MN": "neonatal_care",
    "SN": "neurosurgery",
    "SO": "obstetrics_gynaecology",
    "SE": "ophthalmology",
    "SM": "oral_maxillofacial",
    "OT": "organ_transplant",
    "SB": "orthopaedics",
    "SL": "ent",
    "SS": "paediatric_surgery",
    "SP": "plastic_reconstructive",
    "ST": "polytrauma",
    "MR": "radiation_oncology",
    "SC": "surgical_oncology",
    "SU": "urology",
    "PM": "palliative_medicine",
    "HM": "high_end_medicine",
    "HD": "high_end_diagnostics",
    "HP": "high_end_procedures",
    "IN": "interventional_neuroradiology",
    "PHCnCHC": "primary_care",
    "HRP": "hrp",
    "US": "unspecified_surgical",
}

# Bed-category stratum IDs used by per_day packages (SYSTEM_DESIGN.md stratification)
_BED_CATEGORY_STRATUM_IDS: frozenset[str] = frozenset(
    {"ward", "hdu", "icu_no_vent", "icu_vent"}
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_phase3(session: IRISSession) -> IRISSession:
    """Phase 3 — per-candidate validation.

    Iterates over session.candidate_packages. For each candidate:
      1. Load full procedure record from KB-2 shard
      2. Apply public-hospital reservation check
      3. Classify billing type
      4. Run LLM STG eligibility check
      5. Resolve stratification
      6. Determine implant requirement
      7. Check special conditions rule against past claims
      8. Pre-compute enhancement requests
      9. Build ValidatedPackage and append to session.validated_packages

    Blocked candidates (any step that fails hard) are appended to
    session.phase3_blocked as {procedure_code, reason_code, message} dicts.

    Per-candidate exceptions are caught, logged as ERROR, appended to
    session.errors, and blocked with reason_code="INTERNAL_ERROR" so the
    loop continues for remaining candidates.

    After the loop:
      - session.stg_coverage is finalised (already incremented in-loop)
      - If session.validated_packages is empty, adds a WARNING flag
        NO_VALIDATED_PACKAGES (main.py then sets usp_recommended=True)

    Args:
        session: IRISSession with candidate_packages, patient, hospital, and
                 clinical populated by Phases 0-2.

    Returns:
        The same session object with validated_packages, phase3_blocked,
        and stg_coverage populated.

    Side effects:
        Appends to session.validated_packages, session.phase3_blocked,
        session.flags, session.errors, and session.stg_coverage.
    """
    logger.info(
        "Phase 3 — validating %d candidates", len(session.candidate_packages)
    )

    for candidate in session.candidate_packages:
        try:
            _validate_candidate(candidate, session)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Phase 3 — unhandled exception for %s: %s",
                candidate.procedure_code, exc, exc_info=True,
            )
            session.errors.append(
                f"Phase3 INTERNAL_ERROR for {candidate.procedure_code}: {exc}"
            )
            session.phase3_blocked.append({
                "procedure_code": candidate.procedure_code,
                "reason_code": "INTERNAL_ERROR",
                "message": str(exc),
            })

    validated_count = len(session.validated_packages)
    blocked_count = len(session.phase3_blocked)
    logger.info(
        "Phase 3 complete — validated=%d, blocked=%d, stg_coverage=%s",
        validated_count, blocked_count, session.stg_coverage,
    )

    if validated_count == 0:
        session.add_flag(
            code="NO_VALIDATED_PACKAGES",
            message=(
                "No candidate packages passed Phase 3 validation. "
                "USP referral is recommended."
            ),
            severity="warning",
        )

    return session


# ---------------------------------------------------------------------------
# Per-candidate orchestration (private)
# ---------------------------------------------------------------------------

def _validate_candidate(candidate: CandidatePackage, session: IRISSession) -> None:
    """Run all Phase 3 checks for a single candidate.

    Mutates session directly (appends to validated_packages, phase3_blocked,
    stg_coverage). Raises any unexpected exception — the caller catches it.

    Args:
        candidate: CandidatePackage thin record from Phase 2.
        session:   Current IRISSession.
    """
    code = candidate.procedure_code
    pkg_flags: list[str] = []

    # ------------------------------------------------------------------
    # STEP 1 — Load full procedure record from KB-2 shard
    # ------------------------------------------------------------------
    shard_filename = SPECIALTY_CODE_TO_SHARD.get(candidate.specialty_code)
    if not shard_filename:
        logger.warning(
            "Phase 3 — unknown specialty_code '%s' for %s",
            candidate.specialty_code, code,
        )
        session.phase3_blocked.append({
            "procedure_code": code,
            "reason_code": "SPECIALTY_CODE_UNKNOWN",
            "message": (
                f"specialty_code '{candidate.specialty_code}' has no "
                f"shard mapping in SPECIALTY_CODE_TO_SHARD"
            ),
        })
        return

    try:
        shard = load_specialty_shard(shard_filename)
    except FileNotFoundError:
        session.phase3_blocked.append({
            "procedure_code": code,
            "reason_code": "SHARD_NOT_FOUND",
            "message": f"Shard file {shard_filename}.json not found",
        })
        return

    procedure = get_procedure_from_shard(code, shard)
    if procedure is None:
        session.phase3_blocked.append({
            "procedure_code": code,
            "reason_code": "PROCEDURE_NOT_IN_SHARD",
            "message": (
                f"Procedure {code} not found in shard '{shard_filename}'"
            ),
        })
        return

    # ------------------------------------------------------------------
    # STEP 2 — Public reservation check (Critical Rule 1)
    # ------------------------------------------------------------------
    if (
        procedure.get("reserved_public_only", False)
        and session.hospital.type == "private"
    ):
        session.phase3_blocked.append({
            "procedure_code": code,
            "reason_code": "PUB_RESERVED_BLOCK",
            "message": (
                f"{code} is reserved for public hospitals only"
            ),
        })
        return

    # ------------------------------------------------------------------
    # STEP 3 — Classify billing type
    # ------------------------------------------------------------------
    billing_type = _classify_billing_type(procedure, code)

    # ------------------------------------------------------------------
    # STEP 4 — STG eligibility check (Critical Rules 3 & 4)
    # ------------------------------------------------------------------
    stg = load_stg(code)

    if stg is None:
        session.stg_coverage["stg_missing"] += 1
        pkg_flags.append("STG_MISSING")
        if REQUIRE_STG_FOR_VALIDATION:
            session.phase3_blocked.append({
                "procedure_code": code,
                "reason_code": "STG_REQUIRED",
                "message": (
                    f"STG file missing for {code} and REQUIRE_STG_FOR_VALIDATION=True"
                ),
            })
            return
        # Fail-open: no STG file → pass with warning
        stg_eligible = True
        stg_missing_criteria: list[str] = []
    else:
        session.stg_coverage["validated"] += 1
        llm_result = check_stg_eligibility(code, stg, session.clinical)
        stg_eligible = llm_result["eligible"]
        stg_missing_criteria = llm_result.get("missing_criteria", [])

        if not stg_eligible:
            session.phase3_blocked.append({
                "procedure_code": code,
                "reason_code": "STG_NOT_ELIGIBLE",
                "message": llm_result.get("reasoning", "LLM returned not eligible"),
            })
            return

        if llm_result.get("confidence") == "low":
            snippet = llm_result.get("reasoning", "")[:100]
            pkg_flags.append(f"STG_LOW_CONFIDENCE: {snippet}")

    # ------------------------------------------------------------------
    # STEP 5 — Stratification
    # ------------------------------------------------------------------
    stratification = _determine_stratification(procedure, session.clinical)
    if not stratification.determinable:
        pkg_flags.append(
            f"STRATIFICATION_UNDETERMINABLE: {stratification.note}"
        )

    # ------------------------------------------------------------------
    # STEP 6 — Implant check
    # ------------------------------------------------------------------
    implant = _check_implant(procedure, session.patient)
    if implant.required and not implant.age_appropriate:
        pkg_flags.append(
            "IMPLANT_AGE_BOUNDARY: verify paediatric vs adult device"
        )

    # ------------------------------------------------------------------
    # STEP 7 — Special conditions rule
    # ------------------------------------------------------------------
    popup = procedure.get("special_conditions_popup", False)
    rule = procedure.get("special_conditions_rule", False)

    if rule:
        policy_year_start: str = session.patient.wallet.policy_year_start
        policy_year: str = policy_year_start[:4]
        for claim in session.patient.past_claims:
            if (
                claim.procedure_code == code
                and claim.admission_date[:4] == policy_year
            ):
                pkg_flags.append(
                    f"SPECIAL_CONDITIONS_RULE_TRIGGERED: prior claim for "
                    f"{code} found in policy year {policy_year}"
                )
                break

    # ------------------------------------------------------------------
    # STEP 8 — Enhancement pre-calculation
    # ------------------------------------------------------------------
    enhancement_requests_needed = _plan_enhancement(procedure, session.hospital)

    # ------------------------------------------------------------------
    # STEP 9 — Build ValidatedPackage and append to session
    # ------------------------------------------------------------------
    validated = ValidatedPackage(
        procedure_code=code,
        package_code=candidate.package_code,
        specialty_code=candidate.specialty_code,
        package_name=candidate.package_name,
        procedure_name=candidate.procedure_name,
        billing_type=billing_type,
        billing_unit=procedure.get("billing_unit", ""),
        procedure_label=procedure.get("procedure_label", "regular"),
        auto_approved=procedure.get("auto_approved", "none"),
        enhancement_applicable=procedure.get("enhancement_applicable", False),
        enhancement_requests_needed=enhancement_requests_needed,
        reserved_public_only=procedure.get("reserved_public_only", False),
        base_rate_inr=_extract_base_rate(procedure, session.hospital.city_tier),
        stratification=stratification,
        implant=implant,
        special_conditions_popup=popup,
        special_conditions_rule=rule,
        stg_eligible=stg_eligible,
        stg_missing_criteria=stg_missing_criteria,
        is_addon_to=procedure.get("is_addon_to"),
        addon_type=procedure.get("addon_type"),
        match_score=candidate.match_score,
        flags=pkg_flags,
    )

    logger.info(
        "Phase 3 — validated %s (billing_type=%s, stg_eligible=%s, flags=%d)",
        code, billing_type, stg_eligible, len(pkg_flags),
    )
    session.validated_packages.append(validated)


# ---------------------------------------------------------------------------
# Private helper: billing type classification
# ---------------------------------------------------------------------------

def _classify_billing_type(procedure: dict, procedure_code: str = "") -> str:
    """Classify a KB-2 procedure's billing type for Phase 4 combination rules.

    Priority order (per SYSTEM_DESIGN.md "Billing Type Classification"):
      1. billing_unit == "per_day"              → "per_day"
      2. day_care == True                        → "day_care"
      3. medical_or_surgical field present:
           "surgical"  → "surgical"
           "medical"   → "fixed_medical"
      4. Fallback (medical_or_surgical absent):
           source_refs["billing_unit"] contains "(Surgical)" → "surgical"
           source_refs["billing_unit"] contains "(Medical)"  → "fixed_medical"
           default                                            → "fixed_medical"
           Logs WARNING when fallback is used.

    Args:
        procedure:      Full KB-2 procedure dict from the shard.
        procedure_code: Used only for logging; defaults to empty string.

    Returns:
        String literal: "per_day" | "day_care" | "surgical" | "fixed_medical"
    """
    if procedure.get("billing_unit") == "per_day":
        return "per_day"

    if procedure.get("day_care") is True:
        return "day_care"

    medical_or_surgical = procedure.get("medical_or_surgical")
    if medical_or_surgical is not None:
        if medical_or_surgical == "surgical":
            return "surgical"
        return "fixed_medical"

    # Fallback: parse source_refs hint
    logger.warning(
        "Phase 3 — 'medical_or_surgical' field missing for %s; "
        "falling back to source_refs hint",
        procedure_code,
    )
    source_hint: str = (
        procedure.get("source_refs", {}).get("billing_unit", "")
    )
    if "(Surgical)" in source_hint:
        return "surgical"
    if "(Medical)" in source_hint:
        return "fixed_medical"

    logger.warning(
        "Phase 3 — no billing type hint found in source_refs for %s; "
        "defaulting to 'fixed_medical'",
        procedure_code,
    )
    return "fixed_medical"


# ---------------------------------------------------------------------------
# Private helper: stratification
# ---------------------------------------------------------------------------

def _determine_stratification(
    procedure: dict, clinical: ClinicalInput
) -> StratificationResult:
    """Determine stratification outcome for this procedure.

    If stratification_required is False:
        Returns determinable=True, selected_stratum=None.

    If stratification_required is True:
        CASE A — bed-category (per_day) stratification:
          Detected when any stratum_id is in {"ward","hdu","icu_no_vent","icu_vent"}.
          Matches clinical.bed_category against stratum_id set.
          Returns determinable=False if bed_category is None.

        CASE B — non-per-day stratification (anaesthesia, laterality, etc.):
          MVP: keyword match on stratum criterion text against clinical free text.
          Returns determinable=False when no match is found.

    Args:
        procedure: Full KB-2 procedure dict.
        clinical:  ClinicalInput from the current session.

    Returns:
        StratificationResult with determinable, selected_stratum, and note fields.
    """
    if not procedure.get("stratification_required", False):
        return StratificationResult(
            determinable=True,
            selected_stratum=None,
            note=None,
        )

    criteria: list[dict] = procedure.get("stratification_criteria") or []

    # CASE A — bed-category stratification
    stratum_ids = {s.get("stratum_id", "") for s in criteria}
    is_bed_category = bool(stratum_ids & _BED_CATEGORY_STRATUM_IDS)

    if is_bed_category:
        if clinical.bed_category is None:
            return StratificationResult(
                determinable=False,
                selected_stratum=None,
                note=(
                    "bed_category not provided in clinical input — "
                    "required for per-day package stratification"
                ),
            )
        if clinical.bed_category in stratum_ids:
            return StratificationResult(
                determinable=True,
                selected_stratum=clinical.bed_category,
                note=None,
            )
        # bed_category provided but not in stratum list — undeterminable
        return StratificationResult(
            determinable=False,
            selected_stratum=None,
            note=(
                f"bed_category '{clinical.bed_category}' not found in "
                f"stratification_criteria strata: {sorted(stratum_ids)}"
            ),
        )

    # CASE B — non-per-day stratification (MVP: keyword matching)
    clinical_texts: list[str] = []
    if clinical.chief_complaints:
        clinical_texts.append(clinical.chief_complaints)
    if clinical.provisional_diagnosis:
        clinical_texts.append(clinical.provisional_diagnosis)
    if clinical.history_of_present_illness:
        clinical_texts.append(clinical.history_of_present_illness)
    if clinical.notes:
        clinical_texts.append(clinical.notes)
    if clinical.examination_findings:
        ef = clinical.examination_findings
        for attr in ("general", "cvs", "rs", "abdomen", "cns", "local"):
            val = getattr(ef, attr, None)
            if val:
                clinical_texts.append(val)

    combined_text = " ".join(clinical_texts).lower()

    for stratum in criteria:
        criterion: str = stratum.get("criterion", "").lower()
        if not criterion:
            continue
        # Split criterion into meaningful keywords (3+ chars), match all
        keywords = [w for w in criterion.split() if len(w) >= 3]
        if keywords and all(kw in combined_text for kw in keywords):
            return StratificationResult(
                determinable=True,
                selected_stratum=stratum.get("stratum_id"),
                note=None,
            )

    return StratificationResult(
        determinable=False,
        selected_stratum=None,
        note=(
            "Stratification type undeterminable from available clinical input "
            "— physician selection required"
        ),
    )


# ---------------------------------------------------------------------------
# Private helper: implant check
# ---------------------------------------------------------------------------

def _check_implant(procedure: dict, patient) -> ImplantResult:
    """Check implant requirement and basic appropriateness.

    KB-2 procedure["implant"] is one of:
      - null            → not required
      - dict            → single implant {name, cost_inr}
      - list of dicts   → multiple implant options; use first entry for MVP

    Age/gender appropriateness are advisory flags only — TMS auto-detects
    paediatric vs adult device. Do NOT block on these.

    Args:
        procedure: Full KB-2 procedure dict.
        patient:   PatientContext from the current session.

    Returns:
        ImplantResult. If required=False all other fields are None/True/None.
    """
    raw_implant = procedure.get("implant")

    if raw_implant is None:
        return ImplantResult(
            required=False,
            name=None,
            cost_inr=None,
            age_appropriate=True,
            gender_appropriate=True,
            quantity=None,
        )

    # Normalise to a single dict for MVP
    if isinstance(raw_implant, list):
        implant_entry: dict = raw_implant[0] if raw_implant else {}
    else:
        implant_entry = raw_implant

    name = implant_entry.get("name")
    cost_inr = implant_entry.get("cost_inr")

    # Advisory age-appropriateness: flag boundary cases for paediatric patients
    age_appropriate = True
    if patient is not None and patient.age <= PAEDIATRIC_AGE_MAX:
        # Paediatric device boundary — MEDCO must confirm device size in TMS
        age_appropriate = False

    return ImplantResult(
        required=True,
        name=name,
        cost_inr=cost_inr,
        age_appropriate=age_appropriate,
        gender_appropriate=True,   # MVP: gender check deferred
        quantity=1,                # MEDCO selects exact quantity in TMS
    )


# ---------------------------------------------------------------------------
# Private helper: enhancement planning
# ---------------------------------------------------------------------------

def _plan_enhancement(procedure: dict, hospital) -> int | None:
    """Pre-compute the number of enhancement requests likely needed.

    Formula (per SYSTEM_DESIGN.md Critical Rule 14):
        ceil((los_indicative - 1) / batch_size)

    Batch size is ENHANCEMENT_BATCH_PUBLIC if the hospital is public or in
    a NE/island state; ENHANCEMENT_BATCH_PRIVATE otherwise.

    Args:
        procedure: Full KB-2 procedure dict.
        hospital:  HospitalContext from the current session.

    Returns:
        int — estimated enhancement requests needed, or
        None if enhancement_applicable=False, los_indicative is "daycare",
             los_indicative <= 1, or los_indicative is missing.
    """
    if not procedure.get("enhancement_applicable", False):
        return None

    los = procedure.get("los_indicative")

    if los is None:
        return None
    if isinstance(los, str) and los.lower() == "daycare":
        return None

    los_int: int
    try:
        los_int = int(los)
    except (TypeError, ValueError):
        return None

    if los_int <= 1:
        return None

    is_ne = hospital.state in NE_STATES_AND_ISLANDS
    batch = (
        ENHANCEMENT_BATCH_PUBLIC
        if hospital.type == "public" or is_ne
        else ENHANCEMENT_BATCH_PRIVATE
    )

    return ceil((los_int - 1) / batch)


# ---------------------------------------------------------------------------
# Private helper: rate extraction
# ---------------------------------------------------------------------------

def _extract_base_rate(procedure: dict, city_tier: str) -> int | None:
    """Extract the appropriate base rate from a KB-2 procedure record.

    For per_day packages, rates_inr may be null (rate comes from bed category
    in pmjay.json). In that case, return None — Phase 5 handles the lookup.

    Priority:
      1. rates_inr[city_tier] if rates_inr is a non-null dict
      2. pricing["base_rate_inr"] if present and non-null
      3. None

    Args:
        procedure: Full KB-2 procedure dict.
        city_tier: "tier1" | "tier2" | "tier3" from HospitalContext.

    Returns:
        Integer INR rate or None.
    """
    rates_inr = procedure.get("rates_inr")
    if isinstance(rates_inr, dict):
        tier_rate = rates_inr.get(city_tier)
        if tier_rate is not None:
            return int(tier_rate)

    pricing = procedure.get("pricing") or {}
    base = pricing.get("base_rate_inr")
    if base is not None:
        return int(base)

    return None
