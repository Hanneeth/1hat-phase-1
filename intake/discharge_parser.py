import json
import logging
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from google import genai

from config import LLM_MODEL, LLM_MAX_RETRIES, QUERY_PREDICTOR_TIMEOUT_SECONDS

load_dotenv()
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a medical document parser for IRIS, an AI-powered PM-JAY claims verification "
    "engine used by hospital MEDCOs in India. Your task is to read all provided discharge "
    "documents and extract structured information into a fixed JSON schema. Rules: 1. Extract "
    "information exactly as it appears in the documents — do not infer, summarize, or "
    "fabricate clinical findings. 2. For discharge_summary_text, operative_notes_text, and "
    "pre_anaesthesia_text: reproduce the full text verbatim or near-verbatim from the "
    "relevant document — do not shorten or paraphrase. 3. If a field cannot be determined "
    "from the documents, set it to null, false, 0, or [] as appropriate for its type. "
    "4. dates must be in YYYY-MM-DD format. Times must be in HH:MM format. "
    "5. actual_los_days must be an integer computed as discharge date minus admission date "
    "in calendar days. 6. ward_category_actual must be exactly one of: ward, hdu, icu, "
    "icu_vent. Choose the most intensive level of care the patient received during the "
    "admission. 7. discharge_status must be exactly one of: recovered, lama, referred, died. "
    "8. deviations_declared must always be an empty list. 9. Respond with valid JSON only — "
    "no prose, no markdown, no explanation outside the JSON object. 10. For the "
    "documents_submitted array: you are given a pre-populated list of document entries with "
    "canonical keys. For each entry, set available to true if the corresponding document is "
    "mentioned as present, available, submitted, or enclosed anywhere in the provided "
    "discharge documents — regardless of the exact wording used in the document. Set it to "
    "false if the document is not mentioned or is explicitly stated as unavailable. Do NOT add "
    "new entries to the documents_submitted array and do NOT rename or change any key values. "
    "Only update the available field on the entries already present in the schema. "
    "11. For the package_booked field: extract the bare PM-JAY procedure code only "
    "(e.g. 'SL016A', 'BM001B', 'SG050B'). Do not include the package name, description, "
    "or any additional text. If the document contains 'SL016A — Tonsillectomy (U/L "
    "tonsillectomy unilateral/bilateral)', extract only 'SL016A'."
)

SCHEMA_TEMPLATE = """{
  "preauth_reference": "<string — injected by caller, not parsed from docs>",
  "case_id": "<string — injected by caller, not parsed from docs>",
  "preauth_input_path": null,

  "hospital": {
    "name": "<string — hospital name from documents>",
    "hospital_code": null,
    "address": "<string — hospital address if present>",
    "district": "<string — district if present>"
  },

  "patient": {
    "name": "<string — patient full name>",
    "pmjay_id": "<string — PMJAY/Ayushman Bharat ID if present>",
    "age": 0,
    "gender": "M",
    "address": "<string — patient address if present>",
    "contact_number": "<string — contact number if present>",
    "ipd_number": "<string — IPD/inpatient number if present>",
    "pmjay_case_id": "<string — same as preauth_reference>"
  },

  "treating_consultant": {
    "name": "<string — treating doctor full name>",
    "contact_number": "<string or null>",
    "qualification": "<string — doctor qualifications>",
    "registration_number": "<string — medical registration number>",
    "specialty": "<string — clinical specialty>"
  },

  "admission": {
    "date_of_admission": "<string — YYYY-MM-DD format>",
    "time_of_admission": "<string or null — HH:MM format if present>",
    "date_of_discharge": "<string — YYYY-MM-DD format>",
    "time_of_discharge": "<string or null — HH:MM format if present>",
    "date_of_operation": "<string or null — YYYY-MM-DD format, first operation date>",
    "actual_los_days": 0,
    "ward_category_actual": "<string — one of: ward, hdu, icu, icu_vent>",
    "bed_category_breakdown": {
      "ward_days": 0,
      "hdu_days": 0,
      "icu_days": 0,
      "icu_vent_days": 0
    },
    "admission_type": "<string — emergency or elective>",
    "package_booked": "<string or null — PM-JAY procedure code ONLY e.g. 'SL016A' or 'BM001B'. Extract the bare alphanumeric code only — do NOT include the package name, description, or any text after the code>",
    "discharge_status": "<string — one of: recovered, lama, referred, died>",
    "discharge_condition": "<string — stable, guarded, critical, or similar>"
  },

  "clinical": {
    "presenting_complaints": "<string — chief complaints on admission>",
    "duration_of_complaints": "<string — how long complaints present before admission>",
    "initial_assessment": "<string — clinical assessment on admission>",
    "significant_past_history": "<string — past medical and surgical history>",
    "primary_diagnosis_at_admission": "<string — diagnosis at admission>",
    "final_diagnosis_at_discharge": "<string — final diagnosis at discharge>",
    "icd10_codes": ["<string>"],
    "key_investigations": "<string — list of investigations done>",
    "investigation_findings": "<string — summary of investigation results>",
    "treatment_given": "<string — full treatment narrative>",
    "operative_findings": "<string — intraoperative findings if surgery was done>",
    "final_procedure_performed": "<string — final procedure performed at discharge>",
    "complications": "<string or null — any complications, null if none>",
    "follow_up_date": "<string or null — YYYY-MM-DD format>",
    "advice_on_discharge": "<string — discharge advice given to patient>",
    "discharge_summary_text": "<string — full verbatim or near-verbatim discharge summary text extracted from documents>",
    "operative_notes_text": "<string or null — full verbatim or near-verbatim operative notes text if present in documents>",
    "pre_anaesthesia_text": "<string or null — full verbatim or near-verbatim pre-anaesthesia checkup text if present>"
  },

  "discharge_medications": ["<string — each medication as one string entry>"],

  "signatures": {
    "treating_consultant_signed": false,
    "pmam_signed": false,
    "patient_or_attendant_signed": false
  },

  "patient_amount_collected_inr": 0,

  "sha_notification_date": "<string or null — YYYY-MM-DD format if MLC/death case and SHA notification date is present>",

  "implant_details": null,

  "documents_submitted": __DOCUMENTS_SUBMITTED_PLACEHOLDER__,

  "deviations_declared": []
}"""


def parse_discharge_from_documents(
    files_text: dict[str, str],
    preauth_reference: str,
    case_id: str,
    procedure_doc_keys: list[dict] | None = None,
) -> dict | None:
    """Parse medical documents text using Gemini into the discharge JSON schema.

    Args:
        files_text: Dict mapping filename (e.g. "doc.pdf") to extracted text.
        preauth_reference: Pre-auth identifier string.
        case_id: Case identifier string.
        procedure_doc_keys: Optional list of dynamic document keys for this case.

    Returns:
        The populated discharge schema dict on success, or None on failure.
        Never raises exceptions.
    """
    logger.info(
        "Starting discharge document parsing for case_id='%s' (%d documents)",
        case_id,
        len(files_text),
    )

    if not procedure_doc_keys:
        logger.warning(
            "parse_discharge_from_documents: procedure_doc_keys is None or empty. "
            "Falling back to universal discharge_summary checklist entry."
        )
        docs_array = [
            {"key": "discharge_summary", "label": "Discharge Summary", "available": False}
        ]
    else:
        docs_array = []
        seen = set()
        for doc in procedure_doc_keys:
            key = doc.get("key")
            label = doc.get("label") or key
            if key and key not in seen:
                seen.add(key)
                docs_array.append({
                    "key": key,
                    "label": label,
                    "available": False
                })
        
        # Ensure discharge_summary is present
        if "discharge_summary" not in seen:
            docs_array.insert(0, {
                "key": "discharge_summary",
                "label": "Discharge Summary",
                "available": False
            })

    dynamic_schema = SCHEMA_TEMPLATE.replace(
        "__DOCUMENTS_SUBMITTED_PLACEHOLDER__",
        json.dumps(docs_array, indent=4)
    )

    # Build the prompt
    user_prompt = (
        f"The following documents have been uploaded for discharge case {case_id} "
        f"(preauth reference: {preauth_reference}). Extract all information into the discharge "
        f"JSON schema.\n\n"
    )
    for filename, text in files_text.items():
        user_prompt += f"--- DOCUMENT: {filename} ---\n{text}\n\n"

    user_prompt += (
        "Now populate the following JSON schema exactly. Return only the completed JSON object:\n\n"
        f"{dynamic_schema}"
    )

    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=LLM_MODEL,
                    contents=user_prompt,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0,
                        max_output_tokens=65536,
                        response_mime_type="application/json",
                        http_options=genai.types.HttpOptions(
                            timeout=QUERY_PREDICTOR_TIMEOUT_SECONDS * 1000,
                        ),
                    ),
                )
                raw_text = response.text
                logger.debug(
                    "Discharge parser raw response (attempt %d): %r",
                    attempt,
                    raw_text,
                )

                parsed = _parse_discharge_response(raw_text)
                if parsed is not None:
                    # Overwrite injected keys
                    parsed["preauth_reference"] = preauth_reference
                    parsed["case_id"] = case_id
                    parsed["deviations_declared"] = []

                    logger.info(
                        "Successfully parsed discharge documents for case_id='%s'",
                        case_id,
                    )
                    return parsed

                logger.warning(
                    "Discharge parser: failed to parse response on attempt %d/%d",
                    attempt,
                    LLM_MAX_RETRIES,
                )
            except Exception as exc:
                logger.warning(
                    "Discharge parser: API error on attempt %d/%d: %s",
                    attempt,
                    LLM_MAX_RETRIES,
                    exc,
                )

        logger.error(
            "Discharge parser: all %d retries exhausted for case_id='%s'",
            LLM_MAX_RETRIES,
            case_id,
        )
        return None

    except Exception as exc:
        logger.error(
            "Unexpected error in parse_discharge_from_documents for case_id='%s': %s",
            case_id,
            exc,
        )
        return None


def _parse_discharge_response(raw_text: str) -> dict | None:
    """Parse JSON response from the LLM using 3 extraction strategies."""
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
        first_brace = raw_text.find("{")
        last_brace = raw_text.rfind("}")
        if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
            text_s3 = raw_text[first_brace : last_brace + 1]
            try:
                res = json.loads(text_s3)
                if isinstance(res, dict):
                    parsed = res
                    strategy_number = 3
            except json.JSONDecodeError:
                pass

    if parsed is None:
        return None

    logger.info("Discharge parser: JSON extracted via strategy %d", strategy_number)
    return parsed
