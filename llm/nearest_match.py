"""
llm/nearest_match.py — IRIS Nearest Match Finder
=================================================
Called after the pipeline completes with zero selected packages.
Makes a single Gemini LLM call to identify the most clinically relevant
blocked candidate and explain in one sentence what is missing.

Public API:
    get_nearest_match(blocked_candidates, clinical_dict) -> dict | None
"""

# pyrefly: ignore [missing-import]
from google import genai
import json
import logging
from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES
import os

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a clinical package analyst for IRIS, an AI-powered \
PM-JAY pre-authorization engine.

The IRIS pipeline attempted to match a patient to PM-JAY Health Benefit Packages. \
All candidate packages were blocked during clinical eligibility validation. You are \
given the patient's clinical summary and the list of blocked candidates with the \
exact reasons why each was blocked.

Your task:
1. Identify the single blocked candidate that is most clinically relevant to this \
patient — meaning the procedure that most closely matches what this patient actually \
needs for this admission.
2. For that candidate, state in ONE concise sentence exactly what is missing or what \
threshold was not met. Be specific — name the exact criterion, value, or document. \
Do not paraphrase vaguely. Do not say "criteria not met" — say which criterion and \
what the gap is.
3. If ALL candidates are completely irrelevant to this patient (wrong specialty, \
wrong procedure, no clinical connection to the patient's presenting condition), \
set is_relevant to false and set all other fields to null.

Rules:
- Most relevant means the procedure directly addresses the same clinical condition \
this patient is being admitted for.
- Do not invent reasons. Base what_is_missing only on the provided missing_criteria \
and message fields from the blocked candidate.
- what_is_missing must be one sentence, maximum 120 characters. Be specific and \
clinical.
- Respond with valid JSON only. No prose, no markdown, no explanation outside the \
JSON object.

Response schema:
{
  "is_relevant": true or false,
  "nearest_code": "PROCEDURE_CODE or null",
  "package_name": "package name string or null",
  "what_is_missing": "one sentence max 120 chars or null"
}"""


def get_nearest_match(
    blocked_candidates: list[dict],
    clinical_dict: dict,
) -> dict | None:
    """Find the most clinically relevant blocked candidate using Gemini LLM.

    Called when the IRIS pipeline produces zero selected_packages. Looks at 
    all STG_NOT_ELIGIBLE blocked candidates and identifies which one is closest
    to being valid for this patient, and what specifically is missing.

    Only considers candidates with reason_code == "STG_NOT_ELIGIBLE". Candidates 
    blocked for other reasons (SHARD_NOT_FOUND, PUB_RESERVED_BLOCK, 
    PLAUSIBILITY_FAILED, INTERNAL_ERROR, SPECIALTY_CODE_UNKNOWN) are excluded 
    because those represent system-level or administrative blocks rather than 
    clinical eligibility failures — they carry no information about clinical 
    proximity to the patient's condition.

    Args:
        blocked_candidates: list of dicts from session.phase3_blocked, each 
            having keys: procedure_code, reason_code, message, missing_criteria 
            (list), confidence, procedure_name, package_name.
        clinical_dict: the raw "clinical" dict from the IRIS input JSON, as 
            accessed by raw_json.get("clinical", {}) in main.py. Used to give 
            the LLM patient context.

    Returns:
        dict with keys:
            "is_relevant"    — bool, True if a relevant candidate was found
            "nearest_code"   — str or None, procedure code of the nearest match
            "package_name"   — str or None, package name of the nearest match
            "what_is_missing"— str or None, one sentence max 120 chars

        None if:
            - No STG_NOT_ELIGIBLE candidates exist after filtering
            - All LLM retries exhausted

    Side effects:
        Logs INFO (start, result), WARNING (retry), ERROR (all retries failed).
    """
    stg_blocked = [
        entry for entry in blocked_candidates
        if entry.get("reason_code") == "STG_NOT_ELIGIBLE"
    ]
    logger.info("Nearest match: %d STG_NOT_ELIGIBLE candidates to evaluate", len(stg_blocked))
    if not stg_blocked:
        logger.info("Nearest match: no STG_NOT_ELIGIBLE candidates — returning None")
        return None

    user_prompt = (
        "PATIENT CLINICAL SUMMARY:\n"
        f"Provisional diagnosis: {clinical_dict.get('provisional_diagnosis', 'not provided')}\n"
        f"Planned procedure: {clinical_dict.get('planned_procedure') or 'not stated'}\n"
        f"Chief complaints: {clinical_dict.get('chief_complaints', 'not provided')}\n"
        f"History: {clinical_dict.get('history_of_present_illness') or 'not provided'}\n\n"
        "BLOCKED CANDIDATES:\n"
    )

    for entry in stg_blocked:
        missing_str = "; ".join(entry["missing_criteria"]) if entry.get("missing_criteria") else "not specified"
        msg_snippet = entry.get("message", "")[:300]
        user_prompt += (
            "---\n"
            f"Code: {entry['procedure_code']}\n"
            f"Package: {entry.get('package_name', 'unknown')}\n"
            f"Procedure: {entry.get('procedure_name', 'unknown')}\n"
            f"Missing criteria: {missing_str}\n"
            f"Reasoning: {msg_snippet}\n"
        )

    user_prompt += (
        "\nWhich single blocked candidate is most clinically relevant to this patient? "
        "Respond with JSON only."
    )

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0,
                    max_output_tokens=2048,
                    response_mime_type="application/json",
                    http_options=genai.types.HttpOptions(
                        timeout=LLM_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )

            raw_text = response.text
            logger.debug("Nearest match raw LLM response (attempt %d): %r", attempt, raw_text)

            result = _parse_response(raw_text)
            if result is not None:
                logger.info(
                    "Nearest match result: is_relevant=%s, nearest_code=%s",
                    result["is_relevant"],
                    result["nearest_code"],
                )
                return result

            logger.warning(
                "Nearest match: invalid JSON on attempt %d/%d, retrying",
                attempt,
                LLM_MAX_RETRIES,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Nearest match: API error on attempt %d/%d: %s",
                attempt,
                LLM_MAX_RETRIES,
                exc,
            )

    logger.error(
        "Nearest match: all %d retries exhausted, returning None",
        LLM_MAX_RETRIES,
    )
    return None


def _parse_response(raw_text: str) -> dict | None:
    logger.warning("Nearest match raw response text: %r", raw_text)

    parsed = None
    strategy_number = 0

    # STRATEGY 1 — Direct parse
    text_s1 = raw_text.strip()
    try:
        res = json.loads(text_s1)
        if isinstance(res, dict):
            parsed = res
            strategy_number = 1
    except json.JSONDecodeError:
        pass

    # STRATEGY 2 — Strip markdown fences anywhere in the text
    if parsed is None:
        lines = raw_text.splitlines()
        clean_lines = [line for line in lines if not line.strip().startswith("```")]
        text_s2 = "\n".join(clean_lines).strip()
        try:
            res = json.loads(text_s2)
            if isinstance(res, dict):
                parsed = res
                strategy_number = 2
        except json.JSONDecodeError:
            pass

    # STRATEGY 3 — Extract JSON object by brace scanning
    if parsed is None:
        first_brace = raw_text.find('{')
        last_brace = raw_text.rfind('}')
        if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
            text_s3 = raw_text[first_brace:last_brace + 1]
            try:
                res = json.loads(text_s3)
                if isinstance(res, dict):
                    parsed = res
                    strategy_number = 3
            except json.JSONDecodeError:
                pass

    if parsed is None:
        return None

    logger.info("Nearest match: JSON extracted via strategy %d", strategy_number)

    # VALIDATION
    if "is_relevant" not in parsed or not isinstance(parsed["is_relevant"], bool):
        return None

    if parsed["is_relevant"]:
        nearest_code = parsed.get("nearest_code")
        if not isinstance(nearest_code, str) or not nearest_code.strip():
            return None
        what_is_missing = parsed.get("what_is_missing")
        if not isinstance(what_is_missing, str):
            return None
    else:
        parsed["nearest_code"] = None
        parsed["package_name"] = None
        parsed["what_is_missing"] = None

    return parsed
