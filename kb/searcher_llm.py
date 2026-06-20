"""
kb/searcher_llm.py — IRIS Phase 2: LLM-based candidate search over _index.json.

Uses Gemini to search the health benefit package catalog index for candidate
procedures matching the patient's clinical presentation.

Public API:
    search_candidates(clinical, empanelled_specialties, hospital_is_public)
        → list[CandidatePackage]
"""

import json
import logging
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from google import genai

from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES
from kb.loader import load_index
from models import CandidatePackage, ClinicalInput

load_dotenv()

logger = logging.getLogger(__name__)


def search_candidates(
    clinical: ClinicalInput,
    empanelled_specialties: list[str],
    hospital_is_public: bool,
) -> list[CandidatePackage]:
    """Gemini-based candidate package generation.

    Queries the Gemini LLM with the compact index and the patient clinical context
    to return clinically relevant procedure codes.

    Args:
        clinical: Parsed ClinicalInput from session.clinical.
        empanelled_specialties: List of 2-letter specialty codes the hospital is empanelled for.
        hospital_is_public: True if hospital.type == "public".

    Returns:
        Sorted list of CandidatePackage objects, or empty list if no candidates found or on failure.
    """
    logger.info("Phase 2 LLM: start candidate search")

    # STEP 1 — Load and pre-filter the index.
    index: list[dict] = load_index()
    filtered: list[dict] = []
    for row in index:
        if row.get("specialty_code") not in empanelled_specialties:
            continue
        if not hospital_is_public and row.get("reserved_public_only", False):
            continue
        filtered.append(row)

    logger.info(
        "Phase 2 LLM: %d rows after specialty + reserved_public_only pre-filter (empanelled=%d, public=%s)",
        len(filtered),
        len(empanelled_specialties),
        hospital_is_public,
    )

    # STEP 2 — Serialise the filtered index for the LLM.
    compact_fields = [
        "procedure_code",
        "package_code",
        "specialty_code",
        "package_name",
        "procedure_name",
        "aliases",
        "billing_unit",
        "reserved_public_only",
        "procedure_label",
        "day_care",
        "base_rate_inr",
    ]

    compact_index = []
    for row in filtered:
        compact_row = {}
        for field in compact_fields:
            if field in row:
                compact_row[field] = row[field]
        compact_index.append(compact_row)

    serialised_index = json.dumps(compact_index)

    # STEP 3 — Build the LLM prompt.
    SYSTEM_PROMPT = (
        "You are a clinical package selector for PM-JAY (India's national health "
        "insurance scheme). You are given a list of available PM-JAY procedure packages "
        "and a patient's clinical presentation. Your task is to identify all procedure "
        "codes that are clinically relevant for this patient.\n\n"
        "Rules:\n"
        "- Only return procedure codes that exist exactly in the provided index.\n"
        "- A procedure is relevant if the patient's diagnosis, planned procedure, or "
        "clinical presentation is consistent with what that procedure treats.\n"
        "- Do not return procedures from specialties unrelated to the patient's "
        "presenting condition.\n"
        "- Be specific and clinically precise, not inclusive. Your job is to identify "
        "what this doctor is planning to do for this patient — not every procedure "
        "that could theoretically relate to their diagnosis.\n"
        "- If planned_procedure is explicitly stated in the clinical input, treat it "
        "as the primary anchor. You must find the PM-JAY package(s) for that "
        "specific procedure and include all their variants. That is your first "
        "priority. Do not drift from it.\n"
        # "- When provisional_diagnosis explicitly names the clinical entity using "
        # "terminology that directly matches a package family name, that nomenclature "
        # "is the authoritative anchor for package family selection. Do not override "
        # "it based on mechanism of injury or contextual history. The mechanism "
        # "describes how the injury occurred — the diagnosis describes what the "
        # "clinical entity is. These can differ and the diagnosis takes precedence.\n"
        "- Only include additional procedures if the clinical input explicitly states "
        "or strongly implies they are planned or under active consideration for this "
        "admission. Do not speculate about what might be needed post-operatively or "
        "what could theoretically be indicated given one symptom.\n"
        "- Do not include add-on procedures, high-end drug packages (antibiotics, "
        "antifungals), or diagnostic add-ons unless the clinical input explicitly "
        "mentions them as planned.\n"
        "- Before including any code, ask yourself: which specific line in the "
        "clinical input directly justifies this? If you cannot point to one, exclude "
        "it.\n"
        "- Return only as many codes as the clinical input genuinely supports. There "
        "is no minimum. A clear elective surgical case may correctly return anywhere from 3 to 10 codes. This is not a hardcoded value."
        "Only complex multi-system admissions warrant 15+ codes.\n"
        "- When a clinical condition maps to a package that has multiple procedure "
        "variants under the same package code (different approaches, severity "
        "grades, sizes, or anatomical variants), include ALL procedure codes "
        "under that package. "
        
        "- The provided index is your only source of valid procedure codes. Only "
        "return codes that appear in the provided index. When you identify the "
        "clinically appropriate package family for a documented condition but that "
        "family has no entries in the provided index, do not skip the condition. "
        "Instead, find the closest available package in the index that addresses "
        "the same clinical problem — same anatomical region, same procedure type, "
        "same specialty. Every explicitly documented planned procedure must have "
        "at least one candidate returned if any clinically related package exists "
        "in the index.\n"
        
        "Do not pre-select a single variant — downstream "
        "validation will determine the correct one.\n"
        "- Respond with valid JSON only. No prose outside the JSON.\n"
        '- Response schema: {"procedure_codes": ["CODE1", "CODE2", ...]}'
    )

    user_prompt = (
        f"PATIENT CLINICAL PRESENTATION:\n"
        f"Provisional diagnosis: {clinical.provisional_diagnosis}\n"
        f"Chief complaints: {clinical.chief_complaints}\n"
        f"Planned procedure: {clinical.planned_procedure or 'not specified'}\n"
        f"History: {clinical.history_of_present_illness or 'not provided'}\n\n"
        f"AVAILABLE PM-JAY PROCEDURE INDEX:\n"
        f"{serialised_index}\n\n"
        f"Return the procedure codes that match this patient. Respond with JSON only."
    )

    # STEP 4 — Call Gemini.
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    procedure_codes = []

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0,
                    max_output_tokens=16384,
                    http_options=genai.types.HttpOptions(
                        timeout=LLM_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )
            raw_text = response.text
            print(f"\n=== PHASE 2 LLM RAW RESPONSE ===\n{raw_text}\n================================\n")
            logger.debug("Phase 2 LLM response (attempt %d): %s", attempt, raw_text)

            # STEP 5 — Parse and validate the response.
            parsed = _parse_response(raw_text)
            if parsed is not None and "procedure_codes" in parsed:
                procedure_codes = parsed["procedure_codes"]
                break

            logger.warning(
                "Phase 2 LLM — invalid JSON response on attempt %d/%d, retrying",
                attempt,
                LLM_MAX_RETRIES,
            )
        except Exception as exc:
            logger.warning(
                "Phase 2 LLM — API error on attempt %d/%d: %s",
                attempt,
                LLM_MAX_RETRIES,
                exc,
            )
    else:
        logger.error(
            "Phase 2 LLM — all %d retries exhausted, returning empty list",
            LLM_MAX_RETRIES,
        )
        return []

    # Filter and validate each code
    valid_codes_set = {row["procedure_code"] for row in filtered}
    code_to_row = {row["procedure_code"]: row for row in filtered}

    validated_codes = []
    for code in procedure_codes:
        if isinstance(code, str) and code.strip() != "":
            clean_code = code.strip()
            if clean_code in valid_codes_set:
                validated_codes.append(clean_code)

    logger.info(
        "Phase 2 LLM: Gemini returned %d codes, %d passed validation",
        len(procedure_codes),
        len(validated_codes),
    )

    # STEP 6 — Build CandidatePackage list.
    candidates = []
    for code in validated_codes:
        row = code_to_row[code]
        candidates.append(
            CandidatePackage(
                procedure_code=row["procedure_code"],
                package_code=row["package_code"],
                specialty_code=row["specialty_code"],
                specialty=row.get("specialty", ""),
                package_name=row["package_name"],
                procedure_name=row["procedure_name"],
                billing_unit=row["billing_unit"],
                reserved_public_only=row["reserved_public_only"],
                procedure_label=row["procedure_label"],
                auto_approved=row.get("auto_approved", "none"),
                day_care=row["day_care"],
                base_rate_inr=row.get("base_rate_inr"),
                match_score=100.0,
            )
        )

    candidates.sort(key=lambda cp: cp.package_name)
    logger.info("Phase 2 LLM: returning %d candidate(s)", len(candidates))

    # STEP 7 — Warn if empty.
    if not candidates:
        logger.warning(
            "Phase 2: no candidates found for search string — "
            "Phase 3 will receive empty list, USP path will be triggered"
        )

    return candidates


def _parse_response(raw_text: str) -> dict | None:
    """Parse LLM response text as JSON and clean markdown fences if present."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed
