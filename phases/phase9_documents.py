"""
phases/phase9_documents.py — IRIS Pipeline Phase 9
====================================================
Pre-auth document gap analysis.

Reads  : session.final_package_set, session.clinical, session.hospital,
         session.mlc_required, session.flags
Writes : session.preauth_docs_required, session.preauth_docs_missing
Flags  : DOC_GAP_ANALYSIS, MANDATORY_DOCS_MISSING

Critical Rules applied (SYSTEM_DESIGN.md #19-21):
  #19  Public hospital document relaxation (CAM Annexure 7): at pre-auth,
       public hospitals only need clinical_notes. Private hospitals need the
       full KB-2 mandatory_documents.preauth list.
  #20  Universal docs for private: clinical_notes (hard_block) + patient_photo
       (hard_block).
  #21  Conditional: MLC case → mlc_fir + self_declaration (both hard_block).
       Transplant → NOTTO IDs (hard_block). Oncology → tumour_board_approval
       (hard_block).
"""

from __future__ import annotations

import logging

from kb.loader import load_specialty_shard, get_procedure_from_shard
from models import DocumentItem
from phases.phase3_validator import SPECIALTY_CODE_TO_SHARD
from session import IRISSession
from llm.query_predictor import predict_package_queries

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_phase9(session: IRISSession) -> IRISSession:
    """Phase 9 — build required document list and compute gap.

    Steps:
    1. Collect available doc keys from clinical input (investigations + non-clinical docs).
    2. Build required list = universal + conditional + per-package (KB-2).
    3. Deduplicate required by (key, package_code) to prevent repeats.
    4. Mark each doc available or missing.
    5. Write session.preauth_docs_required and session.preauth_docs_missing.
    6. Emit DOC_GAP_ANALYSIS and, if any hard_block docs are missing,
       MANDATORY_DOCS_MISSING.

    Args:
        session: IRISSession with final_package_set, clinical, hospital, and
                 flag state populated by Phases 0-8.

    Returns:
        session with preauth_docs_required and preauth_docs_missing populated.

    Side effects:
        Appends flags to session.flags. Logs warnings for any shard load failures
        (which are non-blocking — KB gaps do not stop Phase 9).
    """
    logger.info("Phase 9 — document gap analysis: start")

    # Step 1: collect available doc keys
    available_keys: set[str] = _get_available_doc_keys(session.clinical)
    logger.debug("Phase 9 — available doc keys: %s", available_keys)

    # Step 2: build required list
    required: list[DocumentItem] = (
        _get_universal_required(session.hospital)
        + _get_conditional_required(session)
        + _get_package_docs(session)
    )

    # Step 3: deduplicate by (key, package_code) — keep first occurrence
    seen: set[tuple[str, str | None]] = set()
    deduped: list[DocumentItem] = []
    for doc in required:
        dedup_key = (doc.key, doc.package_code)
        if dedup_key not in seen:
            seen.add(dedup_key)
            deduped.append(doc)
    required = deduped

    # Step 4: mark availability
    for doc in required:
        doc.available = doc.key in available_keys

    # Step 5: write to session
    session.preauth_docs_required = required
    session.preauth_docs_missing = [d for d in required if not d.available]

    # Step 6: flags
    session.add_flag(
        "DOC_GAP_ANALYSIS",
        f"Required: {len(required)} docs. Missing: {len(session.preauth_docs_missing)}.",
        "info",
    )

    hard_block_missing = sum(
        1 for d in session.preauth_docs_missing if d.criticality == "hard_block"
    )
    if hard_block_missing > 0:
        session.add_flag(
            "MANDATORY_DOCS_MISSING",
            (
                f"{hard_block_missing} mandatory document(s) missing — "
                "cannot submit pre-auth until resolved."
            ),
            "warning",
        )

    # Step 7 — Query prediction per package
    logger.info("Phase 9 — query prediction: start")
    if not hasattr(session, "query_predictions"):
        session.query_predictions = []
    available_keys_for_prediction = _get_available_doc_keys(session.clinical)
    for fp in session.final_package_set:
        try:
            prediction = predict_package_queries(session, fp, available_keys_for_prediction)
            session.query_predictions.append(prediction)
            logger.info(
                "Phase 9 — query prediction complete for %s: verdict=%s",
                fp.validated.procedure_code,
                prediction.readiness_verdict,
            )
        except Exception as exc:
            logger.error(
                "Phase 9 — query prediction failed for %s: %s",
                fp.validated.procedure_code,
                exc,
            )
    logger.info("Phase 9 — query prediction: complete (%d predictions)", len(session.query_predictions))

    logger.info(
        "Phase 9 — complete | required=%d, missing=%d (hard_block=%d)",
        len(required),
        len(session.preauth_docs_missing),
        hard_block_missing,
    )
    return session


# ---------------------------------------------------------------------------
# Helper: available doc keys from clinical input
# ---------------------------------------------------------------------------

def _get_available_doc_keys(clinical) -> set[str]:
    """Build set of available canonical document keys from the clinical input.

    Sources:
      - clinical.investigations where document_available == True → adds the
        investigation's canonical type string (e.g. "ecg", "blood_reports").
      - clinical.non_clinical_documents_in_hand where available == True → adds
        the document's key string (e.g. "clinical_notes", "patient_photo").

    Args:
        clinical: ClinicalInput from the current session.

    Returns:
        Set of canonical key strings for all documents in hand / with reports.

    Side effects:
        None.
    """
    KEY_MAP = {
    "usg": "usg_report",
    "ct": "ct_report",
    "mri": "mri_report",
    "xray": "xray",
    "ecg": "ecg",
    "echo": "echo",
    "blood_reports": "blood_reports",
    "biopsy_hpe": "biopsy_hpe",
    }
    keys: set[str] = set()

    for inv in clinical.investigations:
        if inv.document_available:
            keys.add(inv.type)
            keys.add(KEY_MAP.get(inv.type, inv.type)) 

    for doc in clinical.non_clinical_documents_in_hand:
        if doc.available:
            keys.add(doc.key)

    return keys


# ---------------------------------------------------------------------------
# Helper: universal required docs
# ---------------------------------------------------------------------------

def _get_universal_required(hospital) -> list[DocumentItem]:
    """Return the universal document baseline for every pre-auth.

    Public hospital (CAM Annexure 7 relaxation — Critical Rule #19):
        Only clinical_notes is required. patient_photo is waived.

    Private hospital (Critical Rule #20):
        clinical_notes  (hard_block)
        patient_photo   (hard_block)

    Args:
        hospital: HospitalContext from the current session.

    Returns:
        List of DocumentItem objects (available=False; set later in run_phase9).

    Side effects:
        None.
    """
    clinical_notes = DocumentItem(
        key="clinical_notes",
        label="Admission / clinical notes",
        package_code=None,
        available=False,
        criticality="hard_block",
    )

    if hospital.type == "public":
        # Relaxation: only clinical_notes needed
        return [clinical_notes]

    # Private: clinical_notes + patient_photo
    patient_photo = DocumentItem(
        key="patient_photo",
        label="Photo of patient on hospital bed",
        package_code=None,
        available=False,
        criticality="hard_block",
    )
    return [clinical_notes, patient_photo]


# ---------------------------------------------------------------------------
# Helper: conditional required docs
# ---------------------------------------------------------------------------

def _get_conditional_required(session: IRISSession) -> list[DocumentItem]:
    """Return conditionally required documents based on session state and flags.

    Conditions (Critical Rule #21):
      - session.mlc_required → mlc_fir (hard_block) + self_declaration (hard_block)
      - Flag NOTTO_DOCS_REQUIRED present → notto_recipient_id + notto_donor_id
        (both hard_block)
      - Flag MTB_REQUIRED present → tumour_board_approval (hard_block)

    All returned items have package_code=None (they are case-level requirements,
    not tied to a specific package).

    Args:
        session: IRISSession with mlc_required and flags populated.

    Returns:
        List of DocumentItem objects (available=False; set later in run_phase9).

    Side effects:
        None.
    """
    docs: list[DocumentItem] = []

    # MLC case — Critical Rule #21
    if session.mlc_required:
        docs.append(DocumentItem(
            key="mlc_fir",
            label="MLC / FIR copy",
            package_code=None,
            available=False,
            criticality="hard_block",
        ))
        docs.append(DocumentItem(
            key="self_declaration",
            label="Self-declaration from patient / attender",
            package_code=None,
            available=False,
            criticality="hard_block",
        ))

    # Flag-driven conditions — resolve flag codes from session.flags
    flag_codes: set[str] = {f.code for f in session.flags}

    # Transplant — Critical Rule #21
    if "NOTTO_DOCS_REQUIRED" in flag_codes:
        docs.append(DocumentItem(
            key="notto_recipient_id",
            label="NOTTO Recipient ID",
            package_code=None,
            available=False,
            criticality="hard_block",
        ))
        docs.append(DocumentItem(
            key="notto_donor_id",
            label="NOTTO Donor ID",
            package_code=None,
            available=False,
            criticality="hard_block",
        ))

    # Oncology — Critical Rule #21
    if "MTB_REQUIRED" in flag_codes:
        docs.append(DocumentItem(
            key="tumour_board_approval",
            label="Multidisciplinary Tumour Board (MTB) approval note",
            package_code=None,
            available=False,
            criticality="hard_block",
        ))

    # USP pathway — additional documents required for unspecified surgical package pre-auth
    if session.usp_recommended:
        docs.append(DocumentItem(
            key="case_summary",
            label="Detailed case summary with diagnosis and treatment plan",
            package_code=None,
            available=False,
            criticality="hard_block",
        ))
        docs.append(DocumentItem(
            key="specialist_opinion",
            label="Specialist opinion letter justifying the procedure",
            package_code=None,
            available=False,
            criticality="hard_block",
        ))
        docs.append(DocumentItem(
            key="cost_estimate",
            label="Estimated cost of treatment from treating hospital",
            package_code=None,
            available=False,
            criticality="hard_block",
        ))

    return docs


# ---------------------------------------------------------------------------
# Helper: per-package docs from KB-2
# ---------------------------------------------------------------------------

def _get_package_docs(session: IRISSession) -> list[DocumentItem]:
    """Return per-package required documents from KB-2 mandatory_documents.preauth.

    Public hospital: returns [] immediately (Annexure 7 relaxation — only
    clinical_notes is needed and it is already added by _get_universal_required).

    Private hospital: for each package in session.final_package_set, loads the
    full shard, finds the procedure record, and reads mandatory_documents.preauth.
    Each document is returned with criticality="ppd_query_risk" (not hard_block —
    these are KB-2 procedure docs that PPD may query on, but won't outright block
    unless the universal docs are also missing).

    Shard/procedure lookup failures are caught and logged as WARNING only — a
    missing shard is an expected KB gap in Phase 1, not a pipeline error.

    Args:
        session: IRISSession with final_package_set and hospital populated.

    Returns:
        List of DocumentItem objects (available=False; set later in run_phase9).

    Side effects:
        Appends to session.errors is intentionally NOT done here — shard gaps are
        KB build gaps, not pipeline errors. They are logged at WARNING level only.
    """
    if session.hospital.type == "public":
        logger.debug(
            "Phase 9 — public hospital: skipping per-package KB-2 doc lookup (Annexure 7 relaxation)."
        )
        return []

    docs: list[DocumentItem] = []

    for fp in session.final_package_set:
        pkg = fp.validated
        try:
            shard_filename = SPECIALTY_CODE_TO_SHARD.get(pkg.specialty_code)
            if not shard_filename:
                logger.warning(
                    "Phase 9 — no shard mapping for specialty_code '%s' (%s); skipping.",
                    pkg.specialty_code,
                    pkg.procedure_code,
                )
                continue

            shard = load_specialty_shard(shard_filename)
            procedure = get_procedure_from_shard(pkg.procedure_code, shard)
            if procedure is None:
                logger.warning(
                    "Phase 9 — procedure %s not found in shard '%s'; skipping.",
                    pkg.procedure_code,
                    shard_filename,
                )
                continue

            preauth_docs: list[dict] = (
                procedure.get("mandatory_documents", {}).get("preauth", []) or []
            )
            for doc in preauth_docs:
                key = doc.get("key")
                label = doc.get("label", key)
                if not key:
                    continue
                docs.append(DocumentItem(
                    key=key,
                    label=label,
                    package_code=pkg.package_code,
                    available=False,
                    criticality="ppd_query_risk",
                ))

        except FileNotFoundError:
            logger.warning(
                "Phase 9 — shard file not found for specialty '%s' (%s); skipping.",
                pkg.specialty_code,
                pkg.procedure_code,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Phase 9 — unexpected error loading docs for %s: %s",
                pkg.procedure_code,
                exc,
            )

    return docs
