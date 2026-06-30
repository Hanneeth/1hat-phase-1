import json
import logging
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from google import genai
from config import LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES, QUERY_PREDICTOR_TIMEOUT_SECONDS
from models import ChecklistItemResult, CommonQueryRisk, PackageQueryPrediction
from session import IRISSession

load_dotenv()
logger = logging.getLogger(__name__)

QUERY_PREDICTOR_SYSTEM_PROMPT = """You are a PM-JAY pre-authorization query risk analyst for IRIS, an AI-powered pre-authorization engine used by hospital MEDCOs in India.

Your task is to evaluate a patient's clinical data against the PPD (Pre-authorization Processing Doctor) checklist for a specific PM-JAY Health Benefit Package, and to assess the likelihood that the PPD will raise a query on each checklist item or on any of the known common query triggers for this procedure.

Rules:
1. Evaluate each PPD checklist item strictly against the patient data provided. Do not assume documents exist unless they appear in the available documents list or investigations list with document_available=true.
2. For checklist items where expected=false: if the patient data suggests the answer is true (e.g. previous surgery of the same type has been done), this is a RED FLAG — assign risk_level high.
3. For checklist items where expected=true: if the document is missing or the result summary does not confirm the required finding, assign risk_level medium or high depending on how critical the document is to the clinical decision.
4. For common query triggers: assess whether the patient's current documentation would prevent the PPD from raising that query. If the trigger condition is clearly not met by the available data, assign high. If partially addressed, assign medium. If clearly resolved by available documents, assign low.
5. Read investigation result_summary fields carefully — do not just check if a document is present. Check whether the result_summary content actually supports the required clinical finding.
6. Do not invent clinical findings not present in the data.
7. Read each checklist question in the context of the package's clinical indication and any clinical key pointers provided. Do not interpret checklist questions in isolation or using general medical knowledge alone. For example, a burns checklist question referencing a burn depth type should be interpreted using the clinical key pointer definitions provided for that package, not general textbook definitions.
8. When a checklist question asks whether a condition "will not heal without surgery" or "requires surgical intervention", evaluate this against the patient's actual documented burn depth, wound characteristics, and the treating surgeon's plan — not against the literal name of a burn depth classification in the question text.
9. Respond with valid JSON only. No prose, no markdown, no explanation outside the JSON object."""


def _load_stg_for_prediction(procedure_code: str) -> dict | None:
    stg_path = f"data/stg/{procedure_code}.json"
    try:
        with open(stg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("STG file not found for prediction: %s", stg_path)
        return None
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in STG file %s: %s", stg_path, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error loading STG file %s: %s", stg_path, exc)
        return None


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


def _build_query_prediction_prompt(
    stg_dict: dict,
    clinical,
    available_doc_keys: set[str],
    package_name: str,
) -> str:
    # Section 2 HBP package details
    proc_code = stg_dict.get("procedure_code", "")
    condition = stg_dict.get("condition", "")
    proc_name = stg_dict.get("procedure_name", "")

    # Section 3 Checklist items
    checklist_lines = []
    ppd_checklist = stg_dict.get("checklist", {}).get("ppd_preauth", [])
    for idx, item in enumerate(ppd_checklist, 1):
        q = item.get("q", "")
        exp = item.get("expected", True)
        checklist_lines.append(f"{idx}. Question: \"{q}\", Expected: {str(exp).lower()}")
    checklist_str = "\n".join(checklist_lines) if checklist_lines else "None provided."

    # Section 4 Common queries
    common_queries = stg_dict.get("common_queries", [])
    common_queries_lines = []
    for idx, q_str in enumerate(common_queries, 1):
        common_queries_lines.append(f"{idx}. {q_str}")
    common_queries_str = "\n".join(common_queries_lines) if common_queries_lines else "None provided."

    # Section 5 Clinical Minimum Requirements
    indications = stg_dict.get("clinical_indications", [])
    indications_str = "\n".join(f"- {ind}" for ind in indications) if indications else "None provided."

    thresholds = stg_dict.get("clinical_thresholds", [])
    thresholds_lines = []
    for t in thresholds:
        field_name = t.get("field", "")
        op = t.get("operator", "")
        val = t.get("value", "")
        note = t.get("note", "")
        thresholds_lines.append(f"- Field: {field_name}, Operator: {op}, Value: {val}, Note: {note}")
    thresholds_str = "\n".join(thresholds_lines) if thresholds_lines else "None provided."

    min_doc_qual = stg_dict.get("min_doctor_qualification", [])
    min_doc_qual_str = ", ".join(min_doc_qual) if min_doc_qual else "None provided."
    key_pointers = stg_dict.get("additional_information", {}).get("clinical_key_pointers", [])
    key_pointers_str = "\n".join(f"- {kp}" for kp in key_pointers) if key_pointers else "None provided."

    # Section 6 Patient Clinical Data
    exam_findings_str = "Not provided"
    if clinical.examination_findings:
        ef = clinical.examination_findings
        exam_findings_str = (
            f"General: {ef.general or 'not documented'}\n"
            f"CVS: {ef.cvs or 'not documented'}\n"
            f"RS: {ef.rs or 'not documented'}\n"
            f"Abdomen: {ef.abdomen or 'not documented'}\n"
            f"CNS: {ef.cns or 'not documented'}\n"
            f"Local: {ef.local or 'not documented'}"
        )

    doc_str = "Not specified"
    if clinical.treating_doctor:
        td = clinical.treating_doctor
        doc_str = (
            f"Name: {td.name}\n"
            f"Qualification: {td.qualification}\n"
            f"Registration Number: {td.registration_number}"
        )

    comorb_str = _format_comorbidities(clinical.comorbidities)

    # Section 7 Investigations
    investigations_lines = []
    for idx, inv in enumerate(clinical.investigations, 1):
        type_str = inv.type
        res_sum = inv.result_summary or "not provided"
        avail = "true" if inv.document_available else "false"
        rep_date = inv.report_date or "not specified"
        investigations_lines.append(
            f"{idx}. Type: {type_str}, Result Summary: {res_sum}, Document Available: {avail}, Report Date: {rep_date}"
        )
    investigations_str = "\n".join(investigations_lines) if investigations_lines else "None."

    # Section 8 Documents in Hand
    avail_keys_str = ", ".join(sorted(available_doc_keys)) if available_doc_keys else "None"
    non_clinical_docs_lines = []
    for doc in clinical.non_clinical_documents_in_hand:
        non_clinical_docs_lines.append(f"- Key: {doc.key}, Available: {str(doc.available).lower()}")
    non_clinical_docs_str = "\n".join(non_clinical_docs_lines) if non_clinical_docs_lines else "None."

    prompt = f"""SECTION 1 — TASK DESCRIPTION:
You are a PM-JAY pre-authorization query risk analyst for IRIS. Your task is to evaluate whether a patient's clinical data satisfies each PPD checklist item for the selected package, and to score the risk that a PPD will raise a query on each item or on any of the known common query triggers for this procedure.

SECTION 2 — PACKAGE BEING EVALUATED:
Procedure Code: {proc_code}
Package Name: {package_name}
Condition: {condition}
Procedure Name: {proc_name}

SECTION 3 — PPD PREAUTH CHECKLIST:
Expected: true means the answer should be YES for the pre-auth to be valid. Expected: false means the answer should be NO for the pre-auth to be valid — if YES then it is a red flag.
{checklist_str}

SECTION 4 — KNOWN COMMON QUERY TRIGGERS FOR THIS PROCEDURE:
These are documented triggers that PPDs commonly raise for this procedure.
{common_queries_str}

SECTION 5 — CLINICAL MINIMUM REQUIREMENTS:
Clinical Indications:
{indications_str}
Clinical Thresholds:
{thresholds_str}
Minimum Doctor Qualification:
{min_doc_qual_str}

SECTION 5B — CLINICAL KEY POINTERS (context for interpreting checklist questions):
{key_pointers_str}
These are official STG clinical definitions and context notes. Use them when
interpreting the meaning of checklist questions — do not read checklist questions
in isolation. For example, burn depth classifications here define what the checklist
questions about burn type actually mean in clinical context.

SECTION 6 — PATIENT CLINICAL DATA:
Provisional Diagnosis: {clinical.provisional_diagnosis}
Planned Procedure: {clinical.planned_procedure or 'not specified'}
Chief Complaints: {clinical.chief_complaints}
History of Present Illness: {clinical.history_of_present_illness or 'not provided'}
Examination Findings:
{exam_findings_str}
Comorbidities: {comorb_str}
Treating Doctor:
{doc_str}

SECTION 7 — INVESTIGATIONS:
{investigations_str}

SECTION 8 — DOCUMENTS IN HAND:
Available Document Keys: {avail_keys_str}
Non-clinical Documents in Hand:
{non_clinical_docs_str}

SECTION 9 — INSTRUCTIONS FOR RESPONSE:
1. For each checklist item in the ppd_preauth checklist: evaluate the patient data and determine the actual answer (true/false or null if cannot determine). Assign risk_level as follows:
   - "pass": actual matches expected — no concern
   - "low": minor gap or uncertainty, PPD unlikely to query
   - "medium": notable gap or weak evidence, PPD may query
   - "high": clear gap, missing document, contradictory finding, or expected=false condition is present — PPD likely to query

2. For each common query trigger: evaluate whether the patient data makes this trigger likely. Assign risk_level "low", "medium", or "high".

3. Respond with valid JSON only. No prose outside the JSON.

SECTION 10 — RESPONSE SCHEMA:
The checklist_results array must have exactly the same number of items as the ppd_preauth checklist provided. The common_query_risks array must have exactly the same number of items as the common_queries list provided. Do not add or remove items. Do not reorder them.

{{
  "checklist_results": [
    {{
      "question": "<verbatim question text>",
      "expected": true or false,
      "actual": true or false or null,
      "risk_level": "pass" or "low" or "medium" or "high",
      "reasoning": "<one sentence, max 150 chars>"
    }}
  ],
  "common_query_risks": [
    {{
      "query_text": "<verbatim trigger text>",
      "risk_level": "low" or "medium" or "high",
      "reasoning": "<one sentence, max 150 chars>"
    }}
  ]
}}"""
    return prompt


def _parse_response(raw_text: str) -> dict | None:
    logger.debug("Query predictor raw response text: %r", raw_text)

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

    logger.info("Query predictor: JSON extracted via strategy %d", strategy_number)
    return parsed


def _validate_parsed_result(parsed: dict) -> bool:
    if not isinstance(parsed, dict):
        return False
    if "checklist_results" not in parsed or not isinstance(parsed["checklist_results"], list):
        return False
    if "common_query_risks" not in parsed or not isinstance(parsed["common_query_risks"], list):
        return False
    
    # Validate each item in checklist_results
    for item in parsed["checklist_results"]:
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
            
    # Validate each item in common_query_risks
    for item in parsed["common_query_risks"]:
        if not isinstance(item, dict):
            return False
        if "query_text" not in item or not isinstance(item["query_text"], str):
            return False
        if "risk_level" not in item or item["risk_level"] not in ("low", "medium", "high"):
            return False
        if "reasoning" not in item or not isinstance(item["reasoning"], str):
            return False
            
    return True


def _call_llm_and_parse_prediction(prompt: str, procedure_code: str) -> dict | None:
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            logger.info("Query predictor: LLM call attempt %d for %s", attempt, procedure_code)
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=QUERY_PREDICTOR_SYSTEM_PROMPT,
                    temperature=0,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                    http_options=genai.types.HttpOptions(
                        timeout=QUERY_PREDICTOR_TIMEOUT_SECONDS * 1000,
                    ),
                ),
            )
            raw_text = response.text
            logger.debug("Query predictor raw response (attempt %d): %r", attempt, raw_text)
            parsed = _parse_response(raw_text)
            if parsed is not None:
                if _validate_parsed_result(parsed):
                    return parsed
                else:
                    logger.warning("Query predictor: parsed response failed validation on attempt %d", attempt)
            else:
                logger.warning("Query predictor: raw response on attempt %d: %r", attempt, raw_text[:500])
                logger.warning("Query predictor: failed to parse response as JSON on attempt %d", attempt)
        except Exception as exc:
            logger.warning("Query predictor: API error on attempt %d: %s", attempt, exc)
            
    logger.error("Query predictor: all retries exhausted for %s", procedure_code)
    return None


def predict_package_queries(
    session: IRISSession,
    fp,
    available_doc_keys: set[str],
) -> PackageQueryPrediction:
    proc_code = fp.validated.procedure_code
    pkg_name = fp.validated.package_name
    
    # Pre-fetch advisory claim docs to have them for fallback if needed
    advisory_claim_docs = []
    if hasattr(fp.validated, "mandatory_documents"):
        docs_dict = getattr(fp.validated, "mandatory_documents", {}) or {}
        advisory_claim_docs = docs_dict.get("claim", []) or []
    if not advisory_claim_docs:
        try:
            from kb.loader import load_specialty_shard, get_procedure_from_shard
            from phases.phase3_validator import SPECIALTY_CODE_TO_SHARD
            shard_filename = SPECIALTY_CODE_TO_SHARD.get(fp.validated.specialty_code)
            if shard_filename:
                shard = load_specialty_shard(shard_filename)
                proc = get_procedure_from_shard(fp.validated.procedure_code, shard)
                if proc:
                    advisory_claim_docs = proc.get("mandatory_documents", {}).get("claim", []) or []
        except Exception:
            pass

    try:
        # Step 1: Load STG
        stg_dict = _load_stg_for_prediction(proc_code)
        if stg_dict is None:
            logger.warning("Query predictor: STG file not available for %s — returning fallback", proc_code)
            return PackageQueryPrediction(
                procedure_code=proc_code,
                package_name=pkg_name,
                readiness_verdict="unknown",
                verdict_summary="STG file not available — query prediction skipped for this package.",
                checklist_results=[],
                common_query_risks=[],
                advisory_claim_docs=advisory_claim_docs,
                llm_evaluation_status="skipped",
            )

        # Step 2 — Doctor qualification check (token-based match)
        extra_risks = []
        min_qual_list = stg_dict.get("min_doctor_qualification", [])
        treating_doctor = session.clinical.treating_doctor

        if min_qual_list and treating_doctor and treating_doctor.qualification:
            doctor_qual_lower = treating_doctor.qualification.lower()

            # Extract individual qualification tokens from STG list
            # Split on common delimiters: '/', ',', spaces, parentheses
            import re
            qual_tokens = set()
            for qual_entry in min_qual_list:
                # Extract tokens that look like degree names
                # Match: M.Ch, MCh, DNB, MS, MD, MBBS, DM, MCh, DNB, FRCS etc.
                tokens = re.findall(
                    r'\b(M\.?Ch|DNB|MS|MD|MBBS|DM|FRCS|MRCS|MCh|MDS|BDS|MPhil)\b',
                    qual_entry,
                    re.IGNORECASE
                )
                qual_tokens.update(t.lower().replace(".", "") for t in tokens)

            # Also extract specialty context words from STG list
            # e.g. "Plastic Surgery", "General Surgery", "Urology"
            specialty_context = []
            for qual_entry in min_qual_list:
                # Extract words after 'in' or inside parentheses
                in_match = re.findall(r'(?:in |in\s*\()\s*([A-Za-z\s]+?)(?:\)|$)', qual_entry, re.IGNORECASE)
                specialty_context.extend([m.strip().lower() for m in in_match])

            # Check 1: does doctor have any of the required degree tokens?
            degree_match = any(
                token in doctor_qual_lower.replace(".", "")
                for token in qual_tokens
            )

            # Check 2: does doctor's qualification mention the relevant specialty?
            specialty_match = True  # default pass if no specialty context found
            if specialty_context:
                specialty_match = any(
                    spec in doctor_qual_lower
                    for spec in specialty_context
                )

            if not degree_match or not specialty_match:
                extra_risks.append(
                    CommonQueryRisk(
                        query_text="Treating doctor qualification may not meet minimum STG requirement",
                        risk_level="high",
                        reasoning=(
                            f"STG requires one of {min_qual_list}. "
                            f"Treating doctor has: {treating_doctor.qualification}. "
                            f"Degree match: {degree_match}, Specialty match: {specialty_match}."
                        )
                    )
                )
            # If both match: no flag, qualification is satisfied.

        # Step 3: Past claim history check (deterministic)
        if session.patient and getattr(session.patient, "past_claims", None) is not None:
            adm_date = session.clinical.admission_date
            if adm_date and len(adm_date) >= 4:
                current_year = adm_date[:4]
                for claim in session.patient.past_claims:
                    claim_date = claim.admission_date
                    if claim_date and len(claim_date) >= 4:
                        claim_year = claim_date[:4]
                        if claim_year == current_year:
                            is_match = False
                            if claim.procedure_code == fp.validated.procedure_code:
                                is_match = True
                            elif fp.validated.package_code and claim.procedure_code.startswith(fp.validated.package_code):
                                is_match = True
                            elif fp.validated.procedure_code and claim.procedure_code.startswith(fp.validated.procedure_code):
                                is_match = True

                            if is_match:
                                extra_risks.append(
                                    CommonQueryRisk(
                                        query_text="Patient has a prior claim for the same package in the current policy year",
                                        risk_level="high",
                                        reasoning="Duplicate package claim in same policy year — PPD will flag this."
                                    )
                                )
                                break

        # Step 4: CAM Table 3 scheme-wide trigger checks (deterministic)
        # Check A — Vitals
        if session.clinical.vitals is None:
            extra_risks.append(
                CommonQueryRisk(
                    query_text="Provide vitals charts, treatment plan and progress notes",
                    risk_level="high",
                    reasoning="Vitals not recorded in clinical input — CAM Table 3 mandatory."
                )
            )
        
        # Check B — History of present illness
        if session.clinical.history_of_present_illness is None or session.clinical.history_of_present_illness == "":
            extra_risks.append(
                CommonQueryRisk(
                    query_text="Provide vitals charts, treatment plan and progress notes",
                    risk_level="medium",
                    reasoning="History of present illness is missing — PPD may request progress notes."
                )
            )

        # Check C — Treating doctor prescription
        has_presc = ("treating_doctor_prescription" in available_doc_keys or
                     "admission_prescription" in available_doc_keys or
                     "prescription" in available_doc_keys)
        if not has_presc and session.clinical.treating_doctor is not None:
            extra_risks.append(
                CommonQueryRisk(
                    query_text="Provide Treating Doctor's Prescription advising Hospitalization with diagnosis",
                    risk_level="medium",
                    reasoning="No treating doctor prescription found in documents in hand."
                )
            )

        # Check D — Clinical photograph (surgical packages only)
        medical_or_surgical = getattr(fp.validated, "medical_or_surgical", None)
        if medical_or_surgical is None:
            if fp.validated.billing_type == "surgical":
                medical_or_surgical = "surgical"
            else:
                try:
                    from kb.loader import load_specialty_shard, get_procedure_from_shard
                    from phases.phase3_validator import SPECIALTY_CODE_TO_SHARD
                    shard_filename = SPECIALTY_CODE_TO_SHARD.get(fp.validated.specialty_code)
                    if shard_filename:
                        shard = load_specialty_shard(shard_filename)
                        proc = get_procedure_from_shard(fp.validated.procedure_code, shard)
                        if proc:
                            medical_or_surgical = proc.get("medical_or_surgical")
                except Exception:
                    pass

        if medical_or_surgical == "surgical":
            has_photo = any(
                ("photo" in k.lower() or "photograph" in k.lower() or "clinical_photo" in k.lower())
                for k in available_doc_keys
            )
            if not has_photo:
                extra_risks.append(
                    CommonQueryRisk(
                        query_text="Provide clinical photograph of the injury/lesion",
                        risk_level="medium",
                        reasoning="No clinical photograph found — CAM Table 3 item for surgical cases."
                    )
                )

        # Check E — MLC documents
        if getattr(session, "mlc_required", False) is True:
            if "mlc_fir" not in available_doc_keys or "self_declaration" not in available_doc_keys:
                extra_risks.append(
                    CommonQueryRisk(
                        query_text="Provide Self-declaration with detailed narration of incident, mentioning date, place and time. With copy of MLC/FIR.",
                        risk_level="high",
                        reasoning="MLC case — FIR or self-declaration missing from documents in hand."
                    )
                )

        # Step 5: Build prompt and call LLM
        prompt = _build_query_prediction_prompt(stg_dict, session.clinical, available_doc_keys, pkg_name)
        llm_result = _call_llm_and_parse_prediction(prompt, proc_code)
        
        if llm_result is None:
            logger.warning("Query predictor: LLM call returned None for %s", proc_code)
            llm_failed = True
            llm_status = "failed"
            checklist_results = []
            common_query_risks = extra_risks
        else:
            llm_failed = False
            llm_status = "success"

        # Step 6: Build ChecklistItemResult list
        checklist_results = []
        if not llm_failed:
            for item in llm_result.get("checklist_results", []):
                checklist_results.append(
                    ChecklistItemResult(
                        question=item.get("question"),
                        expected=item.get("expected"),
                        actual=item.get("actual"),
                        risk_level=item.get("risk_level"),
                        reasoning=item.get("reasoning")
                    )
                )

        # Step 7: Build CommonQueryRisk list
        common_query_risks = []
        if not llm_failed:
            llm_query_risks = []
            for item in llm_result.get("common_query_risks", []):
                llm_query_risks.append(
                    CommonQueryRisk(
                        query_text=item.get("query_text"),
                        risk_level=item.get("risk_level"),
                        reasoning=item.get("reasoning")
                    )
                )
            common_query_risks = llm_query_risks + extra_risks
        else:
            common_query_risks = extra_risks

        # Step 8: Advisory claim docs
        advisory_claim_docs = stg_dict.get("mandatory_documents", {}).get("claim", []) or []

        # Step 9: Compute verdict (deterministic)
        if llm_failed and not extra_risks:
            verdict = "unknown"
        else:
            if any(item.risk_level == "high" for item in common_query_risks):
                verdict = "likely_queried"
            elif any(item.risk_level == "high" for item in checklist_results):
                verdict = "likely_queried"
            elif any(item.risk_level == "medium" for item in common_query_risks) or any(item.risk_level == "medium" for item in checklist_results):
                verdict = "gaps_present"
            else:
                verdict = "ready"

        # Step 10: Build verdict_summary
        high_items = []
        for item in checklist_results:
            if item.risk_level == "high":
                high_items.append(item.question)
        for item in common_query_risks:
            if item.risk_level == "high":
                high_items.append(item.query_text)
                
        medium_items = []
        for item in checklist_results:
            if item.risk_level == "medium":
                medium_items.append(item.question)
        for item in common_query_risks:
            if item.risk_level == "medium":
                medium_items.append(item.query_text)

        if verdict == "unknown":
            verdict_summary = "Query prediction incomplete — STG unavailable or LLM failed."
        elif high_items:
            verdict_summary = f"{len(high_items)} high-risk item(s) detected: " + ", ".join(high_items[:3])
        elif medium_items:
            verdict_summary = f"{len(medium_items)} item(s) may prompt PPD query."
        else:
            verdict_summary = "All checklist items satisfied — package appears ready for pre-auth submission."

        # Step 11: Return
        return PackageQueryPrediction(
            procedure_code=proc_code,
            package_name=pkg_name,
            readiness_verdict=verdict,
            verdict_summary=verdict_summary,
            checklist_results=checklist_results,
            common_query_risks=common_query_risks,
            advisory_claim_docs=advisory_claim_docs,
            llm_evaluation_status=llm_status,
        )

    except Exception as exc:
        logger.error("Query predictor: unhandled exception for %s: %s", proc_code, exc, exc_info=True)
        return PackageQueryPrediction(
            procedure_code=proc_code,
            package_name=pkg_name,
            readiness_verdict="unknown",
            verdict_summary="Query prediction incomplete — STG unavailable or LLM failed.",
            checklist_results=[],
            common_query_risks=[],
            advisory_claim_docs=advisory_claim_docs,
            llm_evaluation_status="failed",
        )
