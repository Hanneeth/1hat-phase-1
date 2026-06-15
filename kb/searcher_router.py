"""
kb/searcher_router.py — Phase 2 search backend router.

Reads PHASE2_SEARCH_MODE from config and delegates search_candidates to
either the fuzzy backend (kb/searcher.py) or the LLM backend
(kb/searcher_llm.py). Both backends implement the same public API so this
module is transparent to the caller.

To switch backends: change PHASE2_SEARCH_MODE in config.py.
  "fuzzy" → rapidfuzz token_set_ratio (deterministic, zero LLM cost)
  "llm"   → Gemini reads full index + clinical input (better clinical
             reasoning, one LLM call per Phase 2 run)
"""

import logging
from config import PHASE2_SEARCH_MODE
from models import CandidatePackage, ClinicalInput

logger = logging.getLogger(__name__)


def search_candidates(
    clinical: ClinicalInput,
    empanelled_specialties: list[str],
    hospital_is_public: bool,
) -> list[CandidatePackage]:
    """Route Phase 2 search to the configured backend.

    Args:
        clinical:                ClinicalInput from session.clinical.
        empanelled_specialties:  List of specialty codes the hospital is 
                                 empanelled for.
        hospital_is_public:      True if hospital.type == "public".

    Returns:
        list[CandidatePackage] from whichever backend is active.
    """
    if PHASE2_SEARCH_MODE == "llm":
        logger.info("Phase 2 router — using LLM backend (PHASE2_SEARCH_MODE=llm)")
        from kb.searcher_llm import search_candidates as _search
    elif PHASE2_SEARCH_MODE == "fuzzy":
        logger.info("Phase 2 router — using fuzzy backend (PHASE2_SEARCH_MODE=fuzzy)")
        from kb.searcher import search_candidates as _search
    else:
        logger.warning(
            "Phase 2 router — unknown PHASE2_SEARCH_MODE '%s', falling back to fuzzy",
            PHASE2_SEARCH_MODE,
        )
        from kb.searcher import search_candidates as _search

    return _search(clinical, empanelled_specialties, hospital_is_public)
