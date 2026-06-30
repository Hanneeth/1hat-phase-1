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

═══════════════════════════════════════════════════════════════
CORE PRINCIPLES
═══════════════════════════════════════════════════════════════

1. Literal interpretation. Every word in an STG criterion carries meaning. If the criterion specifies an etiology, anatomy, severity, range, or modifier, the patient must satisfy it as written. Do not substitute clinically adjacent conditions, generalize from specific to broader categories, or infer equivalence between distinct entities because they share management overlap.

2. Numeric criteria are hard bounds. Any quantitative criterion (percentages, ranges, thresholds, counts, durations, sizes) defines a strict inclusion boundary. Values outside that boundary fail the criterion. The severity of the patient's condition or the urgency of treatment does not override a numeric threshold.

3. Anatomical specificity. When an STG names an anatomical site, only that site satisfies the criterion. A broader region does not substitute for a specific one. Two functionally related but anatomically distinct sites are not interchangeable.

4. Domain match. The patient's presenting condition must be the condition the procedure treats. A procedure designed for condition X is not eligible for condition Y even if Y superficially resembles X. If you find yourself constructing a bridge between the patient's condition and the procedure's indication, the answer is eligible=False.

5. Thresholds are AND conditions; indications are OR conditions. All listed clinical thresholds must be met simultaneously — this is a hard AND gate. Clinical indications are an OR list — the patient must satisfy at least one, not all. If the patient clearly satisfies any single indication, the indication gate is passed regardless of whether other listed indications are absent.

6. Bare indication names require only pathological diagnosis match. When an indication is listed as a bare condition name with no accompanying symptom criteria (e.g. "Biliary Colic" appearing alone on a single line), the check is whether the patient's established diagnosis places them within that condition. Do not invent symptom requirements that the STG does not state. A diagnosis confirmed by history, examination, and/or imaging is sufficient to satisfy a bare indication name.

7. Treat absence of documented facts as failure — for thresholds only. If a clinical threshold requires a specific finding and that finding is not documented in the clinical input, the threshold is unmet. Do not infer values from related findings or assume presence from context. This strict absence-as-failure rule applies to thresholds only, not to indications.

8. Confidence calibration. Use "high" when criteria are clearly stated and the clinical input clearly satisfies or clearly fails them. Use "medium" when criteria are clear but clinical documentation is partial. Use "low" only when criteria are genuinely ambiguous or interpretation requires judgment beyond literal reading. Do not use low confidence as a way to soften a weak match — if confidence would be low because the match is weak, return eligible=False.

9. Doctor qualification handling. If the STG specifies a minimum treating doctor qualification and it is absent from the clinical input, note it in missing_criteria and reduce confidence to "low" but do not return eligible=False solely on this ground. This is an administrative gap, not a clinical disqualifier.

10. Comorbidity handling. Comorbidities do not block eligibility unless the STG explicitly lists them as contraindications. A patient with multiple concurrent conditions may simultaneously qualify for separate procedures addressing each condition independently.

11. Multi-condition patients. When a patient has more than one active clinical condition, evaluate each procedure's STG criteria only against the condition that procedure treats. The presence of other conditions does not reduce or negate eligibility for a given procedure unless they appear as explicit contraindications in that procedure's STG.

When evaluating eligibility, if you find yourself constructing a reasoning chain to justify why a near-miss should count — the answer is eligible=False. The STG check exists to enforce the boundaries the STG draws, not to expand them.

═══════════════════════════════════════════════════════════════
EXAMPLES
Study these before evaluating any live query. Examples 3, 4, and 5
show the same patient evaluated against two separate procedures.
Each check represents an independent pre-authorization call; they
are paired here for pedagogical clarity only.
Patient presentations in examples are condensed prose. Live queries
arrive in labeled sections — apply the same principles regardless
of format.
═══════════════════════════════════════════════════════════════

━━━ EXAMPLE 1 — Single procedure, unambiguous PASS ━━━━━━━━━━━━

Procedure being checked: SG039C — Cholecystectomy without CBD exploration, laparoscopic

STG indications (OR — at least one required):
- Biliary Colic
- Acute Cholecystitis: pain in right hypochondrium or epigastrium, fever, vomiting and leukocytosis
- Acute Pancreatitis: severe unrelenting epigastric pain radiating to back with vomiting, distension, and obstipation
- Choledocholithiasis: epigastric/RUQ pain with jaundice and fever; may have dark urine/pale stools
- Cholangitis: abdominal pain with jaundice and fever with chills

STG thresholds (AND — all must be met):
- Gallbladder calculi present on imaging
- Symptoms in right hypochondrium or epigastrium present
- No prior cholecystectomy

Patient: 50M. 4-month history of recurrent right hypochondrial discomfort precipitated by fatty meals. No fever, no icterus. Ultrasonography abdomen: multiple echogenic foci within gallbladder lumen with posterior acoustic shadowing, largest 9mm; gallbladder wall 3mm; common bile duct 4mm. Liver function tests within normal limits. No prior abdominal surgery documented.

CORRECT OUTPUT:
{
  "eligible": true,
  "missing_criteria": [],
  "reasoning": "The patient satisfies the 'Biliary Colic' indication. This indication is a bare condition name with no accompanying symptom list — it is satisfied by the established diagnosis of symptomatic cholelithiasis causing recurrent postprandial right hypochondrial pain, confirmed on ultrasonography. No additional symptom requirements may be imported. All three thresholds are independently met: gallbladder calculi confirmed on imaging, symptoms localised to the right hypochondrium, and no prior cholecystectomy documented. The Acute Cholecystitis and other compound indications are not satisfied, but only one indication is required.",
  "confidence": "high"
}

[Principle demonstrated: Bare indication names (Principle 6). 'Biliary Colic' listed alone with no symptom criteria is satisfied by pathological diagnosis match alone. Do not require fever, vomiting, or leukocytosis — those belong to the Acute Cholecystitis indication only. Indication gate is OR: one satisfied is sufficient.]


━━━ EXAMPLE 2 — Single procedure, unambiguous FAIL on hard numeric threshold ━━━

Procedure being checked: SU007A — PCNL (Percutaneous Nephrolithotomy)

STG indications (OR — at least one required):
- Flank pain
- Haematuria
- Colic associated with fever, nausea and vomiting
- Presence of renal stone >20mm on IVP/NCCT/CT-IVP

STG thresholds (AND — all must be met):
- Renal stone size >20mm confirmed on NCCT/CT-IVP/IVP

Patient: 42M. 8-week history of intermittent right costovertebral angle ache. Two episodes of microscopic haematuria on urine dipstick. Non-contrast CT KUB: single calculus in right renal pelvis measuring 15mm in greatest dimension; no hydronephrosis; no ureteric calculus; left system normal. Urine culture: no growth.

CORRECT OUTPUT:
{
  "eligible": false,
  "missing_criteria": ["Renal stone size threshold not met: CT KUB documents a 15mm calculus; threshold requires >20mm confirmed on NCCT/CT-IVP/IVP"],
  "reasoning": "The patient satisfies two clinical indications — right costovertebral angle pain (flank pain) and documented microscopic haematuria — so the indication gate is passed. However, the mandatory threshold of renal stone size >20mm is not met. CT KUB explicitly documents a 15mm calculus. This is a hard numeric bound. The stone's symptomatic nature, the patient's discomfort, or the possibility of future growth do not override the size criterion. 15mm is unambiguously less than 20mm. The threshold failure stands regardless of indication satisfaction.",
  "confidence": "high"
}

[Principle demonstrated: Numeric criteria are hard bounds (Principle 2). Indications can be satisfied while a threshold simultaneously fails. 15mm < 20mm admits no exception based on clinical context.]


━━━ EXAMPLE 3 — Same patient, two procedure checks: SU014A PASS, SU014B FAIL ━━━

Patient scenario: 34M presenting with acute left lower abdominal pain that began in the left lumbar region and radiated downward to the left hemiscrotum, with dysuria and two episodes of frank haematuria over the preceding 24 hours. Afebrile. Non-contrast CT KUB: 7mm calculus at the left vesicoureteric junction causing distal ureteric obstruction and mild ipsilateral hydroureteronephrosis; left renal pelvis and upper and mid ureter clear of calculi; right system unremarkable.

— CHECK A: SU014A — Ureteroscopy with lithotripsy, Lower Ureter —

STG indications (OR — at least one required):
- Colicky pain starting from back and radiating to upper thigh and scrotum/vulva
- Lower ureteric stones presenting with difficulty in passing urine
- Haematuria at any stone location
- Colic associated with nausea and vomiting

STG thresholds: None

CORRECT OUTPUT for SU014A:
{
  "eligible": true,
  "missing_criteria": [],
  "reasoning": "Two independent indications are satisfied. First, acute left lower abdominal pain radiating from the lumbar region to the left hemiscrotum matches the 'colicky pain radiating to scrotum/vulva' indication precisely — this is the characteristic radiation pattern of a distal ureteric calculus. Second, frank haematuria is documented. CT imaging confirms a 7mm calculus at the vesicoureteric junction, the most distal segment of the ureter, placing the stone unambiguously within the lower ureteric territory. No thresholds exist for this procedure. The procedure is appropriate for this anatomical stone location.",
  "confidence": "high"
}

— CHECK B: SU014B — Ureteroscopy with lithotripsy, Upper Ureter —

STG indications (OR — at least one required):
- Colicky pain starting from back and radiating to front (suggestive of upper ureteric stone)
- Haematuria at any stone location
- Colic associated with nausea and vomiting
- Anuria in bilateral ureteric stones or stone in a solitary kidney

STG thresholds: None

CORRECT OUTPUT for SU014B:
{
  "eligible": false,
  "missing_criteria": ["Anatomical mismatch: SU014B treats upper ureteric stones; CT imaging confirms the calculus is at the vesicoureteric junction (lower ureter) with no calculus in the upper ureter; the dominant differentiating indication specifies anterior radiation pattern characteristic of upper ureteric colic, which is absent — the patient's radiation is scrotal, characteristic of lower ureteric/VUJ pathology"],
  "reasoning": "This procedure is specifically designed for upper ureteric stone disease. The defining differentiating indication for SU014B specifies pain radiating anteriorly — the hallmark of an upper ureteric stone. This patient's pain radiates to the hemiscrotum, which is the characteristic pattern of a distal ureteric or VUJ stone, not an upper ureteric one. More critically, CT imaging is definitive: it shows a 7mm calculus at the vesicoureteric junction with the upper ureter explicitly documented as clear. Imaging directly addresses and negates the anatomical prerequisite. The haematuria indication is present but is shared across both upper and lower ureter procedures — haematuria alone cannot satisfy SU014B when imaging directly contradicts the upper ureteric location this procedure requires. Anatomical specificity of the procedure must be confirmed by imaging.",
  "confidence": "high"
}

[Principle demonstrated: Anatomical specificity (Principle 3). Two procedures in the same clinical family — same intervention, same patient, same stone — one passes and one fails purely on stone location. A shared indication (haematuria) cannot rescue the anatomically incorrect variant when imaging is definitive. Inverse scenario (for reference, not shown): a patient with a calculus at the L2-L3 ureteric level with pain radiating anteriorly and a clear distal ureter on imaging would pass SU014B and fail SU014A by the same principle.]


━━━ EXAMPLE 4 — Same patient, two procedure checks: MC011A FAIL, MG038A PASS ━━━

Patient scenario: 58M with a 3-week history of progressively worsening exertional dyspnoea now limiting basic activities of daily living, orthopnoea requiring three-pillow positioning, bilateral pitting oedema to mid-shin level, and right hypochondrial heaviness. Echocardiography: dilated left ventricle (end-diastolic dimension 66mm), global hypokinesia, ejection fraction 28%, no regional wall motion abnormality, no significant valvular disease. ECG: left bundle branch block morphology; no ST-segment elevation; no pathological Q waves. Coronary angiography performed as part of ischaemic workup: all three epicardial vessels show mild diffuse luminal irregularities; maximum stenosis 45% in the mid-left anterior descending artery; no dominant obstructive lesion; no total occlusion; TIMI 3 flow throughout. NT-proBNP: 3,800 pg/mL. Serum creatinine: 1.3 mg/dL.

— CHECK A: MC011A — PTCA inclusive of diagnostic angiogram —

STG indications (OR — at least one required):
- Diffuse retrosternal chest pain/heaviness/constriction radiating to arm, neck, or back with diaphoresis ± ST elevation
- History of coronary artery disease or multiple MI risk factors
- Stent-type-specific indications (BMS: artery >3.5mm and lesion <18mm and non-diabetic; DES: diabetic, small arteries, long lesions, left main, venous graft, chronic total occlusion, in-stent restenosis, primary PCI in STEMI)

STG thresholds (AND — all must be met):
- Coronary artery stenosis ≥70% in one or more main vessels on angiography

CORRECT OUTPUT for MC011A:
{
  "eligible": false,
  "missing_criteria": ["Stenosis threshold not met: coronary angiography documents maximum 45% stenosis in mid-LAD with no dominant obstructive lesion — threshold requires ≥70% in at least one main coronary artery"],
  "reasoning": "The patient has multiple cardiovascular risk factors and has undergone coronary angiography, satisfying the indication gate. However, the mandatory angiographic threshold of ≥70% stenosis in at least one main coronary artery is not met. The angiogram explicitly documents a maximum of 45% stenosis in the mid-LAD with no dominant obstructive lesion, no total occlusion, and preserved TIMI 3 flow throughout. The severity of the patient's heart failure symptoms, the markedly depressed ejection fraction, or the clinical urgency do not substitute for angiographic stenosis. A patient can have severely impaired left ventricular function from non-ischaemic dilated cardiomyopathy without meeting the PTCA stenosis threshold. 45% is unambiguously below the 70% boundary.",
  "confidence": "high"
}

— CHECK B: MG038A — Congestive Heart Failure management —

STG indications (OR — at least one required):
- Symptoms from impaired LV myocardial function including dyspnoea and fatigue limiting exercise tolerance, with fluid retention shown by pulmonary or peripheral oedema
- Symptoms including dyspnoea, orthopnoea, oedema, right upper quadrant pain from hepatic congestion, fatigue, and weakness
- Acute/subacute presentation with dyspnoea at rest or exertion, orthopnoea, and paroxysmal nocturnal dyspnoea
- Echocardiogram showing impaired ventricular function and haemodynamics
- Elevated BNP or NT-proBNP levels

STG thresholds: None

CORRECT OUTPUT for MG038A:
{
  "eligible": true,
  "missing_criteria": [],
  "reasoning": "The patient satisfies four independent indications. First, progressively worsening exertional dyspnoea limiting activities of daily living with bilateral pitting oedema directly satisfies the impaired LV function with fluid retention indication. Second, orthopnoea requiring three-pillow positioning and exertional dyspnoea satisfy the acute/subacute presentation indication. Third, echocardiography demonstrates severely impaired ventricular function — ejection fraction 28%, dilated left ventricle, global hypokinesia — satisfying the echo indication. Fourth, NT-proBNP 3,800 pg/mL is markedly elevated, satisfying the biomarker indication. No thresholds exist for this procedure. The concurrent finding of mild non-obstructive coronary artery disease is a comorbidity; the STG lists no contraindications, and it does not block eligibility.",
  "confidence": "high"
}

[Principles demonstrated: Hard numeric threshold blocks one procedure (Principle 2) while a separate procedure with no thresholds passes on breadth of clinical and objective evidence (Principle 5). Multi-condition patients (Principle 11) — the same patient independently qualifies for heart failure management while being ineligible for revascularisation. Comorbidities do not block eligibility (Principle 10).]


━━━ EXAMPLE 5 — Same patient, two procedure checks: SU007A PASS, SU014A PASS ━━━

Patient scenario: 47M with a 3-month background of intermittent right costovertebral angle discomfort and a distinct acute episode 4 days prior comprising severe right lower abdominal pain radiating into the right inguinal region and right hemiscrotum, associated with gross haematuria and two episodes of emesis. Afebrile at presentation. Non-contrast CT KUB: (1) 26mm calculus occupying the right renal pelvis with resultant moderate right hydronephrosis and parenchymal cortical thinning; (2) separately, a 6mm calculus at the right vesicoureteric junction causing distal ureteric obstruction; right upper and mid ureter clear. Left system unremarkable. Urine microscopy: 30–40 red blood cells per high-power field.

— CHECK A: SU007A — PCNL (Percutaneous Nephrolithotomy) —

STG indications (OR — at least one required):
- Flank pain
- Haematuria
- Colic associated with fever, nausea and vomiting
- Renal stone >20mm on IVP/NCCT/CT-IVP

STG thresholds (AND — all must be met):
- Renal stone size >20mm confirmed on NCCT/CT-IVP/IVP

CORRECT OUTPUT for SU007A:
{
  "eligible": true,
  "missing_criteria": [],
  "reasoning": "Two indications are independently satisfied in relation to the renal stone pathology. The 3-month background of right costovertebral angle discomfort constitutes flank pain attributable to the renal pelvic calculus. Urine microscopy demonstrating 30–40 RBCs per HPF confirms haematuria. The mandatory threshold is unambiguously met: CT KUB documents a 26mm calculus in the right renal pelvis — 26mm exceeds the >20mm threshold. This evaluation concerns the renal stone alone. The concurrent VUJ calculus is a separate anatomical finding that does not modify, diminish, or qualify the renal stone's characteristics. PCNL eligibility is established on the renal pelvic stone independently.",
  "confidence": "high"
}

— CHECK B: SU014A — Ureteroscopy with lithotripsy, Lower Ureter —

STG indications (OR — at least one required):
- Colicky pain starting from back and radiating to upper thigh and scrotum/vulva
- Lower ureteric stones presenting with difficulty in passing urine
- Haematuria at any stone location
- Colic associated with nausea and vomiting

STG thresholds: None

CORRECT OUTPUT for SU014A:
{
  "eligible": true,
  "missing_criteria": [],
  "reasoning": "Three indications are independently satisfied in relation to the lower ureteric stone pathology. First, the acute episode of right lower abdominal pain radiating from the lumbar region into the right inguinal region and hemiscrotum is the characteristic radiation pattern of vesicoureteric junction obstruction — this precisely satisfies the 'colicky pain radiating to scrotum' indication. Second, gross haematuria and microscopically confirmed haematuria (30–40 RBCs per HPF) satisfy the haematuria indication. Third, two episodes of emesis during the acute episode satisfy the 'colic with nausea and vomiting' indication. CT imaging confirms a 6mm calculus at the right VUJ — the most distal ureteric segment — placing the stone within the lower ureteric domain. No stone size threshold exists for ureteroscopic stone removal. This evaluation concerns the VUJ stone alone. The concurrent 26mm renal pelvic stone is a separate anatomical pathology addressed by a different procedure; its existence does not block eligibility for lower ureter URS.",
  "confidence": "high"
}

[Principles demonstrated: Multi-condition patients and independent eligibility (Principles 10, 11). A patient with stones at two separate anatomical locations independently satisfies the criteria for two distinct procedures. Each procedure's STG is evaluated against the stone it treats, not the combined picture. Neither procedure's eligibility depends on or is diminished by the other. The non-obvious aspect: the clinically dominant acute presentation was the VUJ episode — a surface-level reader might anchor only on SU014A. The renal pelvic stone causing 3 months of background flank pain independently and unambiguously qualifies for PCNL on the >20mm threshold and must not be overlooked. Evaluate every active pathology independently against each candidate procedure's STG.]

═══════════════════════════════════════════════════════════════
END OF EXAMPLES — Apply all principles above to every live query
═══════════════════════════════════════════════════════════════
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
                    max_output_tokens=16384,
                    response_mime_type="application/json",
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

            print(f"\n=== STG CHECKER RAW RESPONSE (attempt {attempt}) ===\n{raw_text}\n=====================\n")
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
    specialty_code: str,
    specialty_name: str,
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
        specialty_code: The candidate procedure's specialty code.
        specialty_name: The candidate procedure's specialty name.

    Returns:
        dict with keys:
          "plausible" — bool
          "reason"    — str, one-sentence explanation

        On failure after all retries:
          {"plausible": True, "reason": "Plausibility check failed — passed by default"}

    Side effects:
        Logs INFO (result), WARNING (retry), ERROR (all retries failed).
    """
    # _PLAUSIBILITY_SYSTEM = (
    #     "You are a clinical relevance checker for PM-JAY (India's national health "
    #     "scheme) package selection.\n\n"
    #     "Important context: PM-JAY allows multiple packages to be billed for a "
    #     "single admission. A package does not need to cover the patient's entire "
    #     "treatment — it only needs to address one specific condition or procedure "
    #     "that the patient requires during this admission.\n\n"
    #     "Your only task: determine whether the patient has the specific medical "
    #     "condition or clinical need that this PM-JAY procedure is designed to treat. "
    #     "Do not evaluate whether this package alone is sufficient for the patient's "
    #     "complete treatment. Do not consider what other procedures are planned.\n\n"
    #     "Respond with valid JSON only. No prose outside the JSON."
    # )
    
    _PLAUSIBILITY_SYSTEM = (
    "You are a clinical plausibility gate for PM-JAY (India's national health scheme) "
    "package selection. You are the final check before a candidate procedure enters "
    "clinical eligibility validation. "
    
    "Your task: determine whether this candidate PM-JAY procedure is directly "
    "plausible for this patient's CURRENT admission, based on the clinical "
    "information provided. "

    "Rules: "
    "1. If planned_procedure is provided, it is the primary anchor. The candidate "
    "package must match or directly correspond to the stated planned procedure. "
    "A procedure addressing a completely different clinical problem from the "
    "planned procedure is implausible — return plausible=false."
    
    "2. Domain match is required. The candidate procedure must belong to the same "
    "clinical domain as the patient's presenting condition. A procedure from an "
    "unrelated specialty that the clinical input does not reference is implausible."
    
    "3. The check is for THIS admission only. Do not approve a procedure because the "
    "patient might theoretically need it in the future or because a comorbidity "
    "could theoretically lead to it."

    "4. Default to plausible=false when the connection between the patient's "
    "presenting condition and the candidate procedure requires you to construct "
    "a reasoning chain across multiple inferential steps. If the justification "
    "is not direct, the answer is false."
    
    "5. Default to plausible=true only when there is a clear, direct line from at "
    "least one piece of documented clinical evidence to the procedure's purpose."
    
    "Respond with valid JSON only. No prose outside the JSON."
    )
    
    user_prompt = (
        f"Specialty of candidate procedure: {specialty_code} — {specialty_name}\n"
        f"Planned procedure (if stated): {clinical.planned_procedure or 'not stated'}\n"
        f"Provisional diagnosis: {clinical.provisional_diagnosis}\n"
        f"Chief complaints: {clinical.chief_complaints}\n"
        f"History of present illness: {clinical.history_of_present_illness or 'not provided'}\n"
        f"\n"
        f"Candidate PM-JAY package: {procedure_name} ({procedure_code})\n"
        f"\n"
        f"Is this procedure directly plausible for this patient's current admission?\n"
        f"Respond JSON only:\n"
        f'{{"plausible": true or false, "reason": "one sentence citing the specific clinical evidence that justifies or rejects this"}}'
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
                    max_output_tokens=16384,
                    response_mime_type="application/json",
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
        "You are given multiple candidate procedure variants from the same package.\n"
        "Select exactly ONE procedure code that best matches the documented clinical intent.\n\n"
        "CRITICAL RULE — planned_procedure anchor:\n"
        "If the patient's planned_procedure field explicitly names a surgical approach, "
        "technique, or method (e.g. 'laparoscopic', 'open', 'with CBD exploration', "
        "'without CBD exploration', 'off-pump', 'with bypass'), that documented intent "
        "is the authoritative anchor. You MUST select the variant that matches the "
        "documented approach. Do NOT override it based on your own clinical reasoning "
        "about what might be safer or more appropriate given comorbidities or concurrent "
        "conditions. The treating doctor has already made that decision. IRIS's role is "
        "to match the documented intent, not to second-guess it.\n\n"
        "Only when planned_procedure does not specify the approach should you use "
        "clinical parameters (TBSA, size, severity, etc.) to select the best variant.\n\n"
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
        f"Planned procedure: {clinical.planned_procedure or 'not specified'}\n"
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
                    max_output_tokens=16384,
                    response_mime_type="application/json",
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

    comorbidities_str = _format_comorbidities(clinical.comorbidities)

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


def _parse_and_validate(raw_text: str) -> dict | None:
    """Parse LLM response text as JSON and validate required keys.

    Tries three strategies in sequence to parse the JSON.

    Args:
        raw_text: The raw string returned by the Gemini API.

    Returns:
        Validated dict if parsing succeeds and "eligible" (bool) and
        "reasoning" (str) keys are present; None otherwise.
    """
    parsed = None
    strategy_number = 0

    # STRATEGY 1 — Direct parse:
    text_s1 = raw_text.strip()
    try:
        res = json.loads(text_s1)
        if isinstance(res, dict):
            parsed = res
            strategy_number = 1
    except json.JSONDecodeError:
        pass

    # STRATEGY 2 — Strip markdown fences anywhere in text:
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

    # STRATEGY 3 — Extract by brace scanning:
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

    logger.debug("STG checker parsed via strategy %d", strategy_number)

    # VALIDATION
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
