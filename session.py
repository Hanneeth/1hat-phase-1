"""
IRISSession — the IRIS pipeline spine.

session.flags : list[Flag]
    Business outcomes the MEDCO needs to see. Severity: info | warning | block.
    Pipeline proceeds to Phase 10 immediately when any block flag is set.

session.errors : list[str]
    Technical failures for the developer. Plain strings. Pipeline NEVER stops on errors.

session.stg_coverage : dict
    {"validated": n, "stg_missing": n} — incremented by Phase 3, read by Phase 10.

session.usp_recommended : bool
    Set True by main.py when Phase 3 returns zero validated packages.
    Causes main.py to skip Phases 4-8 and jump to Phase 9.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from models import (
    CandidatePackage,
    ClinicalInput,
    DocumentItem,
    FinalPackage,
    Flag,
    HospitalContext,
    PatientContext,
    ValidatedPackage,
)

logger = logging.getLogger(__name__)


@dataclass
class IRISSession:
    """
    Central state object for a single IRIS pre-auth evaluation run.

    Created once in main.py before Phase 0 and passed through the full
    deterministic pipeline (Phases 0–10). Every phase reads from and writes
    back to this object. Phase 10 reads final state to assemble IRISOutput
    and does not write back.

    Fields are grouped and annotated by the phase that owns them. No phase
    should write to another phase's fields.

    Inputs:
        input_data : raw input JSON dict as received from the caller.
        clinical   : parsed ClinicalInput block derived from input_data["clinical"].

    Side effects at construction:
        None — all mutation happens inside phase functions.
    """

    # ------------------------------------------------------------------
    # Set at session creation — required, no default
    # ------------------------------------------------------------------
    input_data: dict           # raw input JSON, preserved for audit / debug
    clinical: ClinicalInput    # parsed clinical block from input_data["clinical"]

    # ------------------------------------------------------------------
    # Populated by Phase 0 — patient + hospital preflight
    # ------------------------------------------------------------------
    patient: PatientContext | None = None
    hospital: HospitalContext | None = None
    patient_eligible: bool = False
    hospital_empanelled: bool = False
    mlc_required: bool = False

    # ------------------------------------------------------------------
    # Populated by Phase 1 — emergency routing (stubbed: always non-emergency)
    # ------------------------------------------------------------------
    is_emergency: bool = False
    er_package_code: str | None = None
    needs_specialty_package: bool = True

    # ------------------------------------------------------------------
    # Populated by Phase 2 — fuzzy candidate generation from _index.json
    # ------------------------------------------------------------------
    candidate_packages: list[CandidatePackage] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Populated by Phase 3 — per-package validation (rules + LLM STG check)
    # ------------------------------------------------------------------
    validated_packages: list[ValidatedPackage] = field(default_factory=list)
    # Each entry: {"procedure_code": str, "reason_code": str, "message": str}
    phase3_blocked: list[dict] = field(default_factory=list)
    # Incremented during Phase 3 loop; read by Phase 10 for output assembly
    stg_coverage: dict = field(
        default_factory=lambda: {"validated": 0, "stg_missing": 0}
    )

    # ------------------------------------------------------------------
    # Set by main.py after Phase 3 result is known
    # True  → skip Phases 4-8, jump directly to Phase 9 then 10
    # ------------------------------------------------------------------
    usp_recommended: bool = False

    # ------------------------------------------------------------------
    # Populated by Phase 4 — multi-package combination rules
    # ------------------------------------------------------------------
    final_package_set: list[FinalPackage] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Populated by Phase 5 — wallet sufficiency check
    # ------------------------------------------------------------------
    wallet_sufficient: bool = True
    copayment_required: bool = False
    copayment_gap_inr: int | None = None   # integer INR; None if no copayment gap
    estimated_total_inr: int = 0           # sum of selected package rates, integer INR

    # ------------------------------------------------------------------
    # Populated by Phase 7 — comorbidity resolution
    # ------------------------------------------------------------------
    comorbidity_notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Populated by Phase 9 — document gap analysis
    # ------------------------------------------------------------------
    preauth_docs_required: list[DocumentItem] = field(default_factory=list)
    preauth_docs_missing: list[DocumentItem] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Accumulated by ALL phases throughout the pipeline
    # ------------------------------------------------------------------
    # Business outcomes the MEDCO sees. severity: "info" | "warning" | "block"
    flags: list[Flag] = field(default_factory=list)
    # Technical failures for the developer. Pipeline NEVER stops on errors.
    errors: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def has_block_flag(self) -> bool:
        """
        Return True if any flag in session.flags has severity == "block".

        Called by main.py after every phase. A True return causes main.py
        to skip all remaining phases and proceed directly to Phase 10.

        Returns:
            bool: True if a blocking flag exists, False otherwise.

        Side effects:
            None — read-only check.
        """
        return any(f.severity == "block" for f in self.flags)

    def add_flag(self, code: str, message: str, severity: str) -> None:
        """
        Construct a Flag, append it to session.flags, and log it.

        All phases must use this method instead of appending directly to
        self.flags, so that every flag emission is logged consistently.

        Args:
            code:     UPPER_SNAKE_CASE flag identifier, e.g. "PUB_RESERVED_BLOCK".
                      Must match a documented flag code from SYSTEM_DESIGN.md.
            message:  Human-readable description for the MEDCO reviewing the case.
            severity: String literal — one of "info" | "warning" | "block".
                      "block" causes main.py to exit the pipeline after the
                      current phase completes.

        Side effects:
            Appends one Flag to self.flags.
            Logs at WARNING level for severity "warning" or "block".
            Logs at INFO level for severity "info".
        """
        from models import Flag  # local import guards against circular import at init

        flag = Flag(code=code, message=message, severity=severity)
        self.flags.append(flag)

        if severity in ("warning", "block"):
            logger.warning("FLAG [%s] %s — %s", severity.upper(), code, message)
        else:
            logger.info("FLAG [INFO] %s — %s", code, message)
