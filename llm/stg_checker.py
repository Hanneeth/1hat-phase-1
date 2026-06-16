"""
llm/stg_checker.py — IRIS LLM-based STG Eligibility Checker
=============================================================
Implements Phase 3's single LLM call: given an STG dict and a patient's
ClinicalInput, asks Gemini to determine whether the patient meets the
Standard Treatment Guideline eligibility criteria for a specific procedure.

Per SYSTEM_DESIGN.md LLM Usage Policy:
  - Only STG fields that carry clinical signal are sent (noisy fields excluded).
  - On any failure after all retries: fail-open (eligible=True, confidence="low").
  - Temperature is 0 for deterministic output.
  - Response must be valid JSON with "eligible" (bool) and "reasoning" (str).
"""

# pyrefly: ignore [missing-import]
from google import genai
import json
import logging
from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES
from models import ClinicalInput
import os

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a strict clinical eligibility validator for PM-JAY (India's national health scheme) package selection.

You are given:
1. Standard Treatment Guideline (STG) criteria for a specific procedure
2. A patient's clinical presentation

Your task: determine if the patient's clinical condition meets the STG eligibility criteria. This system validates pre-authorization requests for government health insurance reimbursement. False positives result in fraudulent claims. Default to eligible=False whenever a criterion is not unambiguously satisfied by the clinical input.

You MUST respond with valid JSON only. No prose outside the JSON object.

Response schema:
{
  "eligible": true or false,
  "missing_criteria": ["description of each unmet criterion"],
  "reasoning": "one paragraph explanation of your decision",
  "confidence": "high", "medium", or "low"
}

Core principles:

1. Literal interpretation of STG criteria. Every word in an STG criterion carries meaning. If the criterion specifies an etiology, anatomy, severity, range, or modifier, the patient must satisfy it as written. Do not substitute clinically adjacent conditions, do not generalize from specific to broader categories, and do not infer that two distinct categories are equivalent because they share management overlap.

2. Numeric criteria are hard bounds. Any quantitative criterion (percentages, ranges, thresholds, counts, durations, ages) defines a strict inclusion set. Values outside that set fail the criterion. Lower-bound and upper-bound language is to be honored exactly.

3. Anatomical specificity. When an STG names anatomy, only that anatomy satisfies the criterion. Broader regions do not satisfy more specific ones, and one body part does not stand in for another even when functionally related.

4. Domain match between diagnosis and procedure. The patient's presenting condition must be the condition the procedure treats. A procedure designed for condition X is not eligible for a patient with condition Y, regardless of whether Y has features that superficially resemble X. The clinical reasoning required to bridge them is itself evidence the match does not hold.

5. Treat absence as failure — for THRESHOLDS only. Clinical thresholds are hard AND conditions. If a threshold criterion is not unambiguously satisfied by the clinical input, it is unmet. Do not assume presence from context. Do not infer values from related findings. Clinical indications are OR conditions — the patient needs to satisfy at least one, not all. If the patient clearly satisfies one indication, the indication gate is passed even if other listed indications are absent.

6. Confidence calibration. Use "high" when STG criteria are clearly stated and the clinical input clearly satisfies or fails them. Use "medium" when the criteria are clear but the clinical input is partially documented. Use "low" only when the criteria themselves are ambiguous or when interpretation requires judgment beyond literal reading. Never use confidence as a way to soften an unclear pass — if confidence would be low because the match is weak, return eligible=False instead.

7. Doctor qualification handling. If the STG specifies a minimum treating doctor qualification and the clinical input does not include it, note it in missing_criteria and set confidence to "low" but do not return eligible=False solely on this ground. This is an administrative gap, not a clinical mismatch.

8. Comorbidity handling. Comorbidities do not block eligibility unless the STG explicitly lists them as contraindications.

When the clinical input requires you to construct a reasoning chain to justify eligibility — when you find yourself explaining why a near-miss should count — the answer is eligible=False. The STG check exists to enforce the boundaries the STG draws, not to expand them.
"""

USER_PROMPT_TEMPLATE = """STG eligibility criteria for PM-JAY procedure {procedure_code}:

=== CLINICAL INDICATIONS (OR logic — patient must satisfy AT LEAST ONE of these to qualify) ===
{indications}

=== CLINICAL THRESHOLDS (must be met for pre-auth approval) ===
{thresholds}

=== MINIMUM DOCTOR QUALIFICATION ===
{qualifications}

=== EXPECTED LENGTH OF STAY ===
{alos}

=== CLINICAL KEY NOTES ===
{clinical_key_pointers}

=== PATIENT CLINICAL PRESENTATION ===
Provisional diagnosis: {diagnosis}
Chief complaints: {complaints}
Duration: {duration} days
{history_section}
{vitals_section}
{examination_section}
Investigations: {investigations}
Comorbidities: {comorbidities}
{pmh_section}
{medications_section}

Evaluate eligibility and respond with JSON only."""

# ---------------------------------------------------------------------------
# Failure fallback
# ---------------------------------------------------------------------------

_FALLBACK_RESULT: dict = {
    "eligible": True,
    "missing_criteria": [],
    "reasoning": "LLM check failed — passed by default",
    "confidence": "low",
}


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def check_stg_eligibility(
    procedure_code: str,
    stg: dict,
    clinical: ClinicalInput,
) -> dict:
    """LLM-based STG eligibility check.

    Sends STG clinical criteria and the patient's clinical presentation to
    Gemini and asks whether the patient meets the eligibility criteria for
    the specified procedure code.

    Only the following STG fields are forwarded (others are noisy):
      - clinical_indications
      - clinical_thresholds  ({field, operator, value, note} — no unit field)
      - min_doctor_qualification  (list of strings)
      - alos
      - additional_information.clinical_key_pointers

    Args:
        procedure_code: The PM-JAY procedure code being validated (e.g. "MM010B").
        stg:            The parsed STG JSON dict loaded from data/stg/<code>.json.
        clinical:       The ClinicalInput dataclass from the current IRISSession.

    Returns:
        dict with keys:
          "eligible"         — bool
          "missing_criteria" — list[str], unmet criterion descriptions
          "reasoning"        — str, one-paragraph explanation
          "confidence"       — str, "high" | "medium" | "low"

        On failure after all retries, returns the fail-open fallback:
          {"eligible": True, "missing_criteria": [],
           "reasoning": "LLM check failed — passed by default", "confidence": "low"}

    Side effects:
        Logs DEBUG (raw LLM text), INFO (result), WARNING (retry), ERROR (all retries failed).
    """
    user_prompt = _build_user_prompt(procedure_code, stg, clinical)
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0,
                    max_output_tokens=4096,
                    http_options=genai.types.HttpOptions(
                        timeout=LLM_TIMEOUT_SECONDS * 1000,  # SDK uses ms
                    ),
                ),
            )

            raw_text: str = response.text
            logger.debug("RAW TEXT: %r", raw_text)
            logger.debug(
                "STG check raw LLM response for %s (attempt %d): %s",
                procedure_code, attempt, raw_text,
            )

            result = _parse_and_validate(raw_text)
            if result is not None:
                logger.info(
                    "STG check for %s — eligible=%s, confidence=%s",
                    procedure_code, result["eligible"], result["confidence"],
                )
                return result

            logger.warning(
                "STG check for %s — invalid JSON response on attempt %d/%d, retrying",
                procedure_code, attempt, LLM_MAX_RETRIES,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "STG check for %s — API error on attempt %d/%d: %s",
                procedure_code, attempt, LLM_MAX_RETRIES, exc,
            )

    logger.error(
        "STG check for %s — all %d retries exhausted, returning fail-open fallback",
        procedure_code, LLM_MAX_RETRIES,
    )
    return dict(_FALLBACK_RESULT)


def check_plausibility(
    procedure_code: str,
    procedure_name: str,
    clinical: ClinicalInput,
) -> dict:
    """LLM-based clinical plausibility check for a candidate PM-JAY package.

    A lightweight check (max_output_tokens=200) that asks the LLM whether a
    candidate package is clinically plausible for the patient based solely on
    provisional diagnosis, chief complaints, and planned procedure. It is
    distinct from the STG eligibility check — it does not require an STG file
    and is intended as a fast pre-filter.

    Uses the same retry loop and fail-open policy as check_stg_eligibility:
    on all retries exhausted, returns plausible=True so no candidate is
    silently dropped due to an API failure.

    Args:
        procedure_code: PM-JAY procedure code, e.g. "BM001A".
        procedure_name: Human-readable procedure name for the prompt.
        clinical:       ClinicalInput dataclass from the current IRISSession.

    Returns:
        dict with keys:
          "plausible" — bool
          "reason"    — str, one-sentence explanation

        On failure after all retries:
          {"plausible": True, "reason": "Plausibility check failed — passed by default"}

    Side effects:
        Logs INFO (result), WARNING (retry), ERROR (all retries failed).
    """
    _PLAUSIBILITY_SYSTEM = (
        "You are a clinical relevance checker for PM-JAY (India's national health "
        "scheme) package selection.\n\n"
        "Important context: PM-JAY allows multiple packages to be billed for a "
        "single admission. A package does not need to cover the patient's entire "
        "treatment — it only needs to address one specific condition or procedure "
        "that the patient requires during this admission.\n\n"
        "Your only task: determine whether the patient has the specific medical "
        "condition or clinical need that this PM-JAY procedure is designed to treat. "
        "Do not evaluate whether this package alone is sufficient for the patient's "
        "complete treatment. Do not consider what other procedures are planned.\n\n"
        "Respond with valid JSON only. No prose outside the JSON."
    )

    user_prompt = (
        f"Patient provisional diagnosis: {clinical.provisional_diagnosis}\n"
        f"Patient chief complaints: {clinical.chief_complaints}\n"
        f"\n"
        f"Candidate PM-JAY package: {procedure_name} ({procedure_code})\n"
        f"\n"
        f"Does this patient have the specific condition or clinical need that "
        f"this PM-JAY procedure is designed to treat?\n"
        f'Respond JSON only:\n'
        f'{{"plausible": true or false, "reason": "one sentence explanation"}}'
    )

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=_PLAUSIBILITY_SYSTEM,
                    temperature=0,
                    max_output_tokens=4096,
                    http_options=genai.types.HttpOptions(
                        timeout=LLM_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )

            raw_text: str = response.text
            logger.debug(
                "Plausibility check raw LLM response for %s (attempt %d): %s",
                procedure_code, attempt, raw_text,
            )

            result = _parse_plausibility(raw_text)
            if result is not None:
                logger.info(
                    "Plausibility check for %s — plausible=%s, reason=%s",
                    procedure_code, result["plausible"], result["reason"],
                )
                return result

            logger.warning(
                "Plausibility check for %s — invalid JSON on attempt %d/%d, retrying",
                procedure_code, attempt, LLM_MAX_RETRIES,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plausibility check for %s — API error on attempt %d/%d: %s",
                procedure_code, attempt, LLM_MAX_RETRIES, exc,
            )

    logger.error(
        "Plausibility check for %s — all %d retries exhausted, returning fail-open fallback",
        procedure_code, LLM_MAX_RETRIES,
    )
    return {"plausible": True, "reason": "Plausibility check failed — passed by default"}


def resolve_stratum(
    package_code: str,
    survivors: list,
    clinical: ClinicalInput,
    patient,
    stgs: dict,
    shard_procedures: dict,
) -> dict:
    """Pick the single best procedure from multiple same-package survivors.

    Called by _resolve_package_duplicates in phase3_validator.py when multiple
    ValidatedPackage objects share the same package_code and all have
    procedure_label == "regular" and quantity_basis not in ("eye", "limb").

    Two scenarios:
      Scenario A — STG files exist for at least one survivor (stgs is non-empty):
        Sends all STGs together with full patient clinical context. LLM picks one.
      Scenario B — No STG files at all (stgs is empty):
        Sends procedure_name + admission_criteria from shard_procedures for each
        survivor, plus patient numeric values (age, weight_kg, TBSA from diagnosis).
        LLM picks one.

    Fallback (all retries exhausted): keeps the survivor with highest
    fuzz.WRatio score between clinical text and procedure_name.
    If all scores are equal, keeps the first in list order.

    Args:
        package_code:      e.g. "BM001"
        survivors:         list of ValidatedPackage objects sharing this package_code
        clinical:          ClinicalInput from session
        patient:           PatientContext from session
        stgs:              dict of {procedure_code: stg_dict}, may be empty
        shard_procedures:  dict of {procedure_code: kb2_procedure_dict}, may be empty

    Returns:
        dict with keys:
          "selected" — str, the procedure_code to keep
          "reason"   — str, one-sentence explanation

        On fallback:
          "selected" — highest fuzzy match or first in list
          "reason"   — "Tiebreaker LLM failed — selected by procedure name match"
    """
    # pyrefly: ignore [missing-import]
    from rapidfuzz import fuzz as _fuzz

    _SYSTEM = (
        "You are a clinical package selector for PM-JAY (India's national health scheme).\n"
        "You are given multiple candidate procedure options from the same package.\n"
        "Select exactly ONE procedure code that best matches the patient's condition.\n"
        "Respond with valid JSON only. No prose outside the JSON.\n"
        'Schema: {"selected": "PROCEDURE_CODE", "reason": "one sentence"}'
    )

    # Build candidate summaries
    candidate_lines: list[str] = []
    for vp in survivors:
        code = vp.procedure_code
        stg = stgs.get(code)
        shard = shard_procedures.get(code, {})
        proc_name = vp.procedure_name
        admission = shard.get("additional_information", {}).get(
            "admission_criteria", "Not specified"
        ) if shard else "Not specified"

        if stg:
            indications = stg.get("clinical_indications", [])
            thresholds = stg.get("clinical_thresholds", [])
            ind_str = "; ".join(indications[:3]) if indications else "not specified"
            thr_str = (
                "; ".join(
                    f"{t.get('field','')} {t.get('operator','')} {t.get('value','')}"
                    for t in thresholds[:3]
                )
                if thresholds else "none"
            )
            candidate_lines.append(
                f"- {code}: {proc_name}\n"
                f"  STG indications: {ind_str}\n"
                f"  STG thresholds: {thr_str}"
            )
        else:
            candidate_lines.append(
                f"- {code}: {proc_name}\n"
                f"  Admission criteria: {admission}"
            )

    candidates_str = "\n".join(candidate_lines)

    # Patient numeric context
    age = patient.age if patient else "unknown"
    weight = clinical.weight_kg or "not provided"
    diagnosis = clinical.provisional_diagnosis or ""
    complaints = clinical.chief_complaints or ""

    # Extract any numbers from diagnosis for stratum matching
    import re as _re
    numbers_in_diagnosis = _re.findall(r'\d+(?:\.\d+)?', diagnosis)
    numeric_context = (
        f"Numbers in diagnosis (for range matching): {', '.join(numbers_in_diagnosis)}"
        if numbers_in_diagnosis else ""
    )

    user_prompt = (
        f"Package: {package_code}\n"
        f"Patient age: {age}\n"
        f"Patient weight: {weight} kg\n"
        f"Provisional diagnosis: {diagnosis}\n"
        f"Chief complaints: {complaints}\n"
        f"{numeric_context}\n\n"
        f"Candidate procedures (select exactly one):\n"
        f"{candidates_str}\n\n"
        f"Select the single best matching procedure code. "
        f'Respond JSON only: {{"selected": "CODE", "reason": "one sentence"}}'
    )

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=_SYSTEM,
                    temperature=0,
                    max_output_tokens=4096,
                    http_options=genai.types.HttpOptions(
                        timeout=LLM_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )
            raw_text: str = response.text
            result = _parse_stratum_selection(raw_text, [vp.procedure_code for vp in survivors])
            if result is not None:
                logger.info(
                    "Stratum tiebreaker for %s — selected=%s, reason=%s",
                    package_code, result["selected"], result["reason"],
                )
                return result

            logger.warning(
                "Stratum tiebreaker for %s — invalid JSON on attempt %d/%d, retrying",
                package_code, attempt, LLM_MAX_RETRIES,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Stratum tiebreaker for %s — API error on attempt %d/%d: %s",
                package_code, attempt, LLM_MAX_RETRIES, exc,
            )

    # Fallback — highest fuzzy match on procedure_name vs clinical text
    logger.error(
        "Stratum tiebreaker for %s — all %d retries exhausted, using fuzzy fallback",
        package_code, LLM_MAX_RETRIES,
    )
    clinical_text = f"{clinical.provisional_diagnosis or ''} {clinical.chief_complaints or ''}"
    best = max(
        survivors,
        key=lambda vp: _fuzz.WRatio(clinical_text, vp.procedure_name or ""),
    )
    return {
        "selected": best.procedure_code,
        "reason": "Tiebreaker LLM failed — selected by procedure name match",
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_user_prompt(
    procedure_code: str,
    stg: dict,
    clinical: ClinicalInput,
) -> str:
    """Assemble the USER_PROMPT_TEMPLATE with STG criteria and clinical data.

    Args:
        procedure_code: The procedure code for context labelling.
        stg:            Parsed STG dict (KB-3).
        clinical:       Patient clinical input dataclass.

    Returns:
        Fully formatted prompt string ready to send to the LLM.
    """
    # --- STG fields ---
    indications: list[str] = stg.get("clinical_indications", [])
    thresholds: list[dict] = stg.get("clinical_thresholds", [])
    qualifications: list[str] = stg.get("min_doctor_qualification", [])
    alos: str = stg.get("alos", "unspecified")
    key_pointers: list[str] = (
        stg.get("additional_information", {}).get("clinical_key_pointers", [])
    )

    indications_str = (
        "\n".join(f"- {ind}" for ind in indications) if indications else "Not specified"
    )
    thresholds_str = _format_thresholds(thresholds) if thresholds else "None specified"
    qualifications_str = ", ".join(qualifications) if qualifications else "Not specified"
    key_pointers_str = (
        "\n".join(f"- {kp}" for kp in key_pointers) if key_pointers else "None"
    )

    # --- Clinical: optional sections ---
    history_section = (
        f"History of present illness: {clinical.history_of_present_illness}"
        if clinical.history_of_present_illness
        else ""
    )

    vitals_section = _format_vitals(clinical.vitals)

    examination_section = _format_examination(clinical.examination_findings)

    investigations_str = _format_investigations(clinical.investigations)

    comorbidities_str = (
        ", ".join(clinical.comorbidities) if clinical.comorbidities else "None reported"
    )

    pmh_section = (
        f"Past medical history: {clinical.past_medical_history}"
        if clinical.past_medical_history
        else ""
    )

    medications_section = (
        f"Current medications: {', '.join(clinical.current_medications)}"
        if clinical.current_medications
        else ""
    )

    return USER_PROMPT_TEMPLATE.format(
        procedure_code=procedure_code,
        indications=indications_str,
        thresholds=thresholds_str,
        qualifications=qualifications_str,
        alos=alos,
        clinical_key_pointers=key_pointers_str,
        diagnosis=clinical.provisional_diagnosis,
        complaints=clinical.chief_complaints,
        duration=clinical.duration_days,
        history_section=history_section,
        vitals_section=vitals_section,
        examination_section=examination_section,
        investigations=investigations_str,
        comorbidities=comorbidities_str,
        pmh_section=pmh_section,
        medications_section=medications_section,
    )


def _format_thresholds(thresholds: list[dict]) -> str:
    """Format threshold list for prompt.

    Each threshold dict has: {field, operator, value, note} — NO unit field.
    Format as: "- {field} {operator} {value} (Note: {note})"

    Args:
        thresholds: List of threshold dicts from stg["clinical_thresholds"].

    Returns:
        Multi-line formatted string, or "None specified" if list is empty.
    """
    if not thresholds:
        return "None specified"
    lines: list[str] = []
    for t in thresholds:
        field = t.get("field", "")
        operator = t.get("operator", "")
        value = t.get("value", "")
        note = t.get("note", "")
        line = f"- {field} {operator} {value}"
        if note:
            line += f" (Note: {note})"
        lines.append(line)
    return "\n".join(lines)


def _format_investigations(investigations: list) -> str:
    """Format investigation list for prompt.

    For each investigation: include type, result_summary, and structured_values
    if present. Structured values are shown as "parameter=value unit" pairs.

    Args:
        investigations: List of Investigation dataclass objects.

    Returns:
        Multi-line formatted string, or "None" if list is empty.
    """
    if not investigations:
        return "None"
    parts: list[str] = []
    for inv in investigations:
        inv_type: str = inv.type
        summary: str | None = inv.result_summary
        structured = inv.structured_values

        line = f"[{inv_type.upper()}]"
        if summary:
            line += f" {summary}"

        if structured:
            sv_parts: list[str] = []
            for sv in structured:
                param = sv.parameter
                val = sv.value
                unit = sv.unit or ""
                pair = f"{param}={val}"
                if unit:
                    pair += f" {unit}"
                if sv.flag and sv.flag != "N":
                    pair += f" ({sv.flag})"
                sv_parts.append(pair)
            if sv_parts:
                line += " | Values: " + ", ".join(sv_parts)

        parts.append(line)
    return "\n".join(parts)


def _format_vitals(vitals: dict) -> str:
    """Format non-null vitals dict into a readable string for the prompt.

    Args:
        vitals: dict of vital sign values (may contain None values).

    Returns:
        Formatted "Vitals: ..." string, or empty string if all values are null.
    """
    non_null = {k: v for k, v in vitals.items() if v is not None}
    if not non_null:
        return ""
    pairs = ", ".join(f"{k}: {v}" for k, v in non_null.items())
    return f"Vitals: {pairs}"


def _format_examination(examination_findings) -> str:
    """Format ExaminationFindings dataclass fields into a prompt string.

    Only non-null fields are included.

    Args:
        examination_findings: ExaminationFindings dataclass or None.

    Returns:
        Formatted multi-line string, or empty string if None or all fields null.
    """
    if examination_findings is None:
        return ""
    field_labels = [
        ("general", "General"),
        ("cvs", "CVS"),
        ("rs", "RS"),
        ("abdomen", "Abdomen"),
        ("cns", "CNS"),
        ("local", "Local"),
    ]
    lines: list[str] = []
    for attr, label in field_labels:
        val = getattr(examination_findings, attr, None)
        if val is not None:
            lines.append(f"{label}: {val}")
    if not lines:
        return ""
    return "Examination:\n" + "\n".join(lines)


def _parse_and_validate(raw_text: str) -> dict | None:
    """Parse LLM response text as JSON and validate required keys.

    Strips markdown code fences if present before parsing.

    Args:
        raw_text: The raw string returned by the Gemini API.

    Returns:
        Validated dict if parsing succeeds and "eligible" (bool) and
        "reasoning" (str) keys are present; None otherwise.
    """
    text = raw_text.strip()
    # Strip markdown code fences that some models include
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first and last fence lines
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None
    if not isinstance(parsed.get("eligible"), bool):
        return None
    if not isinstance(parsed.get("reasoning"), str):
        return None

    # Ensure optional fields have sensible defaults if LLM omitted them
    parsed.setdefault("missing_criteria", [])
    parsed.setdefault("confidence", "low")

    return parsed


def _parse_plausibility(raw_text: str) -> dict | None:
    """Parse and validate an LLM response for the plausibility check.

    Strips markdown code fences if present before parsing. Validates that
    the response contains "plausible" (bool) and "reason" (str).

    Args:
        raw_text: The raw string returned by the Gemini API.

    Returns:
        Validated dict with "plausible" and "reason" keys, or None on failure.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None
    if not isinstance(parsed.get("plausible"), bool):
        return None
    if not isinstance(parsed.get("reason"), str):
        return None

    return parsed


def _parse_stratum_selection(raw_text: str, valid_codes: list[str]) -> dict | None:
    """Parse and validate LLM response for resolve_stratum.

    Strips markdown fences, parses JSON, validates that:
    - "selected" is a str present in valid_codes
    - "reason" is a str

    Args:
        raw_text:    Raw LLM response string.
        valid_codes: List of valid procedure codes the LLM may select from.

    Returns:
        Validated dict or None on any parse/validation failure.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None
    selected = parsed.get("selected")
    if not isinstance(selected, str):
        return None
    if selected not in valid_codes:
        return None
    if not isinstance(parsed.get("reason"), str):
        return None

    return parsed
