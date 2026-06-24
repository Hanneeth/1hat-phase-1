from __future__ import annotations
import json
import logging
import sys
from pathlib import Path
from phases.phase11_claim import run_phase11
from phases.phase10_output import _coerce_serialisable
from dataclasses import asdict
from logger_setup import setup_logging

logger = logging.getLogger(__name__)


def load_discharge_input(path: str) -> dict:
    """Open and parse discharge JSON from path."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Discharge input loaded from %s", path)
        return data
    except Exception as exc:
        logger.error("Failed to load discharge input from %s: %s", path, exc)
        raise


def load_preauth_output(preauth_input_path: str) -> dict:
    """Load or run the pre-authorization pipeline on preauth_input_path."""
    try:
        path_obj = Path(preauth_input_path)
        test_id = path_obj.stem
        cached_path = Path("tests/outputs") / f"{test_id}_output.json"
        
        if cached_path.exists():
            with open(cached_path, "r", encoding="utf-8") as f:
                output_dict = json.load(f)
            logger.info("Pre-auth output loaded from cache: %s", cached_path)
            return output_dict
            
        logger.info("Cache not found at %s. Running pre-auth pipeline for: %s", cached_path, preauth_input_path)
        with open(preauth_input_path, "r", encoding="utf-8") as f:
            raw_json = json.load(f)
            
        from input_validator import validate_input
        from main import build_session, run_pipeline
        from phases.phase10_output import serialize_output
        
        valid, errors = validate_input(raw_json)
        if not valid:
            raise ValueError(f"Invalid pre-auth input in {preauth_input_path}: {errors}")
            
        session = build_session(raw_json)
        output_obj = run_pipeline(session)
        output_dict = serialize_output(output_obj)
        logger.info("Pre-auth pipeline executed successfully for: %s", preauth_input_path)
        return output_dict
        
    except Exception as exc:
        logger.error("Error loading pre-auth output for %s: %s", preauth_input_path, exc)
        raise


def print_claim_summary(output_dict: dict, base_rate_inr: int = 0) -> None:
    """Print a TL;DR summary block of the claim verification output to the terminal."""
    DIVIDER = "═" * 72
    
    status = output_dict.get("claim_status", "UNKNOWN")
    STATUS_ICONS = {
        "CLAIM_READY": "✅",
        "CLAIM_GAPS": "🟡",
        "CLAIM_DEVIATION": "🟠",
        "CLAIM_BLOCKED": "🔴",
    }
    icon = STATUS_ICONS.get(status, "❓")
    
    procedure_code = output_dict.get("procedure_code", "?")
    package_name = output_dict.get("package_name", "?")
    
    special_payment = output_dict.get("special_payment")
    if special_payment and special_payment.get("base_package_rate_inr"):
        base_rate = special_payment.get("base_package_rate_inr", 0)
    else:
        base_rate = base_rate_inr

    print(f"\n{DIVIDER}")
    print(f"  IRIS CLAIM  {icon}  {status}")
    print(f"  {procedure_code} · {package_name} · ₹{base_rate:,}")
    
    los_actual = output_dict.get("los_actual", 0)
    los_approved_indicative = output_dict.get("los_approved_indicative", 0)
    los_deviation_note = output_dict.get("los_deviation_note")
    dev_note_suffix = f" | {los_deviation_note}" if los_deviation_note else ""
    print(f"  LOS: {los_actual} days actual vs {los_approved_indicative} indicative{dev_note_suffix}")

    print("\nCLAIM DOCUMENTS:")
    docs_required = output_dict.get("claim_docs_required", [])
    if docs_required:
        for doc in docs_required:
            available = doc.get("available", False)
            criticality = doc.get("criticality")
            label = doc.get("label", "")
            pkg_code = doc.get("package_code")
            pkg_suffix = f"[{pkg_code}]" if pkg_code else "[universal]"
            
            if available:
                status_icon = "✅"
            elif criticality == "hard_block":
                status_icon = "🔴"
            else:
                status_icon = "🟡"
            print(f"  {status_icon} {label} {pkg_suffix}")
    else:
        print("  None required")

    print("\nCPD EVALUATION:")
    cpd_verdict = (output_dict.get("cpd_verdict") or "unknown").upper()
    cpd_verdict_summary = output_dict.get("cpd_verdict_summary", "")
    CPD_ICONS = {
        "CLEAN": "✅",
        "GAPS_PRESENT": "🟡",
        "LIKELY_DEDUCTED": "🔴",
    }
    cpd_icon = CPD_ICONS.get(cpd_verdict, "❓")
    print(f"  {cpd_icon} {procedure_code} — {cpd_verdict}: {cpd_verdict_summary}")
    
    llm_status = output_dict.get("llm_evaluation_status", "unknown")
    if llm_status != "success":
        print(f"  ⚠ LLM evaluation {llm_status.upper()} — checklist results unavailable")
        
    for res in output_dict.get("cpd_checklist_results", []):
        risk = res.get("risk_level")
        if risk in ("high", "medium"):
            print(f"  ⚠ [CPD CHECKLIST] {res.get('question')}")

    print("\nDEVIATIONS DETECTED:")
    deviations = output_dict.get("deviations_detected", [])
    if deviations:
        severity_icons = {
            "none": "✅",
            "info": "ℹ",
            "warning": "⚠",
            "block": "🔴",
        }
        for dev in deviations:
            dev_type = dev.get("deviation_type")
            from_val = dev.get("from_value")
            to_val = dev.get("to_value")
            desc = dev.get("description")
            sev = dev.get("severity", "warning")
            icon = severity_icons.get(sev, "⚠")
            
            if sev == "none":
                print(f"  {icon} [{dev_type}] Assessed as no deviation — wording difference only")
            elif sev == "info":
                print(f"  {icon} [{dev_type}] {from_val} → {to_val}")
                print(f"     {desc}")
            else:
                just = dev.get("justification_draft")
                if just is None:
                    just_str = "Draft justification: pending LLM"
                else:
                    if len(just) > 120:
                        just_str = f'Draft justification: "{just[:120]}..."'
                    else:
                        just_str = f'Draft justification: "{just}"'
                print(f"  {icon} [{dev_type}] {from_val} → {to_val}")
                print(f"     {desc}")
                print(f"     {just_str}")
    else:
        print("  None detected")

    print("\nSPECIAL PAYMENT:")
    if special_payment:
        trigger = special_payment.get("trigger")
        payable_amount_inr = special_payment.get("payable_amount_inr", 0)
        payable_percentage = special_payment.get("payable_percentage", 0)
        comp_note = special_payment.get("computation_note", "")
        print(f"  ⚠ {trigger}: ₹{payable_amount_inr:,} ({payable_percentage}% of ₹{base_rate:,})")
        print(f"     {comp_note}")
    else:
        print("  None — normal discharge")

    audit_flags = output_dict.get("audit_flags_triggered", [])
    flags_str = ", ".join(audit_flags) if audit_flags else "none"
    print(f"\nAUDIT FLAGS: {flags_str}")

    sha_warning = output_dict.get("sha_notification_warning")
    sha_str = sha_warning if sha_warning else "No issues"
    print(f"\nSHA NOTIFICATION: {sha_str}")

    print("\nSPECIALTY NOTES:")
    spec_notes = output_dict.get("specialty_specific_notes", [])
    if spec_notes:
        for note in spec_notes:
            print(f"  {note}")
    else:
        print("  None")

    print("\nIMAGE DOCS TO UPLOAD TO TMS:")
    reminders = output_dict.get("image_docs_reminder", [])
    if reminders:
        for item in reminders:
            print(f"  • {item}")
    else:
        print("  None required")
        
    print(f"\n{DIVIDER}\n")


if __name__ == "__main__":
    setup_logging()
    
    if len(sys.argv) < 2:
        print("Usage: python main_claim.py <discharge_json_path>")
        sys.exit(1)
        
    input_arg = sys.argv[1]
    input_path = Path(input_arg)

    if input_path.is_dir():
        # Folder path — run intake layer to extract and parse PDFs/DOCXs
        try:
            from intake.intake_runner import run_intake, IntakeError
            discharge_dict = run_intake(str(input_path))
            logger.info("Intake layer completed successfully for folder: %s", input_path)
        except IntakeError as exc:
            logger.error("Intake failed: %s", exc)
            sys.exit(1)
    else:
        # JSON file path — existing behaviour, unchanged
        with open(input_path, "r", encoding="utf-8") as f:
            discharge_dict = json.load(f)
        logger.info("Discharge input loaded from %s", input_path)
    
    preauth_input_path = discharge_dict.get("preauth_input_path")
    if not preauth_input_path:
        logger.error("preauth_input_path missing from discharge JSON")
        sys.exit(1)
        
    preauth_output_dict = load_preauth_output(preauth_input_path)
    try:
        preauth_input_dict = load_discharge_input(preauth_input_path)
        logger.info("Pre-auth input loaded from %s", preauth_input_path)
    except Exception as exc:
        logger.warning(
            "Could not load pre-auth input from %s: %s — "
            "proceeding without pre-auth baseline context.",
            preauth_input_path,
            exc,
        )
        preauth_input_dict = {}
    
    output = run_phase11(
        discharge_dict,
        preauth_output_dict,
        preauth_input_dict,
    )
    
    serialized_dict = _coerce_serialisable(asdict(output))
    
    print(json.dumps(serialized_dict, indent=2))
    print()
    
    try:
        base_rate_inr = (
            preauth_output_dict
            .get("selected_packages", [{}])[0]
            .get("validated", {})
            .get("base_rate_inr", 0)
        ) or 0
    except Exception:
        base_rate_inr = 0
    print_claim_summary(serialized_dict, base_rate_inr=base_rate_inr)
    
    logger.info("Stage 3 complete. Claim status: %s", output.claim_status)
