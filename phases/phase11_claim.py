from __future__ import annotations
import json
import logging
from datetime import date, timedelta
from kb.loader import load_specialty_shard, get_procedure_from_shard
from phases.phase3_validator import SPECIALTY_CODE_TO_SHARD
from models import (
    ClaimDocumentItem, DeviationItem, CPDChecklistResult,
    SpecialPaymentResult, IRISClaimOutput, Flag
)
from llm.cpd_evaluator import evaluate_claim_with_cpd, check_clinical_consistency

logger = logging.getLogger(__name__)

EQUIVALENT_KEYS = {
    "operative_notes": ["procedure_operative_notes", "detailed_operative_notes"],
    "detailed_discharge_summary": "discharge_summary",
    "pre_anesthesia_check_up_report": "pre_anesthesia_checkup_report",
    "pre_anesthesia_checkup_report": "pre_anesthesia_check_up_report",
    "biopsy_hpe": "histopathology_report",
    "ot_notes": "procedure_operative_notes",
    "clinical_photo_intraop": "intraoperative_photo_gross_specimen",
    "clinical_photo_posttreat": "post_treatment_clinical_photograph",
}



def _validate_cross_consistency(
    discharge_dict: dict,
    preauth_output_dict: dict,
    preauth_input_dict: dict,
) -> list[Flag]:
    """Step 0 — Cross-consistency validation.
    
    Checks identity, package integrity, and clinical consistency between
    the discharge JSON and the pre-auth JSON/output.
    
    Returns list of Flag objects. Block flags indicate identity or package
    mismatches. Warning flags indicate clinical inconsistencies.
    Fail-open: exceptions in any sub-check are logged and skipped.
    """
    flags: list[Flag] = []

    # ── IDENTITY CHECKS (deterministic, hard block on mismatch) ──────────

    # PMJAY ID
    try:
        preauth_patient_id = preauth_input_dict.get("patient", {}).get("patient_id", "")
        discharge_pmjay_id = discharge_dict.get("patient", {}).get("pmjay_id", "")
        if preauth_patient_id and discharge_pmjay_id:
            if preauth_patient_id.strip().upper() != discharge_pmjay_id.strip().upper():
                flags.append(Flag(
                    code="IDENTITY_MISMATCH_PMJAY_ID",
                    message=(
                        f"PMJAY ID mismatch — pre-auth: '{preauth_patient_id}', "
                        f"discharge: '{discharge_pmjay_id}'. "
                        f"Documents may belong to different patients."
                    ),
                    severity="block",
                ))
                logger.error(
                    "Step 0 — PMJAY ID mismatch: preauth=%s, discharge=%s",
                    preauth_patient_id, discharge_pmjay_id
                )
    except Exception as exc:
        logger.warning("Step 0 — PMJAY ID check failed: %s", exc)

    # Hospital code
    try:
        preauth_hospital_id = preauth_input_dict.get("hospital", {}).get("hospital_id", "")
        discharge_hospital_code = discharge_dict.get("hospital", {}).get("hospital_code", "")
        if preauth_hospital_id and discharge_hospital_code:
            if preauth_hospital_id.strip().upper() != discharge_hospital_code.strip().upper():
                flags.append(Flag(
                    code="IDENTITY_MISMATCH_HOSPITAL",
                    message=(
                        f"Hospital mismatch — pre-auth: '{preauth_hospital_id}', "
                        f"discharge: '{discharge_hospital_code}'. "
                        f"Claim filed at different hospital from pre-auth."
                    ),
                    severity="block",
                ))
                logger.error(
                    "Step 0 — Hospital mismatch: preauth=%s, discharge=%s",
                    preauth_hospital_id, discharge_hospital_code
                )
    except Exception as exc:
        logger.warning("Step 0 — Hospital check failed: %s", exc)

    # Treating doctor registration number
    try:
        preauth_reg = (
            preauth_input_dict
            .get("clinical", {})
            .get("treating_doctor", {})
            .get("registration_number", "")
        )
        discharge_reg = (
            discharge_dict
            .get("treating_consultant", {})
            .get("registration_number", "")
        )
        if preauth_reg and discharge_reg:
            if preauth_reg.strip().upper() != discharge_reg.strip().upper():
                flags.append(Flag(
                    code="IDENTITY_MISMATCH_DOCTOR_REG",
                    message=(
                        f"Treating doctor registration mismatch — "
                        f"pre-auth: '{preauth_reg}', "
                        f"discharge: '{discharge_reg}'. "
                        f"Verify treating doctor change was authorised."
                    ),
                    severity="block",
                ))
                logger.warning(
                    "Step 0 — Doctor reg mismatch: preauth=%s, discharge=%s",
                    preauth_reg, discharge_reg
                )
    except Exception as exc:
        logger.warning("Step 0 — Doctor registration check failed: %s", exc)

    # Patient name (warning only — spelling variations expected)
    try:
        preauth_name = ""
        # Patient name not directly in pre-auth JSON — it comes from BIS stub
        # Use discharge name as reference and check against preauth_output_dict
        # if it carries patient context, otherwise skip
        discharge_name = discharge_dict.get("patient", {}).get("name", "")
        # Check if preauth_input_dict has any name field
        # (it doesn't in current schema — patient_id only)
        # So we skip name check if preauth has no name
        # This check is reserved for when BIS stub data is available
    except Exception as exc:
        logger.warning("Step 0 — Patient name check skipped: %s", exc)

    # ── PACKAGE INTEGRITY CHECK (deterministic, hard block) ────────────

    try:
        discharge_package = discharge_dict.get("admission", {}).get("package_booked", "")
        selected = (preauth_output_dict.get("selected_packages") or [])
        if selected and discharge_package:
            preauth_package_code = (
                selected[0]
                .get("validated", {})
                .get("procedure_code", "")
            )
            # Compare procedure_code (e.g. "BM001B") against package_booked
            if (preauth_package_code
                    and discharge_package.strip().upper()
                    != preauth_package_code.strip().upper()):
                flags.append(Flag(
                    code="PROCEDURE_MISMATCH",
                    message=(
                        f"Procedure code mismatch — IRIS selected '{preauth_package_code}' "
                        f"at pre-auth but discharge records '{discharge_package}'. "
                        f"Verify correct procedure_code is being claimed."
                    ),
                    severity="block",
                ))
                logger.error(
                    "Step 0 — Procedure code mismatch: preauth=%s, discharge=%s",
                    preauth_package_code, discharge_package
                )
    except Exception as exc:
        logger.warning("Step 0 — Package integrity check failed: %s", exc)

    # ── CLINICAL CONSISTENCY CHECK (LLM, warning only) ─────────────────
    # Only run if no block flags so far — no point checking clinical
    # consistency if patient identity is already wrong.

    block_count = sum(1 for f in flags if f.severity == "block")
    if block_count == 0 and preauth_input_dict:
        try:
            issues = check_clinical_consistency(
                preauth_input_dict, discharge_dict
            )
            for issue in issues:
                flags.append(Flag(
                    code="CLINICAL_CONSISTENCY_WARNING",
                    message=(
                        f"Clinical consistency — {issue.get('field', 'unknown')}: "
                        f"{issue.get('description', '')}"
                    ),
                    severity="warning",
                ))
                logger.warning(
                    "Step 0 — Clinical consistency issue in field '%s': %s",
                    issue.get("field"),
                    issue.get("description"),
                )
        except Exception as exc:
            logger.warning("Step 0 — Clinical consistency check failed: %s", exc)

    logger.info(
        "Step 0 — Cross-consistency validation complete | "
        "flags=%d (block=%d, warning=%d)",
        len(flags),
        sum(1 for f in flags if f.severity == "block"),
        sum(1 for f in flags if f.severity == "warning"),
    )
    return flags


def run_phase11(
    discharge_dict: dict,
    preauth_output_dict: dict,
    preauth_input_dict: dict | None = None,
) -> IRISClaimOutput:
    """Stage 3 — Claims Verification public entry point.
    
    Orchestrates Steps 1-12 in order. On any unhandled exception in a step:
    log ERROR, append to errors list, and continue to next step.
    """
    if preauth_input_dict is None:
        preauth_input_dict = {}
        logger.warning(
            "run_phase11: preauth_input_dict not provided — "
            "LLM will have no pre-auth baseline context."
        )
    errors = []

    # Step 0 — Cross-consistency validation
    consistency_flags: list[Flag] = []
    try:
        consistency_flags = _validate_cross_consistency(
            discharge_dict,
            preauth_output_dict,
            preauth_input_dict,
        )
    except Exception as exc:
        logger.error("Step 0 failed entirely: %s", exc)
        errors.append(f"Step 0 cross-consistency check failed: {exc}")
    
    # Defaults in case steps fail
    context = {
        "procedure_code": "unknown",
        "package_code": None,
        "package_name": "unknown",
        "specialty_code": "unknown",
        "base_rate_inr": 0,
        "billing_type": "unknown",
        "los_indicative": 0,
        "stg_dict": None,
        "shard_dict": None,
        "preauth_reference": "unknown",
        "date_of_discharge": None,
    }
    ds_complete = False
    ds_missing = []
    claim_docs_required = []
    claim_docs_missing = []
    image_docs_reminder = []
    los_deviation = False
    los_deviation_note = None
    deviations = []
    checklist_results = []
    llm_status = "skipped"
    special_payment = None
    audit_flags = []
    sha_warning = None
    specialty_notes = []
    claim_status = "CLAIM_BLOCKED"

    # Step 1: load claim context
    try:
        context = _load_claim_context(discharge_dict, preauth_output_dict)
    except Exception as exc:
        logger.error("Phase 11 Step 1 error: %s", exc)
        errors.append(f"Step 1 load claim context failed: {exc}")

    # Step-based verification check discharge summary completeness
    try:
        ds_complete, ds_missing = _check_discharge_summary_completeness(discharge_dict)
    except Exception as exc:
        logger.error("Phase 11 Step 2 error: %s", exc)
        errors.append(f"Step 2 check discharge summary completeness failed: {exc}")

    # Step 3: build claim docs list
    try:
        claim_docs_required, claim_docs_missing, image_docs_reminder = _build_claim_docs_list(
            context, discharge_dict, preauth_output_dict
        )
    except Exception as exc:
        logger.error("Phase 11 Step 3 error: %s", exc)
        errors.append(f"Step 3 build claim docs list failed: {exc}")

    # Step 4: check length of stay
    try:
        los_deviation, los_deviation_note = _check_los(context, discharge_dict, preauth_output_dict)
    except Exception as exc:
        logger.error("Phase 11 Step 4 error: %s", exc)
        errors.append(f"Step 4 check LoS failed: {exc}")

    # Step 5: detect deviations
    try:
        deviations = _detect_deviations(context, discharge_dict, preauth_output_dict, preauth_input_dict)
    except Exception as exc:
        logger.error("Phase 11 Step 5 error: %s", exc)
        errors.append(f"Step 5 detect deviations failed: {exc}")

    # Step 6: LLM evaluation
    try:
        if context["procedure_code"] != "unknown":
            checklist_results, deviations, llm_status = evaluate_claim_with_cpd(
                procedure_code=context["procedure_code"],
                package_name=context["package_name"],
                discharge_dict=discharge_dict,
                deviations=deviations,
                preauth_output_dict=preauth_output_dict,
                preauth_input_dict=preauth_input_dict,
            )
        else:
            llm_status = "skipped"
    except Exception as exc:
        logger.error("Phase 11 Step 6 (LLM eval) error: %s", exc)
        errors.append(f"Step 6 LLM evaluation failed: {exc}")
        llm_status = "failed"

    # Step 7: compute special payment
    try:
        special_payment = _compute_special_payment(context, discharge_dict)
    except Exception as exc:
        logger.error("Phase 11 Step 7 error: %s", exc)
        errors.append(f"Step 7 compute special payment failed: {exc}")

    # Step 8: check audit flags
    try:
        audit_flags = _check_audit_flags(context, discharge_dict, claim_docs_missing)
    except Exception as exc:
        logger.error("Phase 11 Step 8 error: %s", exc)
        errors.append(f"Step 8 check audit flags failed: {exc}")

    # Step 9: check SHA notification
    try:
        sha_warning = _check_sha_notification(discharge_dict)
    except Exception as exc:
        logger.error("Phase 11 Step 9 error: %s", exc)
        errors.append(f"Step 9 check SHA notification failed: {exc}")

    # Step 10: check specialty-specific guidelines
    try:
        specialty_notes = _check_specialty_specific(context, discharge_dict)
    except Exception as exc:
        logger.error("Phase 11 Step 10 error: %s", exc)
        errors.append(f"Step 10 check specialty-specific notes failed: {exc}")

    # Step 11: compute claim status
    try:
        claim_status = _compute_claim_status(claim_docs_missing, deviations, checklist_results)
    except Exception as exc:
        logger.error("Phase 11 Step 11 error: %s", exc)
        errors.append(f"Step 11 compute claim status failed: {exc}")

    # Step 12: Final Assembly
    cpd_verdict = "clean"
    if any(r.risk_level == "high" for r in checklist_results):
        cpd_verdict = "likely_deducted"
    elif any(r.risk_level == "medium" for r in checklist_results):
        cpd_verdict = "gaps_present"

    high_items = [r.question for r in checklist_results if r.risk_level == "high"]
    medium_count = sum(1 for r in checklist_results if r.risk_level == "medium")
    if high_items:
        cpd_verdict_summary = f"{len(high_items)} high-risk CPD item(s): " + ", ".join(high_items[:3])
    elif medium_count > 0:
        cpd_verdict_summary = f"{medium_count} item(s) may prompt CPD query or deduction."
    else:
        cpd_verdict_summary = "All CPD checklist items satisfied."

    deviation_justifications_drafted = sum(
        1 for d in deviations if d.justification_draft is not None
    )

    return IRISClaimOutput(
        claim_status=claim_status,
        procedure_code=context["procedure_code"],
        package_name=context["package_name"],
        preauth_reference=context["preauth_reference"],
        claim_docs_required=claim_docs_required,
        claim_docs_missing=claim_docs_missing,
        image_docs_reminder=image_docs_reminder,
        cpd_checklist_results=checklist_results,
        cpd_verdict=cpd_verdict,
        cpd_verdict_summary=cpd_verdict_summary,
        llm_evaluation_status=llm_status,
        deviations_detected=deviations,
        deviation_justifications_drafted=deviation_justifications_drafted,
        los_approved_indicative=context["los_indicative"],
        los_actual=discharge_dict.get("admission", {}).get("actual_los_days", 0),
        los_deviation=los_deviation,
        los_deviation_note=los_deviation_note,
        discharge_summary_complete=ds_complete,
        discharge_summary_missing_fields=ds_missing,
        special_payment=special_payment,
        audit_flags_triggered=audit_flags,
        sha_notification_warning=sha_warning,
        specialty_specific_notes=specialty_notes,
        flags=consistency_flags,
        errors=errors,
    )


def _load_claim_context(
    discharge_dict: dict,
    preauth_output_dict: dict,
) -> dict:
    """Extract context details from pre-authorization output and discharge JSON."""
    selected_packages = preauth_output_dict.get("selected_packages", [])
    if not selected_packages:
        raise ValueError("No selected packages in pre-auth output")
    
    first_package = selected_packages[0]
    
    def get_val(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    
    validated = get_val(first_package, "validated")
    if not validated:
        raise ValueError("No validated package details in pre-auth output")
    
    procedure_code = get_val(validated, "procedure_code")
    package_code = get_val(validated, "package_code")
    package_name = get_val(validated, "package_name")
    specialty_code = get_val(validated, "specialty_code")
    base_rate_inr = get_val(validated, "base_rate_inr")
    billing_type = get_val(validated, "billing_type")
    
    # Load STG
    stg_dict = None
    stg_path = f"data/stg/{procedure_code}.json"
    try:
        with open(stg_path, "r", encoding="utf-8") as f:
            stg_dict = json.load(f)
    except FileNotFoundError:
        logger.warning("STG file not found for claim: %s", stg_path)
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in STG file %s: %s", stg_path, exc)
    except Exception as exc:
        logger.error("Unexpected error loading STG file %s: %s", stg_path, exc)
        
    # Load shard
    shard_dict = None
    shard_filename = SPECIALTY_CODE_TO_SHARD.get(specialty_code)
    if shard_filename:
        try:
            shard_dict = load_specialty_shard(shard_filename)
        except Exception as exc:
            logger.warning("Could not load specialty shard for %s: %s", specialty_code, exc)

    los_indicative = 0
    shard_los = None
    if shard_dict is not None:
        proc_entry = get_procedure_from_shard(procedure_code, shard_dict)
        if proc_entry is not None:
            if isinstance(proc_entry, dict):
                shard_los = proc_entry.get("los_indicative")
            else:
                shard_los = getattr(proc_entry, "los_indicative", None)

    if shard_los is not None and isinstance(shard_los, int) and not isinstance(shard_los, bool) and shard_los > 0:
        los_indicative = shard_los
    else:
        if stg_dict and "alos" in stg_dict:
            alos_str = stg_dict["alos"]
            try:
                import re
                match = re.search(r'\d+', str(alos_str))
                if match:
                    los_indicative = int(match.group())
            except Exception:
                los_indicative = 0

    preauth_reference = discharge_dict.get("preauth_reference", "unknown")
    
    admission = discharge_dict.get("admission", {})
    date_of_discharge = admission.get("date_of_discharge")
    
    return {
        "procedure_code": procedure_code,
        "package_code": package_code,
        "package_name": package_name,
        "specialty_code": specialty_code,
        "base_rate_inr": base_rate_inr,
        "billing_type": billing_type,
        "los_indicative": los_indicative,
        "stg_dict": stg_dict,
        "shard_dict": shard_dict,
        "preauth_reference": preauth_reference,
        "date_of_discharge": date_of_discharge,
    }


def _check_discharge_summary_completeness(
    discharge_dict: dict,
) -> tuple[bool, list[str]]:
    """Verify discharge summary contents against CAM Annexure 6 criteria."""
    missing_fields_list = []
    
    def check_field(label: str, path: list):
        curr = discharge_dict
        for p in path:
            if not isinstance(curr, dict) or p not in curr:
                missing_fields_list.append(label)
                return
            curr = curr[p]
        if curr is None:
            missing_fields_list.append(label)
        elif isinstance(curr, str) and not curr.strip():
            missing_fields_list.append(label)
            
    def check_signature(label: str, path: list):
        curr = discharge_dict
        for p in path:
            if not isinstance(curr, dict) or p not in curr:
                missing_fields_list.append(label)
                return
            curr = curr[p]
        if curr is not True:
            missing_fields_list.append(label)

    check_field("Hospital name", ["hospital", "name"])
    check_field("Patient name", ["patient", "name"])
    check_field("PMJAY ID", ["patient", "pmjay_id"])
    check_field("Treating consultant name", ["treating_consultant", "name"])
    check_field("Treating consultant qualification", ["treating_consultant", "qualification"])
    check_field("Treating consultant registration number", ["treating_consultant", "registration_number"])
    check_field("Date of admission", ["admission", "date_of_admission"])
    check_field("Date of discharge", ["admission", "date_of_discharge"])
    check_field("Presenting complaints", ["clinical", "presenting_complaints"])
    check_field("Primary diagnosis at admission", ["clinical", "primary_diagnosis_at_admission"])
    check_field("Final diagnosis at discharge", ["clinical", "final_diagnosis_at_discharge"])
    check_field("Final procedure performed", ["clinical", "final_procedure_performed"])
    check_field("Treatment given", ["clinical", "treatment_given"])
    check_field("Discharge condition", ["admission", "discharge_condition"])
    check_field("Advice on discharge", ["clinical", "advice_on_discharge"])
    
    check_signature("Treating consultant signed", ["signatures", "treating_consultant_signed"])
    check_signature("PMAM signed", ["signatures", "pmam_signed"])
    check_signature("Patient or attendant signed", ["signatures", "patient_or_attendant_signed"])

    is_complete = len(missing_fields_list) == 0
    return (is_complete, missing_fields_list)


def _build_claim_docs_list(
    context: dict,
    discharge_dict: dict,
    preauth_output_dict: dict,
) -> tuple[list[ClaimDocumentItem], list[ClaimDocumentItem], list[str]]:
    """Determine mandatory and conditional claim documents required for submission."""
    claim_docs_required = []
    required_keys = set()
    
    def get_val(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    # SUB-STEP A — Universal claim docs
    claim_docs_required.append(
        ClaimDocumentItem(
            key="discharge_summary",
            label="Discharge Summary (CAM Annexure 6 format)",
            package_code=None,
            available=False,
            criticality="hard_block",
            notes=None
        )
    )
    required_keys.add("discharge_summary")

    # SUB-STEP B — STG mandatory_documents.claim
    stg_dict = context["stg_dict"]
    if stg_dict is not None:
        claim_docs = stg_dict.get("mandatory_documents", {}).get("claim", [])
        for doc in claim_docs:
            key = doc.get("key")
            label = doc.get("label") or key
            if key == "histopathology_report":
                criticality = "cpd_query_risk"
                notes = "Can be submitted within 7 days of discharge"
            else:
                criticality = "hard_block"
                notes = None
            
            if key not in required_keys:
                claim_docs_required.append(
                    ClaimDocumentItem(
                        key=key,
                        label=label,
                        package_code=context["package_code"],
                        available=False,
                        criticality=criticality,
                        notes=notes
                    )
                )
                required_keys.add(key)

    # SUB-STEP C — Shard fallback
    if stg_dict is None:
        if context["shard_dict"] is not None:
            try:
                shard_procedure = get_procedure_from_shard(
                    context["procedure_code"], context["shard_dict"]
                )
                if shard_procedure is None:
                    logger.warning(
                        "Procedure %s: Shard fallback also returned no procedure entry",
                        context["procedure_code"]
                    )
                else:
                    shard_claim_docs = (
                        shard_procedure
                        .get("mandatory_documents", {})
                        .get("claim", []) or []
                    )
                    docs_added = 0
                    for doc in shard_claim_docs:
                        key = doc.get("key")
                        label = doc.get("label") or key
                        if not key:
                            continue
                        if key == "discharge_summary":
                            continue
                        if key in required_keys:
                            continue
                        
                        if key in ("biopsy_hpe", "histopathology_report"):
                            criticality = "cpd_query_risk"
                            notes = "Can be submitted within 7 days of discharge"
                        else:
                            criticality = "hard_block"
                            notes = None
                        
                        claim_docs_required.append(
                            ClaimDocumentItem(
                                key=key,
                                label=label,
                                package_code=context["package_code"],
                                available=False,
                                criticality=criticality,
                                notes=notes
                            )
                        )
                        required_keys.add(key)
                        docs_added += 1
                    
                    logger.info(
                        "Fallback claim docs compiled for procedure %s: added %d docs from shard",
                        context["procedure_code"],
                        docs_added
                    )
            except Exception as exc:
                logger.warning(
                    "Phase 11 Step 3C — Shard fallback procedure lookup failed for %s: %s",
                    context["procedure_code"],
                    exc
                )
        else:
            logger.warning(
                "Procedure %s: neither STG nor shard is available for claim doc compilation. "
                "Only universal docs will be required.",
                context["procedure_code"]
            )

    # SUB-STEP D — Implant invoice
    implant_req = False
    selected_packages = preauth_output_dict.get("selected_packages", [])
    if selected_packages:
        first_package = selected_packages[0]
        validated = get_val(first_package, "validated")
        if validated:
            implant = get_val(validated, "implant")
            if implant:
                implant_req = (get_val(implant, "required") == True)

    if implant_req:
        if "implant_invoice_sticker" not in required_keys:
            claim_docs_required.append(
                ClaimDocumentItem(
                    key="implant_invoice_sticker",
                    label="Implant invoice / sticker with model and serial number",
                    package_code=context["package_code"],
                    available=False,
                    criticality="hard_block",
                    notes=None
                )
            )
            required_keys.add("implant_invoice_sticker")

    # SUB-STEP E — Death within 24h mortality audit
    admission = discharge_dict.get("admission", {})
    discharge_status = admission.get("discharge_status")
    actual_los_days = admission.get("actual_los_days", 0)
    if discharge_status == "death" and actual_los_days <= 1:
        if "mortality_audit_report" not in required_keys:
            claim_docs_required.append(
                ClaimDocumentItem(
                    key="mortality_audit_report",
                    label="Detailed mortality audit report (mandatory for death within 24h — HBP Guidelines Section 6.2.c)",
                    package_code=None,
                    available=False,
                    criticality="hard_block",
                    notes=None
                )
            )
            required_keys.add("mortality_audit_report")

    # SUB-STEP F — Mark availability
    available_keys = set()
    docs_submitted = discharge_dict.get("documents_submitted", [])
    if isinstance(docs_submitted, dict):
        for k, v in docs_submitted.items():
            if v is True or v == "true":
                available_keys.add(k)
    elif isinstance(docs_submitted, list):
        for item in docs_submitted:
            if isinstance(item, dict):
                if item.get("available") is True or item.get("available") == "true":
                    key = item.get("key")
                    if key:
                        available_keys.add(key)
            elif isinstance(item, str):
                available_keys.add(item)

    canonical_available = set()
    for k in available_keys:
        canonical_available.add(k)
        canonical = EQUIVALENT_KEYS.get(k)
        if canonical is not None:
            if isinstance(canonical, list):
                canonical_available.update(canonical)
            else:
                canonical_available.add(canonical)

    for doc in claim_docs_required:
        # Forward check: doc.key itself is in canonical_available
        # (covers canonical required key matched by alias submitted key)
        direct_match = doc.key in canonical_available
        # Reverse check: doc.key is an alias — look up its canonical form
        # and check if that canonical is in canonical_available
        # (covers alias required key matched by canonical submitted key)
        doc_canonical = EQUIVALENT_KEYS.get(doc.key)
        if isinstance(doc_canonical, list):
            reverse_match = any(c in canonical_available for c in doc_canonical)
        else:
            reverse_match = doc_canonical is not None and doc_canonical in canonical_available
        doc.available = direct_match or reverse_match

    # SUB-STEP G — Build image_docs_reminder
    IMAGE_DOC_KEYS = {
        "clinical_photo_initial", "post_treatment_clinical_photograph",
        "clinical_photo_intraop", "intraoperative_photo_gross_specimen",
        "post_op_clinical_photograph", "x_ray", "ecg_strip",
        "implant_invoice_sticker", "stent_sticker_carton",
        "burns_followup_photo_day5", "burns_followup_photo_day10",
        "burns_followup_photo_day15", "burns_followup_photo_day20"
    }
    image_docs_reminder = []
    if isinstance(docs_submitted, list):
        for doc in docs_submitted:
            if isinstance(doc, dict):
                key = doc.get("key")
                label = doc.get("label") or key
                if key in IMAGE_DOC_KEYS:
                    image_docs_reminder.append(label)
            elif isinstance(doc, str):
                if doc in IMAGE_DOC_KEYS:
                    image_docs_reminder.append(doc)
    elif isinstance(docs_submitted, dict):
        for key in docs_submitted.keys():
            if key in IMAGE_DOC_KEYS:
                image_docs_reminder.append(key)

    # SUB-STEP H — Build missing list
    claim_docs_missing = [
        d for d in claim_docs_required
        if not d.available and d.criticality != "reminder_only"
    ]

    return (claim_docs_required, claim_docs_missing, image_docs_reminder)


def _check_los(
    context: dict,
    discharge_dict: dict,
    preauth_output_dict: dict,
) -> tuple[bool, str | None]:
    """Verify actual Length of Stay (LoS) against pre-authorized limits."""
    actual = discharge_dict.get("admission", {}).get("actual_los_days", 0)
    indicative = context["los_indicative"]

    if indicative == 0:
        return (False, None)

    enhancement_plan = preauth_output_dict.get("enhancement_plan", [])

    if actual > indicative:
        los_deviation = True
        if enhancement_plan:
            note = f"LOS {actual} days exceeds indicative {indicative} days. Enhancement plan was filed."
        else:
            note = f"LOS {actual} days exceeds indicative {indicative} days. No enhancement plan found — CPD may query extended stay."
    else:
        los_deviation = False
        note = None

    return (los_deviation, note)


def _detect_deviations(
    context: dict,
    discharge_dict: dict,
    preauth_output_dict: dict,
    preauth_input_dict: dict | None = None,
) -> list[DeviationItem]:
    """Inspect and report any procedural, doctor, ward, or LoS discrepancies."""
    deviations = []
    
    def get_val(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    # CHECK A — Procedure change
    preauth_input_dict = preauth_input_dict or {}
    preauth_clinical = preauth_input_dict.get("clinical", {})
    preauth_procedure = (
        preauth_clinical.get("planned_procedure")
        or (
            preauth_output_dict
            .get("selected_packages", [{}])[0]
            .get("validated", {})
            .get("procedure_name", "")
        )
        or ""
    )
            
    actual_procedure = discharge_dict.get("clinical", {}).get("final_procedure_performed")
    if actual_procedure and preauth_procedure and actual_procedure.lower().strip() != preauth_procedure.lower().strip():
        deviations.append(
            DeviationItem(
                deviation_type="procedure_change",
                description="Procedure performed at discharge differs from pre-auth approved procedure",
                from_value=preauth_procedure,
                to_value=actual_procedure,
                severity="warning",
                justification_draft=None,
                justification_required=True
            )
        )

    # CHECK B — Ward category change
    preauth_bed_category = preauth_output_dict.get("preauth_bed_category")
    if not preauth_bed_category:
        preauth_bed_category = preauth_output_dict.get("clinical", {}).get("bed_category")
    selected_packages = preauth_output_dict.get("selected_packages", [])
    if not preauth_bed_category and selected_packages:
        validated = get_val(selected_packages[0], "validated")
        if validated:
            preauth_bed_category = get_val(validated, "bed_category")
            
    actual_ward = discharge_dict.get("admission", {}).get("ward_category_actual")
    if preauth_bed_category and actual_ward and preauth_bed_category != actual_ward:
        WARD_HIERARCHY = ["ward", "hdu", "icu", "icu_vent"]
        preauth_idx = WARD_HIERARCHY.index(preauth_bed_category) if preauth_bed_category in WARD_HIERARCHY else -1
        actual_idx = WARD_HIERARCHY.index(actual_ward) if actual_ward in WARD_HIERARCHY else -1
        if actual_idx > preauth_idx:
            deviation_type = "ward_upgrade"
            severity = "warning"
            justification_required = True
        else:
            deviation_type = "ward_downgrade"
            severity = "info"
            justification_required = False
            
        deviations.append(
            DeviationItem(
                deviation_type=deviation_type,
                description=f"Ward category changed from pre-auth approved {preauth_bed_category} to actual {actual_ward}",
                from_value=preauth_bed_category,
                to_value=actual_ward,
                severity=severity,
                justification_draft=None,
                justification_required=justification_required
            )
        )

    # CHECK C — Doctor change
    preauth_reg = preauth_output_dict.get("preauth_doctor_registration")
    if not preauth_reg:
        preauth_reg = preauth_output_dict.get("clinical", {}).get("treating_doctor", {}).get("registration_number")
        
    actual_reg = discharge_dict.get("treating_consultant", {}).get("registration_number")
    if preauth_reg and actual_reg and preauth_reg != actual_reg:
        deviations.append(
            DeviationItem(
                deviation_type="doctor_change",
                description="Treating doctor registration number differs from pre-auth",
                from_value=preauth_reg,
                to_value=actual_reg,
                severity="info",
                justification_draft=None,
                justification_required=False
            )
        )

    # CHECK D — LOS exceeded with no enhancement
    los_indicative = context["los_indicative"]
    actual_los = discharge_dict.get("admission", {}).get("actual_los_days", 0)
    enhancement_plan = preauth_output_dict.get("enhancement_plan", [])
    if los_indicative > 0 and actual_los > los_indicative and not enhancement_plan:
        deviations.append(
            DeviationItem(
                deviation_type="los_exceeded",
                description=f"Actual LOS {actual_los} days exceeds indicative {los_indicative} days with no enhancement filed",
                from_value=f"{los_indicative} days (indicative)",
                to_value=f"{actual_los} days (actual)",
                severity="warning",
                justification_draft=None,
                justification_required=True
            )
        )

    # CHECK E — Merge declared deviations
    for dev in discharge_dict.get("deviations_declared", []):
        existing_types = [d.deviation_type for d in deviations]
        if dev.get("type") not in existing_types:
            deviations.append(
                DeviationItem(
                    deviation_type=dev.get("type", "other"),
                    description=dev.get("reason", "Declared by MEDCO"),
                    from_value=dev.get("from", ""),
                    to_value=dev.get("to", ""),
                    severity="warning",
                    justification_draft=None,
                    justification_required=True
                )
            )

    # CHECK F — Urology minor procedure sub-inclusion
    if context["specialty_code"] == "SU" and context["base_rate_inr"] and context["base_rate_inr"] >= 15000:
        MINOR_PROCEDURES = ["cystoscopy", "ureteric catheterisation", "retrograde pyelogram", "dj stenting", "nephrostomy"]
        actual_lower = (discharge_dict.get("clinical", {}).get("final_procedure_performed", "") or "").lower()
        for minor in MINOR_PROCEDURES:
            if minor in actual_lower:
                deviations.append(
                    DeviationItem(
                        deviation_type="urology_minor_sub_inclusion",
                        description=f"{minor} is sub-included in urology package rate ≥₹15,000 and cannot be claimed separately (HBP Guidelines Section 4.25)",
                        from_value="Included in package rate",
                        to_value=f"Listed as separate procedure: {minor}",
                        severity="block",
                        justification_draft=None,
                        justification_required=False
                    )
                )

    return deviations


def _compute_special_payment(
    context: dict,
    discharge_dict: dict,
) -> SpecialPaymentResult | None:
    """Apply HBP guidelines to calculate payable amounts for LAMA/DAMA, death, or referral events."""
    admission = discharge_dict.get("admission", {})
    discharge_status = admission.get("discharge_status")
    if not discharge_status or discharge_status == "recovered":
        return None

    base_rate = context.get("base_rate_inr") or 0
    actual_los = admission.get("actual_los_days", 0)
    billing_type = context.get("billing_type")

    # Compute daily rate
    breakdown = admission.get("bed_category_breakdown", {})
    total_days = (
        breakdown.get("ward_days", 0) +
        breakdown.get("hdu_days", 0) +
        breakdown.get("icu_days", 0) +
        breakdown.get("icu_vent_days", 0)
    )
    if total_days > 0:
        weighted = (
            breakdown.get("ward_days", 0) * 2100 +
            breakdown.get("hdu_days", 0) * 3300 +
            breakdown.get("icu_days", 0) * 8500 +
            breakdown.get("icu_vent_days", 0) * 9000
        )
        per_day_rate = weighted // total_days
    else:
        per_day_rate = 2100

    op_findings = discharge_dict.get("clinical", {}).get("operative_findings")
    surgery_done = op_findings is not None and bool(str(op_findings).strip())

    trigger = ""
    payable = 0
    pct = 0
    note = ""

    if discharge_status in ("lama", "dama"):
        if not surgery_done:
            trigger = "lama_before_surgery"
            payable = per_day_rate * actual_los
            pct = 100
            note = "100% of daily rate × actual days — no surgery performed. HBP Guidelines Section 6.1.a.i"
        else:
            trigger = "lama_after_surgery"
            payable = int(base_rate * 0.75)
            pct = 75
            note = "75% of package rate — LAMA after surgery. HBP Guidelines Section 6.1.a.ii"

    elif discharge_status == "death":
        if actual_los <= 1:
            trigger = "death_within_24h_preauth_approved"
            payable = int(base_rate * 0.50)
            pct = 50
            note = "50% of package amount — death within 24h with preauth approved. Mortality audit mandatory. HBP Guidelines 6.2.c"
        elif not surgery_done:
            trigger = "death_before_surgery"
            payable = per_day_rate * actual_los
            pct = 100
            note = "100% of daily rate × actual days — death before surgery. HBP Guidelines Section 6.2.a.i"
        else:
            complications = (discharge_dict.get("clinical", {}).get("complications") or "").lower()
            if "on table" in complications or "during surgery" in complications:
                trigger = "death_on_table"
                payable = int(base_rate * 0.75)
                pct = 75
                note = "75% of package rate — death on table. HBP Guidelines Section 6.2.a.ii"
            else:
                trigger = "death_after_surgery"
                payable = base_rate
                pct = 100
                note = "100% after detailed medical audit — death after surgery. HBP Guidelines Section 6.2.a.iii"

    elif discharge_status == "referred":
        if not surgery_done:
            trigger = "referral_before_pac"
            payable = per_day_rate * actual_los
            pct = 100
            note = "100% of daily rate × actual days to referring hospital. HBP Guidelines Section 6.3.1.A.i"
        else:
            trigger = "referral_after_surgery"
            payable = int(base_rate * 0.75)
            pct = 75
            note = "75% of package rate to referring hospital. HBP Guidelines Section 6.3.1.A.iii"
            
    else:
        return None

    return SpecialPaymentResult(
        trigger=trigger,
        base_package_rate_inr=base_rate,
        payable_amount_inr=payable,
        payable_percentage=pct,
        computation_note=note
    )


def _check_audit_flags(
    context: dict,
    discharge_dict: dict,
    claim_docs_missing: list[ClaimDocumentItem],
) -> list[str]:
    """Apply audit rule triggers to identify potential billing anomalies."""
    triggered_flags = []

    # FLAG "days_billed_gt_stay"
    actual_los = discharge_dict.get("admission", {}).get("actual_los_days", 0)
    breakdown = discharge_dict.get("admission", {}).get("bed_category_breakdown", {})
    breakdown_total = (
        breakdown.get("ward_days", 0) +
        breakdown.get("hdu_days", 0) +
        breakdown.get("icu_days", 0) +
        breakdown.get("icu_vent_days", 0)
    )
    if breakdown_total > 0 and breakdown_total != actual_los:
        triggered_flags.append("days_billed_gt_stay")

    # FLAG "stable_in_icu"
    if breakdown.get("icu_days", 0) > 0 or breakdown.get("icu_vent_days", 0) > 0:
        treatment = (discharge_dict.get("clinical", {}).get("treatment_given", "") or "").lower()
        ICU_KEYWORDS = {"sepsis", "ventilator", "critical", "haemodynamic", "hemodynamic", "vasopressor", "icu", "intensive care", "inotrope"}
        if not any(kw in treatment for kw in ICU_KEYWORDS):
            triggered_flags.append("stable_in_icu")

    # FLAG "prolonged_stay"
    if context["los_indicative"] > 0:
        if actual_los > context["los_indicative"] * 1.5:
            triggered_flags.append("prolonged_stay")

    # FLAG "missing_stg_docs"
    if any(doc.criticality == "hard_block" for doc in claim_docs_missing):
        triggered_flags.append("missing_stg_docs")

    # FLAG "unspecified_package_abuse"
    if context["procedure_code"].startswith("USP"):
        triggered_flags.append("unspecified_package_abuse")

    # FLAG "cash_collection_detected"
    if discharge_dict.get("patient_amount_collected_inr", 0) > 0:
        triggered_flags.append("cash_collection_detected")

    return triggered_flags


def _check_sha_notification(
    discharge_dict: dict,
) -> str | None:
    """Verify that SHA was notified within 24 hours of non-standard discharges."""
    admission = discharge_dict.get("admission", {})
    discharge_status = admission.get("discharge_status")
    if discharge_status not in ("lama", "dama", "death", "referred"):
        return None

    sha_date_str = discharge_dict.get("sha_notification_date")
    if sha_date_str is None:
        return (
            f"SHA notification date not recorded. For {discharge_status} "
            f"cases, hospital must notify SHA within 24 hours of the event "
            f"to qualify for partial payment (HBP Guidelines Section 6)."
        )

    try:
        date_of_discharge_str = admission.get("date_of_discharge")
        if not date_of_discharge_str:
            return None
        
        sha_date = date.fromisoformat(sha_date_str)
        discharge_date = date.fromisoformat(date_of_discharge_str)
        if sha_date > discharge_date + timedelta(days=1):
            return (
                f"SHA notified on {sha_date_str} — more than 24 hours after "
                f"discharge on {date_of_discharge_str}."
                f" Partial payment eligibility may be affected "
                f"(HBP Guidelines Section 6)."
            )
    except Exception as exc:
        logger.warning("Error parsing dates in _check_sha_notification: %s", exc)

    return None


def _check_specialty_specific(
    context: dict,
    discharge_dict: dict,
) -> list[str]:
    """Execute specialty-specific guidelines checks (e.g. Burns follow-ups, Cardiology stents)."""
    notes = []

    # Burns (BM)
    if context["specialty_code"] == "BM":
        notes.append(
            "Burns package: Follow-up clinical photographs at days 5, 10, "
            "15, and 20 are required as claim documents per HBP Guidelines "
            "Section 4.1. Ensure all are uploaded to TMS."
        )

    # Cardiology (MC or SV)
    if context["specialty_code"] in ("MC", "SV"):
        docs_submitted = discharge_dict.get("documents_submitted", [])
        submitted_keys = set()
        if isinstance(docs_submitted, list):
            for doc in docs_submitted:
                if isinstance(doc, dict):
                    submitted_keys.add(doc.get("key", ""))
                elif isinstance(doc, str):
                    submitted_keys.add(doc)
        elif isinstance(docs_submitted, dict):
            submitted_keys = set(docs_submitted.keys())
            
        if not any("stent" in k for k in submitted_keys):
            notes.append(
                "Cardiology package: Stent carton/sticker detailing stent "
                "particulars must be submitted per HBP Guidelines Section 4.2."
            )

    return notes


def _compute_claim_status(
    claim_docs_missing: list[ClaimDocumentItem],
    deviations: list[DeviationItem],
    cpd_checklist_results: list[CPDChecklistResult],
) -> str:
    """Determine final claim routing state based on priority-ordered severity rules."""
    # 1. Any claim_docs_missing with criticality=="hard_block" -> "CLAIM_BLOCKED"
    if any(doc.criticality == "hard_block" for doc in claim_docs_missing):
        return "CLAIM_BLOCKED"

    # 2. Any deviation with severity=="block" -> "CLAIM_BLOCKED"
    if any(dev.severity == "block" for dev in deviations):
        return "CLAIM_BLOCKED"

    # 3. Any CPDChecklistResult with risk_level=="high" -> "CLAIM_DEVIATION"
    if any(res.risk_level == "high" for res in cpd_checklist_results):
        return "CLAIM_DEVIATION"

    # 4. Any deviation with severity=="warning" -> "CLAIM_DEVIATION"
    if any(dev.severity == "warning" for dev in deviations):
        return "CLAIM_DEVIATION"

    # 5. Any claim_docs_missing with criticality=="cpd_query_risk" -> "CLAIM_GAPS"
    if any(doc.criticality == "cpd_query_risk" for doc in claim_docs_missing):
        return "CLAIM_GAPS"

    # 6. Any CPDChecklistResult with risk_level=="medium" -> "CLAIM_GAPS"
    if any(res.risk_level == "medium" for res in cpd_checklist_results):
        return "CLAIM_GAPS"

    # 7. Otherwise -> "CLAIM_READY"
    return "CLAIM_READY"
