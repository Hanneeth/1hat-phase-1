import json
import logging
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from google import genai
from config import LLM_MODEL, QUERY_PREDICTOR_TIMEOUT_SECONDS, LLM_MAX_RETRIES
from models import CPDChecklistResult, DeviationItem

load_dotenv()
logger = logging.getLogger(__name__)

CPD_EVALUATOR_SYSTEM_PROMPT = (
    "You are a PM-JAY claims verification analyst for IRIS, an AI-powered "
    "pre-authorization and claims engine used by hospital MEDCOs in India.\n\n"
    "Your task is to evaluate a patient's discharge documents against the CPD "
    "(Claims Processing Doctor) checklist for a specific PM-JAY Health Benefit "
    "Package, and to assess the likelihood that the CPD will raise a query or "
    "apply a deduction on each checklist item. You must also draft justification "
    "text for any clinical deviations detected between what was pre-authorized "
    "and what actually happened at discharge.\n\n"
    "Rules:\n"
    "1. Evaluate each CPD checklist item strictly against the discharge "
    "documents provided. Do not assume documents exist unless they appear "
    "in the available documents list with available=true.\n"
    "2. For checklist items where expected=true: if the document is missing "
    "or the text content does not confirm the required finding, assign "
    "risk_level medium or high.\n"
    "3. For checklist items where expected=false: if the discharge data "
    "suggests the answer is true, assign risk_level high.\n"
    "4. Read all text fields carefully — discharge_summary_text, "
    "operative_notes_text, pre_anaesthesia_text. Cross-reference across "
    "these documents. Do not read any single document in isolation.\n"
    "5. For each deviation provided: draft a concise, factual justification "
    "in clinical language that the MEDCO can submit to TMS. The "
    "justification must: reference the specific clinical reason from the "
    "discharge data, acknowledge the deviation explicitly, and state why "
    "it was medically necessary. Maximum 100 words per justification.\n"
    "6. Do not invent clinical findings not present in the discharge data.\n"
    "7. Respond with valid JSON only. No prose, no markdown, no explanation "
    "outside the JSON object.\n"
    "8. For each deviation candidate provided in Section 9: assess whether it represents "
    "a genuine clinical deviation from what was pre-authorized, or whether it is merely a "
    "difference in wording, anatomical specificity, or level of detail describing the same "
    "procedure or finding. Assign severity as follows:\n"
    "- 'none': the discharge finding is clinically identical to what was pre-authorized — "
    "  only the level of detail or wording differs. No justification needed.\n"
    "- 'info': a minor administrative or documentation difference with no clinical impact. "
    "  Justification optional.\n"
    "- 'warning': a genuine clinical deviation that differs from what was pre-authorized "
    "  in a medically meaningful way. Justification required.\n"
    "- 'block': a severe deviation — wrong procedure performed, wrong patient, fraud "
    "  indicator, or package criteria clearly not met. Justification required.\n"
    "For severity 'none' or 'info', justification_draft may be an empty string.\n"
    "For severity 'warning' or 'block', draft a concise clinical justification as per rule 5."
)

CLINICAL_CONSISTENCY_SYSTEM_PROMPT = (
    "You are a PM-JAY claims consistency analyst for IRIS, an AI-powered "
    "pre-authorization and claims engine used by hospital MEDCOs in India.\n\n"
    "Your task is to compare the clinical information recorded at pre-authorization "
    "against the clinical information in the discharge documents, and identify any "
    "significant inconsistencies that the CPD (Claims Processing Doctor) would flag.\n\n"
    "Rules:\n"
    "1. A diagnosis that is more specific at discharge than at pre-auth is EXPECTED "
    "and normal — do not flag it. Example: 'Acute Cholecystitis' at pre-auth becoming "
    "'Post-operative status following lap cholecystectomy for acute calculous "
    "cholecystitis' at discharge is consistent.\n"
    "2. A completely different condition at discharge that was not related to the "
    "pre-auth diagnosis IS a significant inconsistency — flag it.\n"
    "3. New comorbidities discovered during admission are expected — do not flag them "
    "unless they would have changed the package selection at pre-auth.\n"
    "4. Admission type mismatch (emergency at pre-auth, planned at discharge or vice "
    "versa) should be flagged as a warning.\n"
    "5. Only flag genuine clinical inconsistencies — not documentation style differences.\n"
    "6. Respond with valid JSON only. No prose, no markdown outside the JSON object."
)


def _load_stg_for_claim(procedure_code: str) -> dict | None:
    """Construct path as f"data/stg/{procedure_code}.json", open and parse JSON.
    Return parsed dict on success, or None on FileNotFoundError or JSONDecodeError.
    Log WARNING if not found, ERROR if malformed. Never raise.
    """
    stg_path = f"data/stg/{procedure_code}.json"
    try:
        with open(stg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("STG file not found for claim: %s", stg_path)
        return None
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in STG file %s: %s", stg_path, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error loading STG file %s: %s", stg_path, exc)
        return None


def _build_cpd_evaluation_prompt(
    stg_dict: dict,
    discharge_dict: dict,
    deviations: list[dict],
    package_name: str,
    preauth_output_dict: dict,
    preauth_input_dict: dict,
) -> str:
    """Build prompt with 12 sections including preauth baseline and STG context."""
    # SECTION 1 — TASK
    task_desc = (
        "SECTION 1 — TASK:\n"
        f"You are evaluating a PM-JAY claim submission for the package: {package_name}.\n"
        "Your tasks are:\n"
        "1. Evaluate each CPD checklist item against the discharge documents.\n"
        "2. Draft justification text for each deviation.\n"
    )

    # SECTION 2 — PACKAGE
    proc_code = stg_dict.get("procedure_code", "")
    pkg_name = stg_dict.get("package_name", package_name)
    condition = stg_dict.get("condition", "")
    proc_name = stg_dict.get("procedure_name", "")
    package_info = (
        "SECTION 2 — PACKAGE:\n"
        f"Procedure Code: {proc_code}\n"
        f"Package Name: {pkg_name}\n"
        f"Condition: {condition}\n"
        f"Procedure Name: {proc_name}\n"
    )

    # SECTION 2B — PRE-AUTHORIZATION BASELINE (what was approved)
    if not preauth_output_dict or not preauth_input_dict:
        preauth_baseline_info = (
            "SECTION 2B — PRE-AUTHORIZATION BASELINE (what was approved):\n"
            "Pre-authorization baseline: not available — comparison against "
            "pre-auth not possible for this run.\n"
        )
    else:
        selected = preauth_output_dict.get("selected_packages") or [{}]
        validated = (selected[0].get("validated") or {}) if selected else {}
        
        p_code = validated.get("procedure_code", "not available")
        p_pkg_name = validated.get("package_name", "not available")
        p_proc_name = validated.get("procedure_name", "not available")
        p_billing_type = validated.get("billing_type", "not available")
        p_base_rate = validated.get("base_rate_inr", "not available")
        p_stg_reasoning = validated.get("stg_reasoning", "not available")

        clinical = preauth_input_dict.get("clinical") or {}
        p_diag = clinical.get("provisional_diagnosis", "not available")
        p_plan_proc = clinical.get("planned_procedure", "not available")
        p_bed_cat = clinical.get("bed_category", "not available")
        p_chief_compl = clinical.get("chief_complaints", "not available")

        treating_doc = clinical.get("treating_doctor") or {}
        p_doc_name = treating_doc.get("name", "not available")
        p_doc_reg = treating_doc.get("registration_number", "not available")
        p_doc_qual = treating_doc.get("qualification", "not available")

        preauth_baseline_info = (
            "SECTION 2B — PRE-AUTHORIZATION BASELINE (what was approved):\n"
            "This is what was approved at pre-authorization. Compare this against "
            "the discharge documents to identify any deviations that CPD may flag.\n"
            f"- procedure_code: {p_code}\n"
            f"- package_name: {p_pkg_name}\n"
            f"- Pre-auth approved procedure name: {p_proc_name}\n"
            f"- billing_type: {p_billing_type}\n"
            f"- base_rate_inr: {p_base_rate}\n"
            f"- stg_reasoning: {p_stg_reasoning}\n"
            f"- Diagnosis at pre-auth: {p_diag}\n"
            f"- Procedure planned at pre-auth: {p_plan_proc}\n"
            f"- Ward category at pre-auth: {p_bed_cat}\n"
            f"- chief_complaints: {p_chief_compl}\n"
            f"- Treating doctor at pre-auth: {p_doc_name}\n"
            f"- registration_number: {p_doc_reg}\n"
            f"- qualification: {p_doc_qual}\n"
        )

    # SECTION 2C — STG CLINICAL CONTEXT
    min_doc_qual = stg_dict.get("min_doctor_qualification", [])
    stg_alos = stg_dict.get("alos")
    clinical_ind = stg_dict.get("clinical_indications", [])
    additional_info = stg_dict.get("additional_information", {})
    key_pointers = additional_info.get("clinical_key_pointers", []) if isinstance(additional_info, dict) else []

    stg_clinical_context_parts = [
        "SECTION 2C — STG CLINICAL CONTEXT:",
        "Use these STG requirements to evaluate whether the discharge documents "
        "demonstrate compliance. Cross-reference treating doctor qualification "
        "at discharge against min_doctor_qualification. Cross-reference actual "
        "LOS against alos.",
        f"- min_doctor_qualification: {min_doc_qual}",
        f"- alos: {stg_alos}",
        f"- clinical_indications: {clinical_ind}"
    ]
    if key_pointers:
        stg_clinical_context_parts.append(f"- clinical_key_pointers: {key_pointers}")
        
    stg_clinical_context = "\n".join(stg_clinical_context_parts) + "\n"

    # SECTION 3 — CPD CLAIM CHECKLIST
    checklist_lines = []
    cpd_checklist = stg_dict.get("checklist", {}).get("cpd_claim", [])
    for idx, item in enumerate(cpd_checklist, 1):
        q = item.get("q", "")
        exp = item.get("expected", True)
        checklist_lines.append(f"{idx}. Question: \"{q}\", Expected: {str(exp).lower()}")
    
    checklist_str = "\n".join(checklist_lines) if checklist_lines else "None provided."
    checklist_section = (
        "SECTION 3 — CPD CLAIM CHECKLIST:\n"
        "Expected: true means yes is required. Expected: false means yes is a red flag.\n"
        f"{checklist_str}\n"
    )

    # SECTION 4 — DISCHARGE DOCUMENTS AVAILABLE
    docs_submitted = discharge_dict.get("documents_submitted", {})
    doc_lines = []
    if isinstance(docs_submitted, dict):
        for k, v in docs_submitted.items():
            doc_lines.append(f"- {k}: {str(v).lower()}")
    elif isinstance(docs_submitted, list):
        for doc in docs_submitted:
            if isinstance(doc, dict):
                k = doc.get("key", "")
                v = doc.get("available", False)
                doc_lines.append(f"- {k}: {str(v).lower()}")
            else:
                doc_lines.append(f"- {doc}: true")
    docs_str = "\n".join(doc_lines) if doc_lines else "None provided."
    docs_section = (
        "SECTION 4 — DISCHARGE DOCUMENTS AVAILABLE:\n"
        f"{docs_str}\n"
    )

    # SECTION 5 — DISCHARGE SUMMARY TEXT
    clinical = discharge_dict.get("clinical", {})
    ds_text = clinical.get("discharge_summary_text") or "Not provided"
    ds_section = (
        "SECTION 5 — DISCHARGE SUMMARY TEXT:\n"
        f"{ds_text}\n"
    )

    # SECTION 6 — OPERATIVE NOTES TEXT
    op_text = clinical.get("operative_notes_text") or "Not provided"
    op_section = (
        "SECTION 6 — OPERATIVE NOTES TEXT:\n"
        f"{op_text}\n"
    )

    # SECTION 7 — PRE-ANAESTHESIA TEXT
    pa_text = clinical.get("pre_anaesthesia_text") or "Not provided"
    pa_section = (
        "SECTION 7 — PRE-ANAESTHESIA TEXT:\n"
        f"{pa_text}\n"
    )

    # SECTION 8 — STRUCTURED DISCHARGE FIELDS
    final_diag = clinical.get("final_diagnosis_at_discharge", "Not provided")
    final_proc = clinical.get("final_procedure_performed", "Not provided")
    op_findings = clinical.get("operative_findings", "Not provided")
    treatment = clinical.get("treatment_given", "Not provided")
    complications = clinical.get("complications") or "None"
    discharge_cond = clinical.get("discharge_condition", "Not provided")
    advice = clinical.get("advice_on_discharge", "Not provided")

    admission = discharge_dict.get("admission", {})
    los = admission.get("actual_los_days", "Not provided")
    ward = admission.get("ward_category_actual", "Not provided")
    discharge_status = admission.get("discharge_status", "Not provided")

    consultant = discharge_dict.get("treating_consultant", {})
    c_name = consultant.get("name", "Not provided")
    c_qual = consultant.get("qualification", "Not provided")
    c_reg = consultant.get("registration_number", "Not provided")

    structured_section = (
        "SECTION 8 — STRUCTURED DISCHARGE FIELDS:\n"
        "From Clinical:\n"
        f"- Final Diagnosis at Discharge: {final_diag}\n"
        f"- Final Procedure Performed: {final_proc}\n"
        f"- Operative Findings: {op_findings}\n"
        f"- Treatment Given: {treatment}\n"
        f"- Complications: {complications}\n"
        f"- Discharge Condition: {discharge_cond}\n"
        f"- Advice on Discharge: {advice}\n"
        "From Admission:\n"
        f"- Actual LoS Days: {los}\n"
        f"- Ward Category Actual: {ward}\n"
        f"- Discharge Status: {discharge_status}\n"
        "From Treating Consultant:\n"
        f"- Name: {c_name}\n"
        f"- Qualification: {c_qual}\n"
        f"- Registration Number: {c_reg}\n"
    )

    # SECTION 9 — DEVIATIONS TO JUSTIFY
    dev_lines = []
    if deviations:
        for idx, d in enumerate(deviations, 1):
            dev_lines.append(
                f"{idx}. Type: {d.get('deviation_type')}\n"
                f"   From Value: {d.get('from_value')}\n"
                f"   To Value: {d.get('to_value')}\n"
                f"   Description: {d.get('description')}"
            )
        deviations_str = "\n".join(dev_lines)
    else:
        deviations_str = "No deviations detected — skip justification drafting."
    deviations_section = (
        "SECTION 9 — DEVIATIONS TO JUSTIFY:\n"
        f"{deviations_str}\n"
    )

    # SECTION 10 — RESPONSE SCHEMA
    response_section = (
        "SECTION 10 — RESPONSE SCHEMA:\n"
        "{\n"
        '  "cpd_checklist_results": [\n'
        "    {\n"
        '      "question": "<verbatim question text>",\n'
        '      "expected": true or false,\n'
        '      "actual": true or false or null,\n'
        '      "risk_level": "pass" or "low" or "medium" or "high",\n'
        '      "reasoning": "<one sentence max 200 chars>"\n'
        "    }\n"
        "  ],\n"
        '  "deviation_justifications": [\n'
        "    {\n"
        '      "deviation_type": "<type string>",\n'
        '      "severity": "none" or "info" or "warning" or "block",\n'
        '      "justification_draft": "<max 100 words clinical justification, empty string if severity is none or info>"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "cpd_checklist_results must have exactly the same number of items as the cpd_claim checklist.\n"
        "deviation_justifications must have one entry per deviation candidate in the deviations list. For each entry you must include deviation_type, severity, and justification_draft.\n"
        "If no deviations, return empty list.\n"
        "Respond with JSON only."
    )

    prompt_parts = [
        task_desc,
        package_info,
        preauth_baseline_info,
        stg_clinical_context,
        checklist_section,
        docs_section,
        ds_section,
        op_section,
        pa_section,
        structured_section,
        deviations_section,
        response_section
    ]

    return "\n\n".join(prompt_parts)


def _parse_cpd_response(raw_text: str) -> dict | None:
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

    logger.info("CPD evaluator: JSON extracted via strategy %d", strategy_number)
    return parsed


def _validate_cpd_result(parsed: dict) -> bool:
    """Validate JSON keys and data types against schema expectations."""
    if not isinstance(parsed, dict):
        return False
    if "cpd_checklist_results" not in parsed or not isinstance(parsed["cpd_checklist_results"], list):
        return False
    if "deviation_justifications" not in parsed or not isinstance(parsed["deviation_justifications"], list):
        return False

    # Validate cpd_checklist_results
    for item in parsed["cpd_checklist_results"]:
        if not isinstance(item, dict):
            return False
        if "question" not in item or not isinstance(item["question"], str):
            return False
        if "expected" not in item or not isinstance(item["expected"], bool):
            return False
        if "actual" not in item or item["actual"] not in (True, False, None):
            return False
        if "risk_level" not in item or item["risk_level"] not in ("pass", "low", "medium", "high"):
            return False
        if "reasoning" not in item or not isinstance(item["reasoning"], str):
            return False

    # Validate deviation_justifications
    for item in parsed["deviation_justifications"]:
        if not isinstance(item, dict):
            return False
        if "deviation_type" not in item or not isinstance(item["deviation_type"], str):
            return False
        if "severity" not in item or item["severity"] not in ("none", "info", "warning", "block"):
            return False
        if "justification_draft" not in item or not isinstance(item["justification_draft"], str):
            return False

    return True


def _call_cpd_llm_and_parse(
    prompt: str,
    procedure_code: str,
) -> dict | None:
    """Execute Gemini API call with configured parameters and retry loop."""
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            logger.info("CPD evaluator: LLM call attempt %d for %s", attempt, procedure_code)
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=CPD_EVALUATOR_SYSTEM_PROMPT,
                    temperature=0,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                    http_options=genai.types.HttpOptions(
                        timeout=QUERY_PREDICTOR_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )
            raw_text = response.text
            logger.debug("CPD evaluator raw response (attempt %d): %r", attempt, raw_text)
            
            parsed = _parse_cpd_response(raw_text)
            if parsed is not None:
                if _validate_cpd_result(parsed):
                    return parsed
                else:
                    logger.warning("CPD evaluator: parsed response failed validation on attempt %d", attempt)
            else:
                logger.warning("CPD evaluator: failed to parse response as JSON on attempt %d", attempt)
        except Exception as exc:
            logger.warning("CPD evaluator: API error on attempt %d: %s", attempt, exc)
            
    logger.error("CPD evaluator: all retries exhausted for %s", procedure_code)
    return None


def evaluate_claim_with_cpd(
    procedure_code: str,
    package_name: str,
    discharge_dict: dict,
    deviations: list[DeviationItem],
    preauth_output_dict: dict | None = None,
    preauth_input_dict: dict | None = None,
) -> tuple[list[CPDChecklistResult], list[DeviationItem], str]:
    """Evaluate a claim submission using CPD checklist and clinical deviations."""
    if preauth_output_dict is None:
        preauth_output_dict = {}
    if preauth_input_dict is None:
        preauth_input_dict = {}
    try:
        # Step 1: Load STG
        stg_dict = _load_stg_for_claim(procedure_code)
        if stg_dict is None:
            logger.warning("CPD evaluator: STG file not available for %s — skipping evaluation", procedure_code)
            return ([], deviations, "skipped")

        # Step 2: Serialize deviations for prompt
        deviations_as_dicts = []
        for dev in deviations:
            deviations_as_dicts.append({
                "deviation_type": dev.deviation_type,
                "from_value": dev.from_value,
                "to_value": dev.to_value,
                "description": dev.description,
            })

        # Step 3: Build prompt
        prompt = _build_cpd_evaluation_prompt(
            stg_dict, discharge_dict, deviations_as_dicts, package_name,
            preauth_output_dict, preauth_input_dict
        )

        # Step 4: Call LLM
        llm_result = _call_cpd_llm_and_parse(prompt, procedure_code)
        if llm_result is None:
            logger.warning("CPD evaluator: LLM call failed for %s", procedure_code)
            return ([], deviations, "failed")

        # Step 5: Build CPDChecklistResult list
        checklist_results = []
        for item in llm_result.get("cpd_checklist_results", []):
            checklist_results.append(
                CPDChecklistResult(
                    question=item["question"],
                    expected=item["expected"],
                    actual=item["actual"],
                    risk_level=item["risk_level"],
                    reasoning=item["reasoning"],
                )
            )

        # Step 6: Apply justifications to DeviationItems in-place
        justification_map = {
            d["deviation_type"]: d["justification_draft"]
            for d in llm_result.get("deviation_justifications", [])
        }
        severity_map = {
            d["deviation_type"]: d["severity"]
            for d in llm_result.get("deviation_justifications", [])
        }
        for dev in deviations:
            if dev.deviation_type in justification_map:
                dev.justification_draft = justification_map[dev.deviation_type]
            if dev.deviation_type in severity_map:
                severity_val = severity_map[dev.deviation_type]
                dev.severity = severity_val
                dev.justification_required = severity_val in ("warning", "block")

        # Step 7: Return
        return (checklist_results, deviations, "success")

    except Exception as exc:
        logger.error("CPD evaluator: Unexpected error evaluating claim for %s: %s", procedure_code, exc)
        return ([], deviations, "failed")


def _format_comorbidities(comorbidities: list) -> str:
    """Format comorbidities list into a readable string for the prompt.

    Handles list elements that are either dicts or strings.

    Args:
        comorbidities: List of comorbidities (dicts or strings).

    Returns:
        Formatted single-line string with "; " separators, or "None reported".
    """
    if not comorbidities:
        return "None reported"
    formatted_items = []
    for item in comorbidities:
        if isinstance(item, dict):
            condition = item.get("condition")
            if not condition:
                continue
            details = []
            duration = item.get("duration")
            if duration:
                details.append(f"duration: {duration}")
            controlled = item.get("controlled")
            if controlled is True:
                details.append("controlled")
            elif controlled is False:
                details.append("uncontrolled")
            if details:
                formatted_items.append(f"{condition} ({', '.join(details)})")
            else:
                formatted_items.append(condition)
        elif isinstance(item, str):
            formatted_items.append(item)
    if not formatted_items:
        return "None reported"
    return "; ".join(formatted_items)


def check_clinical_consistency(
    preauth_input_dict: dict,
    discharge_dict: dict,
) -> list[dict]:
    """Check clinical consistency between pre-auth and discharge data.
    
    Returns a list of issue dicts, each with:
      - field: str (e.g. "diagnosis", "admission_type", "comorbidities")
      - severity: str ("warning" only — no hard blocks from clinical consistency)
      - description: str (one sentence)
    
    Returns [] if no issues found or if LLM call fails (fail-open).
    """
    preauth_clinical = preauth_input_dict.get("clinical", {})
    discharge_clinical = discharge_dict.get("clinical", {})
    discharge_admission = discharge_dict.get("admission", {})

    prompt = (
        "Compare the following pre-authorization clinical data against the "
        "discharge clinical data and identify any significant inconsistencies.\n\n"
        "PRE-AUTHORIZATION DATA:\n"
        f"Provisional diagnosis: "
        f"{preauth_clinical.get('provisional_diagnosis', 'not provided')}\n"
        f"Planned procedure: "
        f"{preauth_clinical.get('planned_procedure', 'not provided')}\n"
        f"Comorbidities: "
        f"{_format_comorbidities(preauth_clinical.get('comorbidities', []))}\n"
        f"Admission type (is_emergency): "
        f"{preauth_clinical.get('is_emergency', 'not provided')}\n\n"
        "DISCHARGE DATA:\n"
        f"Primary diagnosis at admission: "
        f"{discharge_clinical.get('primary_diagnosis_at_admission', 'not provided')}\n"
        f"Final diagnosis at discharge: "
        f"{discharge_clinical.get('final_diagnosis_at_discharge', 'not provided')}\n"
        f"Final procedure performed: "
        f"{discharge_clinical.get('final_procedure_performed', 'not provided')}\n"
        f"Treatment given: "
        f"{discharge_clinical.get('treatment_given', 'not provided')}\n"
        f"Complications: "
        f"{discharge_clinical.get('complications', 'none')}\n"
        f"Discharge status: "
        f"{discharge_admission.get('discharge_status', 'not provided')}\n"
        f"Admission type at discharge: "
        f"{discharge_admission.get('admission_type', 'not provided')}\n\n"
        "Return JSON only in this exact format:\n"
        "{\n"
        '  "issues": [\n'
        '    {\n'
        '      "field": "<field name>",\n'
        '      "severity": "warning",\n'
        '      "description": "<one sentence max 200 chars>"\n'
        '    }\n'
        '  ]\n'
        "}\n"
        "If no issues found, return: {\"issues\": []}"
    )

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=CLINICAL_CONSISTENCY_SYSTEM_PROMPT,
                    temperature=0,
                    max_output_tokens=2048,
                    response_mime_type="application/json",
                    http_options=genai.types.HttpOptions(
                        timeout=QUERY_PREDICTOR_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )
            raw_text = response.text
            # 3-strategy JSON parse — same pattern as rest of codebase
            parsed = None
            for strategy, text in [
                (1, raw_text.strip()),
                (2, "\n".join(
                    l for l in raw_text.splitlines()
                    if not l.strip().startswith("```")
                ).strip()),
                (3, raw_text[raw_text.find('{'):raw_text.rfind('}')+1]
                    if '{' in raw_text and '}' in raw_text else ""),
            ]:
                try:
                    res = json.loads(text)
                    if isinstance(res, dict) and "issues" in res:
                        parsed = res
                        logger.info(
                            "Clinical consistency: JSON via strategy %d", strategy
                        )
                        break
                except json.JSONDecodeError:
                    pass

            if parsed is not None:
                issues = parsed.get("issues", [])
                # Validate each issue has required fields
                valid_issues = []
                for issue in issues:
                    if (isinstance(issue, dict)
                            and "field" in issue
                            and "severity" in issue
                            and "description" in issue):
                        valid_issues.append(issue)
                return valid_issues

            logger.warning(
                "Clinical consistency: invalid response on attempt %d/%d",
                attempt, LLM_MAX_RETRIES
            )

        except Exception as exc:
            logger.warning(
                "Clinical consistency: API error on attempt %d/%d: %s",
                attempt, LLM_MAX_RETRIES, exc
            )

    logger.error("Clinical consistency: all retries exhausted — returning [] (fail-open)")
    return []
