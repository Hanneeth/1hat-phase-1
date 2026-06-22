"""
phases/phase10_output.py — IRIS Pipeline Phase 10
===================================================
Output assembly. Pure read — does not modify session.

Reads  : all session fields accumulated by Phases 0-9
Writes : nothing — returns IRISOutput
Exports: run_phase10, serialize_output

Readiness status logic (SYSTEM_DESIGN.md "Output Schema" — first match wins):
  1. Any flag severity=="block"                    → BLOCKED
  2. Any missing doc criticality=="hard_block"     → BLOCKED
  3. final_package_set empty (not usp path)        → BLOCKED
  4. Any missing doc criticality=="ppd_query_risk" → CONDITIONAL
  5. Any flag severity=="warning"                  → READY_WITH_WARNINGS
  6. Otherwise                                     → READY
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from config import (
    ENHANCEMENT_BATCH_PRIVATE,
    ENHANCEMENT_BATCH_PUBLIC,
    NE_STATES_AND_ISLANDS,
)
from models import EnhancementPlan, IRISOutput
from session import IRISSession

logger = logging.getLogger(__name__)

# Standard enhancement caveat — non-negotiable per spec
_ENHANCEMENT_CAVEAT = (
    "Estimated based on indicative LoS — actual stay may vary. "
    "File additional enhancement requests as needed."
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_phase10(session: IRISSession) -> IRISOutput:
    """Phase 10 — assemble IRISOutput from session state. Pure read.

    Collects all fields written by Phases 0-9 and produces the final structured
    output object. Does NOT mutate session.

    Args:
        session: Fully-executed IRISSession after all preceding phases.

    Returns:
        IRISOutput with all fields populated.

    Side effects:
        None — this phase is read-only. It does not append flags, errors, or any
        other session state. All decisions are encoded in the status determination.
    """
    logger.info("Phase 10 — output assembly: start")

    status = _determine_status(session)
    enhancement_plan = _build_enhancement_plan(session)

    output = IRISOutput(
        readiness_status=status,
        selected_packages=session.final_package_set,
        blocked_candidates=session.phase3_blocked,
        preauth_docs_required=session.preauth_docs_required,
        preauth_docs_missing=session.preauth_docs_missing,
        query_predictions=session.query_predictions,
        enhancement_plan=enhancement_plan,
        copayment_required=session.copayment_required,
        copayment_gap_inr=session.copayment_gap_inr,
        comorbidity_notes=session.comorbidity_notes,
        flags=session.flags,
        stg_coverage=session.stg_coverage,
        errors=session.errors,
    )

    logger.info(
        "Phase 10 — complete | status=%s, selected=%d, blocked=%d, "
        "docs_required=%d, docs_missing=%d, predictions=%d, enhancement_plans=%d, errors=%d",
        status,
        len(output.selected_packages),
        len(output.blocked_candidates),
        len(output.preauth_docs_required),
        len(output.preauth_docs_missing),
        len(output.query_predictions),
        len(enhancement_plan),
        len(output.errors),
    )
    return output


# ---------------------------------------------------------------------------
# Status determination
# ---------------------------------------------------------------------------

def _determine_status(session: IRISSession) -> str:
    """Determine the pre-auth readiness status. First match wins.

    Precedence order (from SYSTEM_DESIGN.md "Output Schema"):
      1. Any flag with severity=="block"                    → "BLOCKED"
      2. Any missing DocumentItem with criticality=="hard_block" → "BLOCKED"
      3. session.final_package_set is empty AND not usp_recommended → "BLOCKED"
      4. Any missing DocumentItem with criticality=="ppd_query_risk" → "CONDITIONAL"
      5. Any flag with severity=="warning"                  → "READY_WITH_WARNINGS"
      6. Otherwise                                         → "READY"

    Note on rule 3: when session.usp_recommended=True the final_package_set is
    intentionally empty (USP referral path). We do not BLOCK that case here —
    the USP flag itself carries the message. Status resolves to READY_WITH_WARNINGS
    via rule 5 (USP_RECOMMENDED is a warning flag).

    Args:
        session: IRISSession to inspect.

    Returns:
        String literal: "BLOCKED" | "CONDITIONAL" | "READY_WITH_WARNINGS" | "READY"

    Side effects:
        None — pure read.
    """
    # Rule 1: any block flag
    if any(f.severity == "block" for f in session.flags):
        logger.debug("Phase 10 status → BLOCKED (block flag present)")
        return "BLOCKED"

    # Rule 2: any hard_block document missing
    if any(
        d.criticality == "hard_block"
        for d in session.preauth_docs_missing
    ):
        logger.debug("Phase 10 status → BLOCKED (hard_block doc missing)")
        return "BLOCKED"

    # Rule 3: empty package set (not USP path)
    if not session.final_package_set and not session.usp_recommended:
        logger.debug("Phase 10 status → BLOCKED (final_package_set empty, not USP)")
        return "BLOCKED"

    # Rule 4: any ppd_query_risk document missing
    if any(
        d.criticality == "ppd_query_risk"
        for d in session.preauth_docs_missing
    ):
        logger.debug("Phase 10 status → CONDITIONAL (ppd_query_risk doc missing)")
        return "CONDITIONAL"

    # Rule 5: any warning flag
    if any(f.severity == "warning" for f in session.flags):
        logger.debug("Phase 10 status → READY_WITH_WARNINGS (warning flag present)")
        return "READY_WITH_WARNINGS"

    # Rule 6: default
    logger.debug("Phase 10 status → READY")
    return "READY"


# ---------------------------------------------------------------------------
# Enhancement plan builder
# ---------------------------------------------------------------------------

def _build_enhancement_plan(session: IRISSession) -> list[EnhancementPlan]:
    """Build enhancement request plan for packages that need it.

    Iterates session.final_package_set and creates an EnhancementPlan for every
    package where enhancement_requests_needed is not None and > 0.

    Batch size is determined by hospital type and NE/island state:
      - public hospital OR hospital in NE_STATES_AND_ISLANDS → ENHANCEMENT_BATCH_PUBLIC
      - private hospital (non-NE)                           → ENHANCEMENT_BATCH_PRIVATE

    Note on los_indicative_days: ValidatedPackage does not store the raw
    los_indicative integer (it was used during Phase 3 to compute
    enhancement_requests_needed but not persisted). los_indicative_days is set
    to 0 as a placeholder. The estimated_requests count is already correct —
    only the raw LoS display value is missing. This field should be added to
    ValidatedPackage in a future schema revision.

    Args:
        session: IRISSession with final_package_set and hospital populated.

    Returns:
        List of EnhancementPlan objects (may be empty).

    Side effects:
        None.
    """
    if not session.hospital:
        logger.warning(
            "Phase 10 — hospital context missing; cannot build enhancement plan."
        )
        return []

    is_ne = session.hospital.state in NE_STATES_AND_ISLANDS
    batch = (
        ENHANCEMENT_BATCH_PUBLIC
        if (session.hospital.type == "public" or is_ne)
        else ENHANCEMENT_BATCH_PRIVATE
    )

    plans: list[EnhancementPlan] = []
    for fp in session.final_package_set:
        pkg = fp.validated
        requests_needed = pkg.enhancement_requests_needed

        if requests_needed is None or requests_needed <= 0:
            continue

        plans.append(EnhancementPlan(
            procedure_code=pkg.procedure_code,
            estimated_requests=requests_needed,
            batch_size_used=batch,
            los_indicative_days=0,   # TODO: persist los_indicative on ValidatedPackage
            caveat=_ENHANCEMENT_CAVEAT,
        ))
        logger.debug(
            "Phase 10 — enhancement plan: %s → %d requests (batch=%d)",
            pkg.procedure_code,
            requests_needed,
            batch,
        )

    return plans


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def serialize_output(output: IRISOutput) -> dict:
    """Convert IRISOutput to a JSON-serialisable dict.

    Uses dataclasses.asdict() to recursively convert all nested dataclasses.
    Any non-serialisable types (datetime, Path, etc.) are coerced to str via a
    custom default applied post-conversion.

    Args:
        output: IRISOutput as returned by run_phase10.

    Returns:
        Plain dict safe for json.dumps().

    Side effects:
        None.
    """
    raw: dict = asdict(output)
    return _coerce_serialisable(raw)


def _coerce_serialisable(obj):
    """Recursively coerce any non-JSON-serialisable leaf values to str.

    Handles: dict, list, str, int, float, bool, None — all pass through.
    Anything else (datetime, Path, Enum, etc.) is converted to str.

    Args:
        obj: Any Python object produced by dataclasses.asdict().

    Returns:
        A JSON-safe equivalent of obj.
    """
    if isinstance(obj, dict):
        return {k: _coerce_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_coerce_serialisable(item) for item in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # Fallback: coerce to string (datetime, Path, custom objects, etc.)
    return str(obj)
