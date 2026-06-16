"""
main.py — IRIS Pipeline Orchestrator
======================================
CLI entry point and phase sequencer for the IRIS PM-JAY pre-authorisation engine.

Usage:
    python main.py input.json          # read from file
    python main.py < input.json        # read from stdin

Output:
    IRISOutput serialised as formatted JSON written to stdout.
    All logs go to stdout via logger_setup (separated from JSON by logger format).

Pipeline sequence (deterministic, fixed order):
    Phase 0  → preflight (patient + hospital context)
    Phase 1  → emergency routing
    Phase 2  → fuzzy candidate generation
    Phase 3  → per-package validation (rules + LLM STG check)
    --- early-exit checks after each of the above ---
    Phase 4  → multi-package combination rules
    Phase 5  → wallet sufficiency
    Phase 6  → exclusion verification
    Phase 7  → comorbidity resolution
    Phase 8  → special populations
    Phase 9  → document gap analysis
    Phase 10 → output assembly (pure read, returns IRISOutput)

Early exits:
    - After any phase: has_block_flag() → True → skip to Phase 10 immediately.
    - After Phase 3: validated_packages empty → set usp_recommended, add warning
      flag, skip Phases 4-8, jump to Phase 9 then Phase 10.
"""

import sys
import json
import logging
from pathlib import Path

from logger_setup import setup_logging
from input_validator import validate_input
from session import IRISSession
from models import (
    ClinicalInput,
    Investigation,
    StructuredValue,
    DocumentInHand,
    ExaminationFindings,
    PersonalHistory,
    TreatingDoctor,
    IRISOutput,
)
from phases.phase0_preflight import run_phase0
from phases.phase1_emergency import run_phase1
from phases.phase2_candidates import run_phase2
from phases.phase3_validator import run_phase3
from phases.phase4_multipackage import run_phase4
from phases.phase5_financial import run_phase5
from phases.phase6_exclusion import run_phase6
from phases.phase7_comorbidity import run_phase7
from phases.phase8_special_pop import run_phase8
from phases.phase9_documents import run_phase9
from phases.phase10_output import run_phase10, serialize_output

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Clinical input parser
# ---------------------------------------------------------------------------

def parse_clinical_input(raw_clinical: dict) -> ClinicalInput:
    """Convert a raw clinical dict (from input JSON) into a ClinicalInput dataclass.

    Handles all nested object parsing:
      - investigations: list of Investigation, each with an optional list of
        StructuredValue objects (None when document_available=False or not OCR'd).
      - non_clinical_documents_in_hand: list of DocumentInHand.
      - treating_doctor: TreatingDoctor or None if absent.
      - examination_findings: ExaminationFindings or None if absent.
      - personal_history: PersonalHistory or None if absent.

    vitals is kept as a raw dict — the phase functions and LLM receive it as-is.
    All optional fields use .get() with safe defaults so the parser is
    tolerant of partially-populated inputs (test stubs, partial entries, etc.).

    Args:
        raw_clinical: The value of raw_input_json["clinical"] — a plain dict.

    Returns:
        ClinicalInput dataclass instance with all nested objects populated.

    Side effects:
        None.
    """
    # --- investigations ---
    investigations: list[Investigation] = []
    for raw_inv in raw_clinical.get("investigations", []):
        raw_sv = raw_inv.get("structured_values")
        structured_values = None
        if raw_sv is not None:
            structured_values = [
                StructuredValue(
                    parameter=sv.get("parameter", ""),
                    value=sv.get("value"),
                    unit=sv.get("unit"),
                    flag=sv.get("flag"),
                    leads=sv.get("leads"),
                )
                for sv in raw_sv
            ]
        investigations.append(
            Investigation(
                type=raw_inv.get("type", "other"),
                result_summary=raw_inv.get("result_summary"),
                structured_values=structured_values,
                document_available=raw_inv.get("document_available", False),
                report_date=raw_inv.get("report_date"),
            )
        )

    # --- non_clinical_documents_in_hand ---
    docs_in_hand: list[DocumentInHand] = [
        DocumentInHand(
            key=d.get("key", ""),
            label=d.get("label", ""),
            available=d.get("available", False),
        )
        for d in raw_clinical.get("non_clinical_documents_in_hand", [])
    ]

    # --- treating_doctor ---
    raw_doc = raw_clinical.get("treating_doctor")
    treating_doctor: TreatingDoctor | None = None
    if raw_doc:
        treating_doctor = TreatingDoctor(
            name=raw_doc.get("name", ""),
            registration_number=raw_doc.get("registration_number", ""),
            qualification=raw_doc.get("qualification", ""),
            specialty_code=raw_doc.get("specialty_code", ""),
        )

    # --- examination_findings ---
    raw_ef = raw_clinical.get("examination_findings")
    examination_findings: ExaminationFindings | None = None
    if raw_ef:
        examination_findings = ExaminationFindings(
            general=raw_ef.get("general"),
            cvs=raw_ef.get("cvs"),
            rs=raw_ef.get("rs"),
            abdomen=raw_ef.get("abdomen"),
            cns=raw_ef.get("cns"),
            local=raw_ef.get("local"),
        )

    # --- personal_history ---
    raw_ph = raw_clinical.get("personal_history")
    personal_history: PersonalHistory | None = None
    if raw_ph:
        personal_history = PersonalHistory(
            smoking=raw_ph.get("smoking"),
            alcohol=raw_ph.get("alcohol"),
            diet=raw_ph.get("diet"),
        )

    return ClinicalInput(
        admission_date=raw_clinical.get("admission_date"),
        bed_category=raw_clinical.get("bed_category"),
        is_emergency=raw_clinical.get("is_emergency", False),
        is_medico_legal=raw_clinical.get("is_medico_legal", False),
        chief_complaints=raw_clinical.get("chief_complaints", ""),
        duration_days=raw_clinical.get("duration_days", 0),
        history_of_present_illness=raw_clinical.get("history_of_present_illness"),
        provisional_diagnosis=raw_clinical.get("provisional_diagnosis", ""),
        planned_procedure=raw_clinical.get("planned_procedure"),
        weight_kg=raw_clinical.get("weight_kg"),
        height_cm=raw_clinical.get("height_cm"),
        vitals=raw_clinical.get("vitals", {}),
        examination_findings=examination_findings,
        investigations=investigations,
        comorbidities=raw_clinical.get("comorbidities", []),
        past_medical_history=raw_clinical.get("past_medical_history"),
        past_surgical_history=raw_clinical.get("past_surgical_history"),
        # --- fields with defaults ---
        current_medications=raw_clinical.get("current_medications", []),
        allergies=raw_clinical.get("allergies", []),
        personal_history=personal_history,
        family_history=raw_clinical.get("family_history"),
        non_clinical_documents_in_hand=docs_in_hand,
        treating_doctor=treating_doctor,
        notes=raw_clinical.get("notes"),
    )


# ---------------------------------------------------------------------------
# Session builder
# ---------------------------------------------------------------------------

def build_session(raw_json: dict) -> IRISSession:
    """Create an IRISSession from the raw input JSON dict.

    Parses the clinical block via parse_clinical_input() and constructs a fresh
    IRISSession with input_data and clinical populated. All other session fields
    start at their dataclass defaults — each phase populates its own subset.

    Args:
        raw_json: Full IRIS input JSON (top-level keys: patient, hospital, clinical).

    Returns:
        IRISSession ready to be passed into Phase 0.

    Side effects:
        None.
    """
    parsed_clinical = parse_clinical_input(raw_json.get("clinical", {}))
    return IRISSession(input_data=raw_json, clinical=parsed_clinical)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(session: IRISSession) -> IRISOutput:
    """Run all pipeline phases in fixed sequence with early-exit checks.

    Phases are called in order: 0 → 1 → 2 → 3 → (routing) → 4 → 5 → 6 → 7 → 8
    → 9 → 10. After each of Phases 0-3, session.has_block_flag() is checked;
    a True result causes an immediate skip to Phase 10.

    Special routing after Phase 3:
        If session.validated_packages is empty (and no block flag was raised),
        this indicates no standard PM-JAY package matched the clinical input.
        In that case:
          - session.usp_recommended is set to True
          - A USP_RECOMMENDED warning flag is added
          - Phases 4-8 are skipped
          - Phase 9 (document gap) and Phase 10 (output) are still run

    Args:
        session: A freshly built IRISSession (output of build_session).

    Returns:
        IRISOutput assembled by Phase 10.

    Side effects:
        Mutates session in place through all phase calls. Phase 10 is read-only
        and returns a new IRISOutput object without further session mutation.
    """
    # --- Phase 0: patient + hospital preflight ---
    logger.info("Pipeline start — running Phase 0")
    session = run_phase0(session)
    if session.has_block_flag():
        logger.warning("Block flag after Phase 0 — skipping to Phase 10")
        return run_phase10(session)

    # --- Phase 1: emergency routing ---
    logger.info("Running Phase 1")
    session = run_phase1(session)
    if session.has_block_flag():
        logger.warning("Block flag after Phase 1 — skipping to Phase 10")
        return run_phase10(session)

    # --- Phase 2: fuzzy candidate generation ---
    logger.info("Running Phase 2")
    session = run_phase2(session)
    if session.has_block_flag():
        logger.warning("Block flag after Phase 2 — skipping to Phase 10")
        return run_phase10(session)

    # --- Phase 3: per-package validation (rules + LLM STG check) ---
    logger.info("Running Phase 3")
    session = run_phase3(session)
    if session.has_block_flag():
        logger.warning("Block flag after Phase 3 — skipping to Phase 10")
        return run_phase10(session)

    # --- Special routing: zero validated packages → USP pathway ---
    if len(session.validated_packages) == 0:
        logger.warning(
            "Phase 3 returned 0 validated packages — activating USP pathway"
        )
        session.usp_recommended = True
        session.add_flag(
            code="USP_RECOMMENDED",
            message=(
                "No standard PM-JAY packages validated for this clinical input. "
                "Unspecified Surgical Package (USP) pathway may apply. Consult SHA."
            ),
            severity="warning",
        )
        # Skip Phases 4-8; still run document check and output assembly
        session = run_phase9(session)
        return run_phase10(session)

    # --- Normal pathway: Phases 4-9 ---
    logger.info("Running Phase 4")
    session = run_phase4(session)

    logger.info("Running Phase 5")
    session = run_phase5(session)

    logger.info("Running Phase 6")
    session = run_phase6(session)

    logger.info("Running Phase 7")
    session = run_phase7(session)

    logger.info("Running Phase 8")
    session = run_phase8(session)

    logger.info("Running Phase 9")
    session = run_phase9(session)

    # --- Phase 10: output assembly ---
    logger.info("Running Phase 10")
    return run_phase10(session)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def print_summary(output_dict: dict) -> None:
    """Print a human-readable 3-5 line summary of the IRIS result to stdout."""

    DIVIDER = "═" * 72

    status = output_dict.get("readiness_status", "UNKNOWN")

    # Status icon
    STATUS_ICONS = {
        "READY": "✅",
        "READY_WITH_WARNINGS": "⚠️",
        "CONDITIONAL": "🟡",
        "BLOCKED": "🔴",
    }
    icon = STATUS_ICONS.get(status, "❓")

    # Selected packages line
    packages = output_dict.get("selected_packages", [])
    if packages:
        pkg_parts = []
        for fp in packages:
            v = fp.get("validated", {})
            code = v.get("procedure_code", "?")
            name = v.get("package_name", "?")
            proc = v.get("procedure_name", "")
            rate = v.get("base_rate_inr")
            role = fp.get("role", "primary")
            factor = fp.get("deduction_factor", 1.0)
            rate_str = f"₹{int(rate * factor):,}" if rate else "rate unknown"
            pkg_parts.append(f"{code} · {name} ({proc}) · {rate_str} [{role}]")
        packages_line = " | ".join(pkg_parts)
    else:
        packages_line = "No packages selected"

    # Financial line
    estimated = output_dict.get("estimated_total_inr", 0)
    copayment = output_dict.get("copayment_required", False)
    gap = output_dict.get("copayment_gap_inr")
    if copayment and gap:
        financial_line = (
            f"Estimated ₹{estimated:,} | ⚠️  CO-PAYMENT REQUIRED — Gap: ₹{gap:,}"
        )
    else:
        financial_line = f"Estimated ₹{estimated:,} | Wallet sufficient ✓"

    # Docs line
    required = len(output_dict.get("preauth_docs_required", []))
    missing = len(output_dict.get("preauth_docs_missing", []))
    docs_line = f"Docs: {required} required"
    if missing:
        docs_line += f", {missing} MISSING 🔴"
    else:
        docs_line += ", all in hand ✓"

    # Key warning flags (exclude info flags, exclude noisy always-present ones)
    SKIP_FLAGS = {
        "EMERGENCY_PHASE_STUBBED",
        "CANDIDATES_GENERATED",
        "FINANCIAL_ESTIMATE_APPROXIMATE",
        "DEDUCTION_APPROXIMATE",
        "DOC_GAP_ANALYSIS",
    }
    warn_flags = [
        f.get("code", "")
        for f in output_dict.get("flags", [])
        if f.get("severity") in ("warning", "block")
        and f.get("code") not in SKIP_FLAGS
    ]
    flags_line = f"Flags: {', '.join(warn_flags)}" if warn_flags else "Flags: none"

    # Print
    print(f"\n{DIVIDER}")
    print(f"  IRIS RESULT  {icon}  {status}")
    print(f"  {packages_line}")
    print(f"  {financial_line}")
    print(f"  {docs_line}  |  {flags_line}")
    print(f"{DIVIDER}\n")


def main() -> None:
    """CLI entry point for the IRIS pipeline.

    Reads input JSON from a file path argument or from stdin, validates it,
    builds a session, runs the full pipeline, and writes the serialised
    IRISOutput as formatted JSON to stdout.

    Usage:
        python main.py input.json          # read from file
        python main.py < input.json        # read from stdin

    Exit codes:
        0 — pipeline completed (even if readiness_status is BLOCKED)
        1 — input validation failed (output is {"error": ..., "details": [...]})

    Side effects:
        Writes JSON to stdout.
        Writes logs to stdout (via setup_logging / StreamHandler).
    """
    setup_logging()

    # --- Read input ---
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1])
        raw_json = json.loads(input_path.read_text(encoding="utf-8"))
        logger.info("Input loaded from %s", input_path)
    else:
        raw_json = json.load(sys.stdin)
        logger.info("Input loaded from stdin")

    # --- Validate ---
    valid, errors = validate_input(raw_json)
    if not valid:
        print(json.dumps({"error": "Invalid input", "details": errors}, indent=2))
        sys.exit(1)

    # --- Build session ---
    session = build_session(raw_json)
    logger.info(
        "Session created: patient_id=%s",
        raw_json.get("patient", {}).get("patient_id", "?"),
    )

    # --- Run pipeline ---
    output = run_pipeline(session)

    # --- Serialise and print ---
    output_dict = serialize_output(output)
    print(json.dumps(output_dict, indent=2, default=str))
    print_summary(output_dict)

    logger.info("Pipeline complete. Status: %s", output.readiness_status)


if __name__ == "__main__":
    main()

