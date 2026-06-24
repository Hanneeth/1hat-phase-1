"""
models.py — IRIS Pipeline Data Models
======================================
All dataclasses used throughout the IRIS PM-JAY pre-authorisation pipeline.
IRISSession is intentionally excluded — it lives in session.py.

Import order mirrors pipeline data flow:
  Patient/Hospital context → Clinical input → Candidate/Validated packages
  → Final selection → Documents/Flags → Output

Python 3.11+ required (uses X | Y union syntax without `from __future__ import annotations`).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 1. WalletBalance
# ---------------------------------------------------------------------------

@dataclass
class WalletBalance:
    """
    PM-JAY beneficiary wallet state at time of admission.

    family_balance_inr       — Primary family entitlement balance (₹5 lakh/year standard).
    vay_vandana_balance_inr  — Additional Vay Vandana Yojana entitlement for senior citizens
                               (age ≥70). None if beneficiary is not enrolled or not eligible.
    policy_year_start        — ISO date string marking the start of the active policy year,
                               used to interpret balance figures correctly (e.g. "2025-04-01").
    """
    family_balance_inr: int
    vay_vandana_balance_inr: int | None
    policy_year_start: str  # ISO date e.g. "2025-04-01"


# ---------------------------------------------------------------------------
# 2. PastClaim
# ---------------------------------------------------------------------------

@dataclass
class PastClaim:
    """
    A single historical PM-JAY claim record for the beneficiary.

    Used by Phase 6 exclusion verification to detect package-level or
    time-window based re-admission rules, and by Phase 5 to compute
    cumulative spend within the policy year.
    """
    procedure_code: str
    admission_date: str       # ISO date string
    package_amount_inr: int
    status: str               # e.g. "approved" | "rejected" | "pending"


# ---------------------------------------------------------------------------
# 3. PatientContext
# ---------------------------------------------------------------------------

@dataclass
class PatientContext:
    """
    Beneficiary identity and entitlement data loaded by Phase 0 from the BIS stub.

    home_state / home_district — used by Phase 8 to detect portability cases
    (patient state ≠ hospital state).
    wallet                     — current entitlement balances; drives Phase 5 financial check.
    past_claims                — history used by Phase 5 (spend) and Phase 6 (exclusions).
    """
    patient_id: str
    family_id: str
    name: str
    age: int
    gender: str               # "M" | "F"
    home_state: str
    home_district: str
    wallet: WalletBalance
    past_claims: list[PastClaim] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. HospitalContext
# ---------------------------------------------------------------------------

@dataclass
class HospitalContext:
    """
    Empanelled hospital profile loaded by Phase 0 from the HEM stub.

    type                    — "private" | "public"; drives document relaxation rules
                              (CAM Annexure 7) and reserved_public_only blocking.
    city_tier               — "tier1" | "tier2" | "tier3"; used to select the correct
                              rate column from rates_inr in KB-2.
    is_aspirational_district— NHA pricing modifier for aspirational districts.
    accreditation           — "none" | "bronze" | "nabh_entry" | "nabh_full" | "nqas";
                              reserved for future enhancement pricing.
    scheme                  — must be "pmjay" for Phase 0 to pass; any other value sets
                              SCHEME_NOT_SUPPORTED block flag.
    empanelled_specialties  — list of 2-letter specialty codes the hospital can admit under.
    """
    hospital_id: str
    name: str
    type: str                 # "private" | "public"
    city_tier: str            # "tier1" | "tier2" | "tier3"
    state: str
    district: str
    is_aspirational_district: bool
    accreditation: str        # "none" | "bronze" | "nabh_entry" | "nabh_full" | "nqas"
    scheme: str               # "pmjay" only in MVP
    empanelled_specialties: list[str]


# ---------------------------------------------------------------------------
# 5. StructuredValue
# ---------------------------------------------------------------------------

@dataclass
class StructuredValue:
    """
    A single extracted parameter from an OCR-processed investigation document.

    Produced upstream by the OCR pipeline and included in the clinical input JSON.
    Phase 3 passes these to the LLM STG checker for precise threshold matching
    (e.g. Troponin I > 0.5 ng/mL, ST elevation present).

    leads — ECG-specific field; populated only for ECG investigation types
            (e.g. "II, III, aVF"). None for all other investigation types.
    flag  — "H" (above normal) | "L" (below normal) | "N" (normal) | None.
    """
    parameter: str
    value: float | str | None
    unit: str | None
    flag: str | None          # "H" | "L" | "N" | None
    leads: str | None         # ECG only, e.g. "II, III, aVF"


# ---------------------------------------------------------------------------
# 6. Investigation
# ---------------------------------------------------------------------------

@dataclass
class Investigation:
    """
    A single investigation or diagnostic report in the clinical input.

    type             — canonical enum value from SYSTEM_DESIGN.md:
                       ecg | echo | xray | ct | mri | usg | blood_reports |
                       urine_report | stool_report | cag_report | eeg | abg_chart |
                       csf | hpe | fnac | other
    result_summary   — free-text summary of the report (as entered by the ward clerk
                       or extracted by OCR). May be None if report is unavailable.
    structured_values— machine-readable extracted parameters. None if the document
                       was not available or not yet OCR-processed.
    document_available — whether the physical/digital document is in hand.
    report_date      — ISO date string of the report, or None.
    """
    type: str
    result_summary: str | None
    structured_values: list[StructuredValue] | None   # None if not OCR'd
    document_available: bool
    report_date: str | None   # ISO date or None


# ---------------------------------------------------------------------------
# 7. DocumentInHand
# ---------------------------------------------------------------------------

@dataclass
class DocumentInHand:
    """
    A non-clinical document that the hospital has collected from the patient
    at the time of admission (e.g. clinical_notes, patient_photo, mlc_fir).

    key       — canonical enum key from SYSTEM_DESIGN.md non_clinical_documents_in_hand
                (e.g. "clinical_notes", "patient_photo", "mlc_fir").
    available — True if the document is physically present and ready to upload.

    Phase 9 uses this list to compute document gaps against KB-2 mandatory_documents.preauth.
    """
    key: str
    label: str
    available: bool


# ---------------------------------------------------------------------------
# 8. ExaminationFindings
# ---------------------------------------------------------------------------

@dataclass
class ExaminationFindings:
    """
    Structured systemic examination findings recorded at admission.

    All fields are optional strings — None means the system was not examined or
    findings were not documented. The LLM in Phase 3 uses these as clinical context
    for STG eligibility assessment.

    Sections follow standard clinical examination order:
      general → CVS → RS → abdomen → CNS → local
    """
    general: str | None
    cvs: str | None
    rs: str | None
    abdomen: str | None
    cns: str | None
    local: str | None


# ---------------------------------------------------------------------------
# 9. PersonalHistory
# ---------------------------------------------------------------------------

@dataclass
class PersonalHistory:
    """
    Patient personal/social history relevant to clinical context.

    All fields are free-text strings or None. Passed to the LLM in Phase 3
    as supplementary context but not used for hard rule evaluation.
    """
    smoking: str | None
    alcohol: str | None
    diet: str | None


# ---------------------------------------------------------------------------
# 10. TreatingDoctor
# ---------------------------------------------------------------------------

@dataclass
class TreatingDoctor:
    """
    Identity and qualification of the admitting/treating doctor.

    registration_number — State medical council registration number
                          (e.g. "TN-MED-12345").
    specialty_code      — 2-letter HBP specialty code (e.g. "MC" for Cardiology).
                          Used by Phase 8 to check MTB / oncology escalation flags.
    qualification       — Free-text degree string (e.g. "MD DM Cardiology").
                          Phase 3 LLM compares this against STG min_doctor_qualification.
    """
    name: str
    registration_number: str
    qualification: str
    specialty_code: str       # 2-letter HBP code e.g. "MC"


# ---------------------------------------------------------------------------
# 11. ClinicalInput
# ---------------------------------------------------------------------------

@dataclass
class ClinicalInput:
    """
    Complete clinical presentation of the patient at admission.

    This is the third top-level key in the IRIS input JSON. It drives
    all clinical decision logic from Phase 1 (emergency routing) through
    Phase 9 (document gap analysis).

    Fields with defaults (current_medications, allergies, personal_history,
    family_history, non_clinical_documents_in_hand, treating_doctor, notes)
    appear last to satisfy Python dataclass ordering rules.

    Key fields:
      bed_category      — "ward"|"hdu"|"icu_no_vent"|"icu_vent"|None.
                          None triggers StratificationResult(determinable=False)
                          for per_day packages in Phase 3.
      vitals            — flexible dict; keys are canonical strings from input schema
                          (bp_systolic_mmhg, bp_diastolic_mmhg, pulse_bpm, spo2_pct,
                          temperature_f, rr_per_min, gcs, blood_glucose_mgdl).
                          Phase 3 forwards non-null values only to the LLM.
      is_medico_legal   — if True, Phase 9 adds mlc_fir + self_declaration as hard_block docs.
      comorbidities     — list of canonical comorbidity strings; Phase 7 resolves these.
    """
    admission_date: str | None               # ISO date e.g. "2026-06-12"
    bed_category: str | None                 # ward|hdu|icu_no_vent|icu_vent or None
    is_emergency: bool
    is_medico_legal: bool
    chief_complaints: str
    duration_days: int
    history_of_present_illness: str | None
    provisional_diagnosis: str
    planned_procedure: str | None
    weight_kg: float | None
    height_cm: float | None
    vitals: dict                             # flexible dict of vital sign values
    examination_findings: ExaminationFindings | None
    investigations: list[Investigation]
    comorbidities: list[str]
    past_medical_history: str | None
    past_surgical_history: str | None
    # --- fields with defaults below (must follow non-default fields) ---
    current_medications: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    personal_history: PersonalHistory | None = None
    family_history: str | None = None
    non_clinical_documents_in_hand: list[DocumentInHand] = field(default_factory=list)
    treating_doctor: TreatingDoctor | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# 12. CandidatePackage
# ---------------------------------------------------------------------------

@dataclass
class CandidatePackage:
    """
    Thin procedure record produced by Phase 2 fuzzy search against _index.json.

    Contains only the fields available in the KB-2 index (not the full shard).
    Phase 3 uses procedure_code to load the full shard record for detailed validation.

    match_score — rapidfuzz WRatio score (0–100). Phase 2 drops candidates below
                  MIN_FUZZY_SCORE (config.py). Retained here for Phase 3 logging
                  and Phase 10 output audit trail.
    base_rate_inr — may be None for per_day packages (rate comes from bed category
                    in pmjay.json, not from the index row).
    """
    procedure_code: str
    package_code: str
    specialty_code: str
    specialty: str
    package_name: str
    procedure_name: str
    billing_unit: str
    reserved_public_only: bool
    procedure_label: str      # regular | add_on | standalone | follow_up
    auto_approved: str        # none | full | day1_only
    day_care: bool
    base_rate_inr: int | None
    match_score: float


# ---------------------------------------------------------------------------
# 13. StratificationResult
# ---------------------------------------------------------------------------

@dataclass
class StratificationResult:
    """
    Outcome of Phase 3 stratification matching for a single procedure.

    For per_day packages, stratification is always by bed category
    (ward | hdu | icu_no_vent | icu_vent). For surgical packages with
    stratification_required=True, stratum_id may be an anaesthesia type
    or other criterion from KB-2 stratification_criteria.

    determinable    — False when the required input is missing (e.g. bed_category
                      is None in ClinicalInput). This is NOT a block; it produces a
                      warning flag and the best-effort rate is estimated.
    selected_stratum— the matched stratum_id string when determinable=True, else None.
    note            — human-readable explanation when determinable=False.
    """
    determinable: bool
    selected_stratum: str | None  # e.g. "ward", "hdu", "icu_vent"
    note: str | None              # populated when determinable=False


# ---------------------------------------------------------------------------
# 14. ImplantResult
# ---------------------------------------------------------------------------

@dataclass
class ImplantResult:
    """
    Implant applicability determination for a single procedure in Phase 3.

    required            — True if the KB-2 procedure record has a non-null implant field.
    name / cost_inr     — from KB-2 implant object; None if not required.
    age_appropriate     — placeholder for future paediatric implant size checks (Phase 8).
    gender_appropriate  — placeholder for gender-restricted implant checks.
    quantity            — number of implant units required; None if not specified in KB-2.
    """
    required: bool
    name: str | None
    cost_inr: int | None
    age_appropriate: bool
    gender_appropriate: bool
    quantity: int | None


# ---------------------------------------------------------------------------
# 15. ValidatedPackage
# ---------------------------------------------------------------------------

@dataclass
class ValidatedPackage:
    """
    Rich procedure record produced by Phase 3 after full validation.

    Built by loading the full KB-2 shard for a CandidatePackage and running:
      — reserved_public_only check
      — billing type classification (_classify_billing_type)
      — STG eligibility LLM check
      — stratification resolution
      — implant determination
      — enhancement pre-calculation

    Fields with defaults appear last (Python dataclass ordering requirement).

    Key fields:
      billing_type         — "surgical"|"fixed_medical"|"per_day"|"day_care"
                             classified by _classify_billing_type() in Phase 3.
      enhancement_requests_needed — pre-computed ceil((los-1)/batch) or None
                             if los_indicative is "daycare".
      stg_eligible         — True if LLM returned eligible=True, or if LLM failed
                             (fail-open per LLM Usage Policy in SYSTEM_DESIGN.md).
      stg_missing_criteria — LLM-returned list of clinical criteria not met.
      flags                — per-package warning strings accumulated in Phase 3
                             (e.g. "STG file missing", "billing_type fallback used").
    """
    procedure_code: str
    package_code: str
    specialty_code: str
    package_name: str
    procedure_name: str
    billing_type: str         # surgical | fixed_medical | per_day | day_care
    billing_unit: str
    procedure_label: str      # regular | add_on | standalone | follow_up
    auto_approved: str        # none | full | day1_only
    enhancement_applicable: bool
    enhancement_requests_needed: int | None
    reserved_public_only: bool
    base_rate_inr: int | None
    stratification: StratificationResult
    implant: ImplantResult
    special_conditions_popup: bool
    special_conditions_rule: bool
    stg_eligible: bool
    # --- fields with defaults below ---
    stg_missing_criteria: list[str] = field(default_factory=list)
    stg_reasoning: str | None = None
    is_addon_to: list[str] | None = None
    addon_type: str | None = None
    match_score: float = 0.0
    flags: list[str] = field(default_factory=list)  # per-package warning strings


# ---------------------------------------------------------------------------
# 16. FinalPackage
# ---------------------------------------------------------------------------

@dataclass
class FinalPackage:
    """
    A validated package after Phase 4 combination rule processing.

    Wraps a ValidatedPackage and adds billing role and deduction factor
    as determined by Phase 4 multi-package combination rules:

      Surgical + Surgical  → roles: primary(1.0), secondary(0.5), tertiary(0.25)
      Surgical + Fixed Med → both at 1.0 (roles: primary + secondary)
      Add-on + Primary     → add-on at 1.0 (role: addon)
      Standalone           → role: standalone, pre_auth_group=2

    pre_auth_group — 1 = main pre-auth submission, 2 = separate submission required
                     (standalone packages and split cases).
    deduction_factor — multiply base_rate_inr by this to get effective billing amount.
    """
    validated: ValidatedPackage
    role: str           # primary | secondary | tertiary | addon | standalone
    deduction_factor: float  # 1.0 | 0.5 | 0.25
    pre_auth_group: int      # 1 = main pre-auth, 2 = standalone/split


# ---------------------------------------------------------------------------
# 17. DocumentItem
# ---------------------------------------------------------------------------

@dataclass
class DocumentItem:
    """
    A single document entry in the pre-auth document checklist (Phase 9).

    Used in both preauth_docs_required and preauth_docs_missing lists in IRISOutput.

    key          — canonical document key from KB-2 mandatory_documents.preauth
                   or universal requirement list (e.g. "clinical_notes", "patient_photo").
    package_code — the package this document belongs to, or None for universal requirements
                   (clinical_notes and patient_photo apply regardless of package).
    available    — whether the document was found in ClinicalInput.non_clinical_documents_in_hand.
    criticality  — "hard_block"      → missing doc causes BLOCKED readiness status.
                   "ppd_query_risk"  → missing doc causes CONDITIONAL status (PPD may raise query).
    """
    key: str
    label: str
    package_code: str | None  # None = universal requirement
    available: bool
    criticality: str          # hard_block | ppd_query_risk


# ---------------------------------------------------------------------------
# 18. Flag
# ---------------------------------------------------------------------------

@dataclass
class Flag:
    """
    A pipeline business event flag appended to session.flags during any phase.

    Flags are surfaced to the MEDCO in IRISOutput.flags. Only flags are visible
    to end users — errors (session.errors) are for developer debugging only.

    code     — UPPER_SNAKE_CASE identifier (e.g. "PUB_RESERVED_BLOCK",
               "VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS", "USP_RECOMMENDED").
    severity — "block"   → Phase 10 sets readiness_status = BLOCKED; pipeline
                           should have already exited early via has_block_flag().
               "warning" → contributes to READY_WITH_WARNINGS if no harder condition.
               "info"    → informational only; does not affect readiness status.
    """
    code: str      # UPPER_SNAKE_CASE
    message: str
    severity: str  # info | warning | block


# ---------------------------------------------------------------------------
# 19. EnhancementPlan
# ---------------------------------------------------------------------------

@dataclass
class EnhancementPlan:
    """
    Pre-computed enhancement request estimate for a single per_day procedure.

    Enhancement requests extend a pre-auth beyond the initially authorised LoS.
    PM-JAY requires separate enhancement requests filed in batches (ENHANCEMENT_BATCH_*
    from config.py — 2 for private hospitals, 5 for public/NE hospitals).

    Formula (Phase 3, also available for Phase 10 assembly):
      estimated_requests = ceil((los_indicative - 1) / batch_size)

    los_indicative_days — 0 when original los_indicative was the string "daycare",
                          in which case estimated_requests is not meaningful (set to 0).
    caveat              — always the standard NHA caveat string (non-negotiable per spec).
    """
    procedure_code: str
    estimated_requests: int
    batch_size_used: int
    los_indicative_days: int  # 0 if los was "daycare"
    caveat: str               # standard: "Estimated based on indicative LoS — actual stay may vary. File additional enhancement requests as needed."


# ---------------------------------------------------------------------------
# 20. ChecklistItemResult
# ---------------------------------------------------------------------------

@dataclass
class ChecklistItemResult:
    question: str
    expected: bool
    actual: bool | None
    risk_level: str
    reasoning: str


# ---------------------------------------------------------------------------
# 21. CommonQueryRisk
# ---------------------------------------------------------------------------

@dataclass
class CommonQueryRisk:
    query_text: str
    risk_level: str
    reasoning: str


# ---------------------------------------------------------------------------
# 22. PackageQueryPrediction
# ---------------------------------------------------------------------------

@dataclass
class PackageQueryPrediction:
    procedure_code: str
    package_name: str
    readiness_verdict: str
    verdict_summary: str
    checklist_results: list[ChecklistItemResult]
    common_query_risks: list[CommonQueryRisk]
    advisory_claim_docs: list[dict]
    llm_evaluation_status: str = "unknown"


# ---------------------------------------------------------------------------
# 22a. ClaimDocumentItem
# ---------------------------------------------------------------------------

@dataclass
class ClaimDocumentItem:
    key: str
    label: str
    package_code: str | None
    available: bool
    criticality: str
    notes: str | None


# ---------------------------------------------------------------------------
# 22b. DeviationItem
# ---------------------------------------------------------------------------

@dataclass
class DeviationItem:
    deviation_type: str
    description: str
    from_value: str
    to_value: str
    severity: str
    justification_draft: str | None
    justification_required: bool


# ---------------------------------------------------------------------------
# 22c. CPDChecklistResult
# ---------------------------------------------------------------------------

@dataclass
class CPDChecklistResult:
    question: str
    expected: bool
    actual: bool | None
    risk_level: str
    reasoning: str


# ---------------------------------------------------------------------------
# 22d. SpecialPaymentResult
# ---------------------------------------------------------------------------

@dataclass
class SpecialPaymentResult:
    trigger: str
    base_package_rate_inr: int
    payable_amount_inr: int
    payable_percentage: int
    computation_note: str


# ---------------------------------------------------------------------------
# 22e. IRISClaimOutput
# ---------------------------------------------------------------------------

@dataclass
class IRISClaimOutput:
    claim_status: str
    procedure_code: str
    package_name: str
    preauth_reference: str
    claim_docs_required: list[ClaimDocumentItem] = field(default_factory=list)
    claim_docs_missing: list[ClaimDocumentItem] = field(default_factory=list)
    image_docs_reminder: list[str] = field(default_factory=list)
    cpd_checklist_results: list[CPDChecklistResult] = field(default_factory=list)
    cpd_verdict: str = "unknown"
    cpd_verdict_summary: str = ""
    llm_evaluation_status: str = "unknown"
    deviations_detected: list[DeviationItem] = field(default_factory=list)
    deviation_justifications_drafted: int = 0
    los_approved_indicative: int = 0
    los_actual: int = 0
    los_deviation: bool = False
    los_deviation_note: str | None = None
    discharge_summary_complete: bool = False
    discharge_summary_missing_fields: list[str] = field(default_factory=list)
    special_payment: SpecialPaymentResult | None = None
    audit_flags_triggered: list[str] = field(default_factory=list)
    sha_notification_warning: str | None = None
    specialty_specific_notes: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 23. IRISOutput
# ---------------------------------------------------------------------------

@dataclass
class IRISOutput:
    """
    Final output of the IRIS pipeline, assembled by Phase 10.

    readiness_status determination (first match wins, per SYSTEM_DESIGN.md):
      1. Any Flag(severity="block")                          → BLOCKED
      2. Any DocumentItem(criticality="hard_block", available=False) → BLOCKED
      3. session.final_package_set is empty                 → BLOCKED
      4. Any DocumentItem(criticality="ppd_query_risk", available=False) → CONDITIONAL
      5. Any Flag(severity="warning")                        → READY_WITH_WARNINGS
      6. Otherwise                                           → READY

    blocked_candidates   — list of dicts: {procedure_code, reason_code, message}.
                           Not DocumentItems — these are packages that were fully
                           rejected by Phase 3, not document gaps.
    stg_coverage         — {"validated": n, "stg_missing": n} from session.stg_coverage.
                           Reflects Phase 3 LLM call outcomes across all candidates.
    errors               — technical failure strings from session.errors. Surfaced in
                           output for developer visibility; not shown to the MEDCO.
    """
    readiness_status: str     # READY | READY_WITH_WARNINGS | CONDITIONAL | BLOCKED
    # --- all remaining fields have defaults (readiness_status is the only required field) ---
    selected_packages: list[FinalPackage] = field(default_factory=list)
    blocked_candidates: list[dict] = field(default_factory=list)  # {procedure_code, reason_code, message}
    preauth_docs_required: list[DocumentItem] = field(default_factory=list)
    preauth_docs_missing: list[DocumentItem] = field(default_factory=list)
    query_predictions: list[PackageQueryPrediction] = field(default_factory=list)
    enhancement_plan: list[EnhancementPlan] = field(default_factory=list)
    copayment_required: bool = False
    copayment_gap_inr: int | None = None
    comorbidity_notes: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    stg_coverage: dict = field(default_factory=lambda: {"validated": 0, "stg_missing": 0})
    errors: list[str] = field(default_factory=list)
