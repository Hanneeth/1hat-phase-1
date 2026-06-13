"""
kb/searcher.py — IRIS Phase 2: Fuzzy candidate search over _index.json.

Produces a ranked, deduplicated list of CandidatePackage objects from the
KB-2 thin index using rapidfuzz token-set-ratio scoring.

Public API:
    search_candidates(clinical, empanelled_specialties, hospital_is_public)
        → list[CandidatePackage]

Private helper:
    _build_search_string(clinical) → str
"""

import logging

from rapidfuzz import fuzz

from config import MIN_FUZZY_SCORE, TOP_N_CANDIDATES
from kb.loader import load_index
from models import CandidatePackage, ClinicalInput

logger = logging.getLogger(__name__)


def search_candidates(
    clinical: ClinicalInput,
    empanelled_specialties: list[str],
    hospital_is_public: bool,
) -> list[CandidatePackage]:
    """Fuzzy search _index.json to generate candidate packages for Phase 3.

    Builds a free-text search string from the clinical input, scores every
    index row against that string using rapidfuzz token_set_ratio, and returns
    a ranked, deduplicated shortlist of CandidatePackage objects.

    Steps:
        1.  Build search string via _build_search_string(clinical).
        2.  Load the full index via load_index().
        3.  Pre-filter rows:
              - Keep only rows where specialty_code is in empanelled_specialties.
              - If hospital_is_public is False, exclude rows where
                reserved_public_only is True.
        4.  Score each surviving row:
              - Score against every alias in row["aliases"].
              - Score against row["package_name"].
              - Score against row["procedure_name"].
              - match_score = max of all scores.
        5.  Keep only rows where match_score >= MIN_FUZZY_SCORE.
        6.  Sort descending by match_score.
        7.  Take top TOP_N_CANDIDATES rows.
        8.  Deduplicate by package_code: if multiple rows share the same
            package_code, keep only the one with the highest match_score.
        9.  Convert each surviving row to CandidatePackage.
        10. Return the final list.

    Args:
        clinical: Parsed ClinicalInput from session.clinical.
        empanelled_specialties: List of 2-letter specialty codes the hospital
            is empanelled for (from HospitalContext.empanelled_specialties).
        hospital_is_public: True if hospital.type == "public". Controls whether
            reserved_public_only procedures are included.

    Returns:
        Ranked list of CandidatePackage objects, best match first.
        Empty list if no candidates survive all filters.

    Side effects:
        Logs INFO counts after specialty and reserved_public_only pre-filters,
        after score filter, and after deduplication.
        Logs WARNING if final list is empty.
    """
    # Step 1: Build search string
    search_string = _build_search_string(clinical)
    logger.info("Phase 2 search string: %r", search_string)

    # Step 2: Load index (cached after first call)
    index: list[dict] = load_index()
    logger.info("Phase 2: index loaded — %d total rows", len(index))

    # Step 3: Pre-filter by specialty empanelment and reserved_public_only
    filtered: list[dict] = []
    for row in index:
        if row.get("specialty_code") not in empanelled_specialties:
            continue
        if not hospital_is_public and row.get("reserved_public_only", False):
            continue
        filtered.append(row)

    logger.info(
        "Phase 2: %d rows after specialty + reserved_public_only pre-filter "
        "(empanelled=%d specialties, public=%s)",
        len(filtered),
        len(empanelled_specialties),
        hospital_is_public,
    )

    # Step 4–5: Score and filter by MIN_FUZZY_SCORE
    scored: list[tuple[float, dict]] = []
    for row in filtered:
        targets: list[str] = []
        targets.extend(row.get("aliases") or [])
        if row.get("package_name"):
            targets.append(row["package_name"])
        if row.get("procedure_name"):
            targets.append(row["procedure_name"])

        match_score: float = max(
            (fuzz.token_set_ratio(search_string, t) for t in targets),
            default=0.0,
        )
        if match_score >= MIN_FUZZY_SCORE:
            scored.append((match_score, row))

    logger.info(
        "Phase 2: %d rows after score filter (MIN_FUZZY_SCORE=%d)",
        len(scored),
        MIN_FUZZY_SCORE,
    )

    # Step 6–7: Sort descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    top: list[tuple[float, dict]] = scored[:TOP_N_CANDIDATES]

    # Step 8: Deduplicate by package_code — keep highest match_score per package
    seen_packages: dict[str, tuple[float, dict]] = {}
    for score, row in top:
        pkg_code: str = row["package_code"]
        if pkg_code not in seen_packages or score > seen_packages[pkg_code][0]:
            seen_packages[pkg_code] = (score, row)

    deduped: list[tuple[float, dict]] = sorted(
        seen_packages.values(), key=lambda x: x[0], reverse=True
    )
    logger.info(
        "Phase 2: %d candidates after package_code deduplication (was %d pre-dedup)",
        len(deduped),
        len(top),
    )

    # Step 9: Convert to CandidatePackage
    candidates: list[CandidatePackage] = []
    for score, row in deduped:
        candidates.append(
            CandidatePackage(
                procedure_code=row["procedure_code"],
                package_code=row["package_code"],
                specialty_code=row["specialty_code"],
                specialty=row["specialty"],
                package_name=row["package_name"],
                procedure_name=row["procedure_name"],
                billing_unit=row["billing_unit"],
                reserved_public_only=row["reserved_public_only"],
                procedure_label=row["procedure_label"],
                auto_approved=row["auto_approved"],
                day_care=row["day_care"],
                base_rate_inr=row.get("base_rate_inr"),  # can be null
                match_score=score,
            )
        )

    # Step 10: Return — warn if empty
    if not candidates:
        logger.warning(
            "Phase 2: no candidates found for search string %r — "
            "Phase 3 will receive empty list, USP path will be triggered",
            search_string,
        )

    logger.info("Phase 2: returning %d candidate(s)", len(candidates))
    return candidates


def _build_search_string(clinical: ClinicalInput) -> str:
    """Build a free-text search string from clinical input fields.

    STUB for MVP — concatenates available text fields with spaces.
    Later will be replaced by LLM entity extraction (Phase 2 entity
    extraction deferred per SYSTEM_DESIGN.md LLM Usage Policy).

    Concatenates (space-separated, skipping None values):
        - clinical.chief_complaints
        - clinical.provisional_diagnosis
        - clinical.planned_procedure   (if not None)
        - clinical.history_of_present_illness (if not None)

    Args:
        clinical: Parsed ClinicalInput from session.clinical.

    Returns:
        A single space-separated string of all available clinical text.
        Never returns None — returns empty string if all fields are None.

    Side effects:
        None.
    """
    parts: list[str] = []

    if clinical.chief_complaints:
        parts.append(clinical.chief_complaints)
    if clinical.provisional_diagnosis:
        parts.append(clinical.provisional_diagnosis)
    if clinical.planned_procedure:
        parts.append(clinical.planned_procedure)
    if clinical.history_of_present_illness:
        parts.append(clinical.history_of_present_illness)

    return " ".join(parts)
