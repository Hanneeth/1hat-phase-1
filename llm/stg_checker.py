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

SYSTEM_PROMPT = """You are a clinical eligibility validator for PM-JAY (India's national health scheme) package selection.

You are given:
1. Standard Treatment Guideline (STG) criteria for a specific procedure
2. A patient's clinical presentation

Your task: determine if the patient's clinical condition meets the STG eligibility criteria.

You MUST respond with valid JSON only. No prose outside the JSON object.

Response schema:
{
  "eligible": true or false,
  "missing_criteria": ["description of each unmet criterion"],
  "reasoning": "one paragraph explanation of your decision",
  "confidence": "high", "medium", or "low"
}

Rules:
- Match medical concepts even when exact words differ. Example: "BCVA 6/60" satisfies threshold "BCVA <= 6/9" because 6/60 is worse vision than 6/9.
- If a required investigation type is mentioned in criteria but not present in the clinical input at all, list it as missing.
- If an investigation exists but the specific value isn't recorded, set confidence to "low".
- Comorbidities do not block eligibility unless the STG explicitly states a contraindication.
- Minimum doctor qualification: if treating doctor's qualification is not provided or clearly below minimum, note it but do not block — set confidence to "low".
- If clinical_key_pointers contain important eligibility details, use them.
"""

USER_PROMPT_TEMPLATE = """STG eligibility criteria for PM-JAY procedure {procedure_code}:

=== CLINICAL INDICATIONS ===
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
