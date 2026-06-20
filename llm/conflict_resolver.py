"""
llm/conflict_resolver.py — IRIS Phase 4 Conflict Resolver
==========================================================
Called at the start of Phase 4 when validated_packages has more than one 
package. Detects and resolves two types of conflicts before PM-JAY billing 
combination rules are applied:

1. Mutual exclusivity — two packages represent alternative classifications 
   of the same clinical entity (e.g. Thermal burns vs Flame burns). Only 
   one can be correct.

2. Sub-inclusion — one package's procedure description explicitly includes 
   a procedure that another validated package represents as a standalone 
   charge (e.g. burns package includes skin graft in its rate; separate 
   skin graft package is double-billing).

Public API:
    resolve_conflicts(validated_packages, clinical_dict) -> list[ValidatedPackage]
"""

import json
import logging
import os

from dotenv import load_dotenv  # pyrefly: ignore [missing-import]
from google import genai  # pyrefly: ignore [missing-import]

from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES
from models import ValidatedPackage

load_dotenv()
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a clinical billing conflict detector for IRIS, an AI-powered "
    "PM-JAY pre-authorization engine.\n\n"
    "You are given a list of PM-JAY packages that have passed clinical "
    "eligibility validation for a patient. Your task is to identify packages "
    "that should be DROPPED before billing because they either:\n\n"
    "1. MUTUALLY EXCLUSIVE: Two packages represent alternative clinical "
    "classifications of the same condition and only one can be correct for "
    "this patient. Example: 'Thermal burns' and 'Flame burns' are separate "
    "PM-JAY package families for different burn mechanisms — a patient cannot "
    "simultaneously have both. The diagnosis and clinical details determine "
    "which is correct.\n\n"
    "2. SUB-INCLUDED: One package's description explicitly states that a "
    "certain procedure is already included in its rate, and another validated "
    "package separately represents that same procedure. Filing both would be "
    "double-billing. Example: a burns package description says 'Includes % "
    "TBSA skin grafted, flap cover, follow-up dressings' — a separately "
    "validated skin graft package alongside it is already covered and must "
    "be dropped.\n\n"
    "Rules:\n"
    "1. Only drop a package if it is genuinely conflicting by one of the two "
    "criteria above. Do NOT drop packages that treat separate, distinct "
    "clinical conditions in the same admission — those are additive and "
    "should all be retained.\n"
    "2. When two packages are mutually exclusive, drop the one whose "
    "classification does NOT match the patient's documented diagnosis. "
    "The provisional_diagnosis is the authoritative classification.\n"
    "3. When a package is sub-included in another, drop the sub-included "
    "one, not the primary package.\n"
    "4. If no conflicts exist, return an empty codes_to_drop list.\n"
    "5. Respond with valid JSON only. No prose outside the JSON.\n\n"
    "Response schema:\n"
    "{\n"
    '  "codes_to_drop": ["CODE1", "CODE2"],\n'
    '  "reasons": {\n'
    '    "CODE1": "one sentence why this is dropped",\n'
    '    "CODE2": "one sentence why this is dropped"\n'
    "  }\n"
    "}"
)


def _parse_response(raw_text: str) -> dict | None:
    logger.debug("Conflict resolver raw response text: %r", raw_text)

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

    # STRATEGY 2 — Strip markdown fences
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

    logger.info("Conflict resolver: JSON extracted via strategy %d", strategy_number)

    # VALIDATION
    if "codes_to_drop" not in parsed or not isinstance(parsed["codes_to_drop"], list):
        return None

    for item in parsed["codes_to_drop"]:
        if not isinstance(item, str):
            return None

    return parsed


def resolve_conflicts(
    validated_packages: list[ValidatedPackage],
    clinical_dict: dict,
) -> list[ValidatedPackage]:
    """Detect and remove conflicting packages before Phase 4 billing rules.

    Called when len(validated_packages) > 1. Sends all validated packages 
    to the LLM with patient context. The LLM identifies packages that are 
    either mutually exclusive alternatives or sub-included in another 
    package's rate, and returns the codes to DROP.

    Args:
        validated_packages: Full list of ValidatedPackage objects from Phase 3.
        clinical_dict: Raw clinical dict from the IRIS input JSON (the 
            session.clinical fields as a dict), used to give the LLM 
            patient context.

    Returns:
        Filtered list of ValidatedPackage objects with conflicts removed.
        On LLM failure, returns the original list unchanged (fail-open).
    """
    if len(validated_packages) <= 1:
        logger.info("Conflict resolver: only %d package(s), skipping", len(validated_packages))
        return validated_packages

    logger.info("Conflict resolver: checking %d packages for conflicts", len(validated_packages))

    user_prompt = (
        "PATIENT CLINICAL SUMMARY:\n"
        f"Provisional diagnosis: "
        f"{clinical_dict.get('provisional_diagnosis', 'not provided')}\n"
        f"Planned procedure: "
        f"{clinical_dict.get('planned_procedure') or 'not stated'}\n"
        f"Chief complaints: "
        f"{clinical_dict.get('chief_complaints', 'not provided')}\n\n"
        "VALIDATED PACKAGES (all passed clinical eligibility):\n"
    )
    for pkg in validated_packages:
        user_prompt += (
            f"- {pkg.procedure_code} | {pkg.package_name} | "
            f"{pkg.procedure_name[:200]}\n"
        )
    user_prompt += (
        "\nAre any of these packages mutually exclusive alternatives for the "
        "same condition, or sub-included in another package's rate? "
        "Return codes_to_drop. If no conflicts, return empty list. "
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
                    max_output_tokens=4096,
                    response_mime_type="application/json",
                    http_options=genai.types.HttpOptions(
                        timeout=LLM_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )

            raw_text = response.text
            logger.debug("Conflict resolver raw LLM response (attempt %d): %r", attempt, raw_text)

            result = _parse_response(raw_text)
            if result is not None:
                codes_to_drop = set(result.get("codes_to_drop", []))
                reasons = result.get("reasons", {})

                if not codes_to_drop:
                    logger.info("Conflict resolver: no conflicts detected")
                    return validated_packages

                for code in codes_to_drop:
                    logger.warning(
                        "Conflict resolver: dropping %s — %s",
                        code,
                        reasons.get(code, "conflict"),
                    )

                filtered = [
                    pkg for pkg in validated_packages
                    if pkg.procedure_code not in codes_to_drop
                ]

                logger.info(
                    "Conflict resolver: %d → %d packages after conflict resolution",
                    len(validated_packages),
                    len(filtered),
                )
                return filtered

            logger.warning(
                "Conflict resolver: invalid response on attempt %d/%d, retrying",
                attempt,
                LLM_MAX_RETRIES,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Conflict resolver: API error on attempt %d/%d: %s",
                attempt,
                LLM_MAX_RETRIES,
                exc,
            )

    logger.error("Conflict resolver: all retries exhausted — returning original list unchanged (fail-open)")
    return validated_packages
