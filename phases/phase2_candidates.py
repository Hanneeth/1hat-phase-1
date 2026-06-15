"""Phase 2 — Candidate package generation via fuzzy search."""

import logging
from session import IRISSession
from kb.searcher_router import search_candidates

logger = logging.getLogger(__name__)


def run_phase2(session: IRISSession) -> IRISSession:
    """Phase 2 — Candidate package generation via fuzzy search.

    Steps:
    1. If session.has_block_flag() → return session immediately (defensive)
    2. Call:
       candidates = search_candidates(
           clinical=session.clinical,
           empanelled_specialties=session.hospital.empanelled_specialties,
           hospital_is_public=(session.hospital.type == "public")
       )
    3. session.candidate_packages = candidates
    4. add_flag("CANDIDATES_GENERATED",
                f"Generated {len(candidates)} candidates from index",
                "info")
    5. If len(candidates) == 0:
       add_flag("NO_CANDIDATES_FOUND",
                "Fuzzy search returned zero candidates. Check clinical input or consider USP pathway.",
                "warning")
    6. Return session

    Wrap in try/except: on exception, append to session.errors,
    add_flag("CANDIDATE_GENERATION_FAILED", str(e), "block"), return session.
    """
    logger.info("Phase 2 — Candidate generation: start")

    # 1. If session.has_block_flag() → return session immediately
    if session.has_block_flag():
        logger.info("Phase 2 skipped: session has block flag")
        return session

    try:
        # 2. Call search_candidates
        candidates = search_candidates(
            clinical=session.clinical,
            empanelled_specialties=session.hospital.empanelled_specialties,
            hospital_is_public=(session.hospital.type == "public"),
        )

        # 3. session.candidate_packages = candidates
        session.candidate_packages = candidates

        # 4. add_flag
        session.add_flag(
            "CANDIDATES_GENERATED",
            f"Generated {len(candidates)} candidates from index",
            "info",
        )

        # 5. If len(candidates) == 0
        if len(candidates) == 0:
            session.add_flag(
                "NO_CANDIDATES_FOUND",
                "Fuzzy search returned zero candidates. Check clinical input or consider USP pathway.",
                "warning",
            )

    except Exception as e:
        session.errors.append(f"Phase 2 error: {e}")
        session.add_flag("CANDIDATE_GENERATION_FAILED", str(e), "block")
        return session

    # 6. Return session
    return session
