# pyrefly: ignore [missing-import]
import streamlit as st
import json
import logging
import io
import uuid
import datetime
from pathlib import Path

from main import build_session, run_pipeline
from phases.phase10_output import serialize_output
from llm.nearest_match import get_nearest_match
from dataclasses import asdict
from phases.phase11_claim import run_phase11
from phases.phase10_output import _coerce_serialisable
from intake.intake_runner import run_intake_from_bytes, IntakeError

# 1. Page Configuration
st.set_page_config(page_title="IRIS Debug Console", layout="wide")

# 2. Session State Initialisation
if "output_dict" not in st.session_state:
    st.session_state.output_dict = None
if "logs" not in st.session_state:
    st.session_state.logs = ""
if "run_error" not in st.session_state:
    st.session_state.run_error = None
if "num_investigations" not in st.session_state:
    st.session_state.num_investigations = 1
if "nearest_match" not in st.session_state:
    st.session_state.nearest_match = None
if "claim_output_dict" not in st.session_state:
    st.session_state.claim_output_dict = None
if "claim_logs" not in st.session_state:
    st.session_state.claim_logs = ""
if "claim_run_error" not in st.session_state:
    st.session_state.claim_run_error = None
if "claim_intake_error" not in st.session_state:
    st.session_state.claim_intake_error = None

# 3. Log Capture Mechanism
def run_with_log_capture(raw_json: dict):
    log_buffer = io.StringIO()
    handler = logging.StreamHandler(log_buffer)
    handler.setFormatter(
        logging.Formatter("[%(levelname)s][%(name)s] %(message)s")
    )
    handler.setLevel(logging.DEBUG)

    root_logger = logging.getLogger()
    prev_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)

    try:
        session = build_session(raw_json)
        output = run_pipeline(session)
        output_dict = serialize_output(output)
        output_dict["estimated_total_inr"] = getattr(session, "estimated_total_inr", 0)
        nearest_match = None
        if not output_dict.get("selected_packages"):
            nearest_match = get_nearest_match(
                output_dict.get("blocked_candidates", []),
                raw_json.get("clinical", {})
            )
        error_msg = None
    except Exception as e:
        output_dict = None
        error_msg = str(e)
        nearest_match = None
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(prev_level)

    logs = log_buffer.getvalue()
    return output_dict, logs, error_msg, nearest_match


def run_claim_with_log_capture(
    discharge_dict: dict,
    preauth_output_dict: dict,
    preauth_input_dict: dict,
) -> tuple[dict | None, str, str | None]:
    log_buffer = io.StringIO()
    handler = logging.StreamHandler(log_buffer)
    handler.setFormatter(
        logging.Formatter("[%(levelname)s][%(name)s] %(message)s")
    )
    handler.setLevel(logging.DEBUG)
    root_logger = logging.getLogger()
    prev_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)
    try:
        output = run_phase11(
            discharge_dict,
            preauth_output_dict,
            preauth_input_dict,
        )
        output_dict = _coerce_serialisable(asdict(output))
        error_msg = None
    except Exception as e:
        output_dict = None
        error_msg = str(e)
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(prev_level)
    logs = log_buffer.getvalue()
    return output_dict, logs, error_msg

# 4. Title and Header
st.title("🏥 IRIS Debug Console")
st.caption("PM-JAY Pre-Authorisation Engine — Internal Testing Interface")

tab1, tab2, tab3 = st.tabs([
    "⚕ Pre-Auth (Stage 1/2)",
    "📋 Claims Verification (Stage 3)",
    "ℹ About"
])

with tab1:
    # 5. Mode Selector
    mode = st.radio("Mode", ["Test Case Mode", "Manual Input Mode"], horizontal=True)

    raw_json = None
    validation_failed = False

    # 6. Inputs Section
    if mode == "Test Case Mode":
        TC_DIR = Path(__file__).parent / "tests" / "inputs"

        def load_tc_list():
            """Return list of (display_label, file_path) sorted by TC number."""
            tc_files = sorted(TC_DIR.glob("TC*.json"))
            result = []
            for f in tc_files:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    tc_id = data.get("_test_id", f.stem)
                    desc = data.get("_description", "No description")
                    result.append((f"{tc_id} — {desc}", f))
                except Exception:
                    result.append((f.stem, f))
            return result

        tc_list = load_tc_list()

        if not tc_list:
            st.warning("No TC files found in tests/inputs/")
        else:
            labels = [item[0] for item in tc_list]
            selected_idx = st.selectbox("Select Test Case", range(len(labels)),
                                         format_func=lambda i: labels[i])
            selected_path = tc_list[selected_idx][1]
            try:
                tc_data = json.loads(selected_path.read_text(encoding="utf-8"))
            except Exception as e:
                st.error(f"Failed to parse JSON file {selected_path.name}: {e}")
                tc_data = None

            if tc_data:
                # Show info card about this TC
                with st.container(border=True):
                    st.caption("TEST CASE")
                    st.write(tc_data.get("_test_id", "—"))
                    st.caption("DESCRIPTION")
                    st.write(tc_data.get("_description", "—"))
                    st.caption("CLINICAL SNAPSHOT")
                    clinical = tc_data.get("clinical", {})
                    st.write(f"**Chief Complaints:** {clinical.get('chief_complaints', '—')}")
                    st.write(f"**Diagnosis:** {clinical.get('provisional_diagnosis', '—')}")
                    planned = clinical.get("planned_procedure")
                    if planned:
                        st.write(f"**Planned Procedure:** {planned}")
                    patient_id = tc_data.get("patient", {}).get("patient_id", "—")
                    hospital_id = tc_data.get("hospital", {}).get("hospital_id", "—")
                    st.write(f"**Patient ID:** {patient_id} | **Hospital ID:** {hospital_id}")

                raw_json = tc_data

    else:
        # Manual Input Mode
        st.info(
            "🏥 Running as: **P001 — Ravi Kumar** (58M, Tamil Nadu) | "
            "**H001 — Apollo Hospitals Chennai** (Private · Tier 1 · NABH Full · PM-JAY)",
            icon=None
        )

        st.subheader("Core Clinical Information")

        chief_complaints = st.text_area(
            "Chief Complaints *",
            height=100,
            placeholder=(
                "e.g. Left groin swelling and dragging pain for 6 months, "
                "worsening on exertion, reducible on lying down, no bowel complaints"
            ),
            help="Describe the patient's main symptoms as they would appear in the admission note."
        )

        provisional_diagnosis = st.text_area(
            "Provisional Diagnosis *",
            height=68,
            placeholder="e.g. Left indirect inguinal hernia",
            help="Clinical diagnosis at the time of admission."
        )

        planned_procedure = st.text_input(
            "Planned Procedure",
            placeholder="e.g. Laparoscopic inguinal hernioplasty (TEP repair) — leave blank if unknown at admission",
            help="Optional. If known, improves package matching significantly."
        )

        hpi = st.text_area(
            "History of Present Illness",
            height=120,
            placeholder=(
                "e.g. Patient noticed a swelling in left groin 6 months ago, initially small "
                "and reducible. Has gradually increased in size. Associated with dragging pain "
                "on exertion and prolonged standing. Reduces on lying down. No vomiting or "
                "urinary symptoms."
            ),
            help="Richer narrative than chief complaints. Used by the LLM for STG eligibility matching — more detail = better accuracy."
        )

        col_dur, col_adm = st.columns(2)
        with col_dur:
            duration_days = st.number_input(
                "Duration of Illness (days)",
                min_value=0,
                value=0,
                step=1,
                help="0 = same-day onset (e.g. acute emergency). 180 = 6 months."
            )
        with col_adm:
            admission_date = st.date_input(
                "Admission Date",
                value=datetime.date.today(),
                help="Date of hospital admission."
            )

        st.subheader("Admission Flags")
        col1, col2, col3 = st.columns(3)
        with col1:
            is_emergency = st.checkbox(
                "Emergency Admission",
                value=False,
                help="Check if patient was admitted as an emergency case."
            )
        with col2:
            is_medico_legal = st.checkbox(
                "Medico-Legal Case (MLC)",
                value=False,
                help="Check if this is an MLC (accident, assault, burns, poisoning etc). Triggers MLC document requirements."
            )
        with col3:
            bed_category_options = {
                "Not Applicable (surgical/fixed package)": None,
                "Ward": "ward",
                "HDU": "hdu",
                "ICU without Ventilator": "icu_no_vent",
                "ICU with Ventilator": "icu_vent",
            }
            bed_label = st.selectbox(
                "Bed Category",
                options=list(bed_category_options.keys()),
                index=0,
                help="Required for per-day medical packages. Leave as 'Not Applicable' for most surgical cases."
            )
            bed_category = bed_category_options[bed_label]

        st.subheader("Comorbidities")
        comorbidities_text = st.text_input(
            "Active Comorbidities (comma-separated)",
            placeholder=(
                "e.g. type2_diabetes, hypertension, anaemia, hypothyroidism, copd"
            ),
            help=(
                "List active conditions at admission, comma-separated. "
                "Standard values: type2_diabetes, hypertension, anaemia, dyslipidaemia, "
                "hypothyroidism, copd, asthma, ckd, obesity, hyperlipidaemia. "
                "Leave blank if none."
            )
        )
        comorbidities = [c.strip() for c in comorbidities_text.split(",") if c.strip()]

        st.subheader("Investigations")
        st.caption("Add each investigation type separately. Result summary is the human-readable report finding.")

        INVESTIGATION_TYPES = {
            "Blood Reports": "blood_reports",
            "USG (Ultrasound)": "usg",
            "ECG": "ecg",
            "Echocardiogram": "echo",
            "X-Ray": "xray",
            "CT Scan": "ct",
            "MRI": "mri",
            "CAG Report (Coronary Angiography)": "cag_report",
            "HPE (Histopathology)": "hpe",
            "FNAC": "fnac",
            "EEG": "eeg",
            "ABG Chart": "abg_chart",
            "CSF Analysis": "csf",
            "Urine Report": "urine_report",
            "Stool Report": "stool_report",
            "Other": "other",
        }

        investigations = []
        for i in range(st.session_state.num_investigations):
            col_type, col_summary, col_doc = st.columns([2, 5, 1])
            with col_type:
                inv_label = st.selectbox(
                    "Type" if i == 0 else " ",
                    options=list(INVESTIGATION_TYPES.keys()),
                    key=f"inv_type_{i}",
                    label_visibility="visible" if i == 0 else "collapsed"
                )
            with col_summary:
                inv_summary = st.text_input(
                    "Result Summary" if i == 0 else " ",
                    key=f"inv_summary_{i}",
                    placeholder="e.g. Multiple gallstones, largest 1.4 cm, no CBD dilatation",
                    label_visibility="visible" if i == 0 else "collapsed"
                )
            with col_doc:
                inv_doc = st.checkbox(
                    "Doc?" if i == 0 else " ",
                    key=f"inv_doc_{i}",
                    value=False,
                    help="Is the physical document/report currently in hand?",
                    label_visibility="visible" if i == 0 else "collapsed"
                )
            investigations.append({
                "type": INVESTIGATION_TYPES[inv_label],
                "result_summary": inv_summary.strip() if inv_summary.strip() else None,
                "structured_values": None,
                "document_available": inv_doc,
                "report_date": None,
            })

        col_add, col_remove, _ = st.columns([1, 1, 6])
        with col_add:
            if st.button("+ Add Investigation"):
                st.session_state.num_investigations += 1
                st.rerun()
        with col_remove:
            if st.session_state.num_investigations > 1:
                if st.button("− Remove Last"):
                    st.session_state.num_investigations -= 1
                    st.rerun()

        st.subheader("Documents Currently in Hand")
        st.caption("Check all physical documents the MEDCO has in hand right now. Drives the pre-auth document gap analysis.")

        DOCUMENT_OPTIONS = [
            ("clinical_notes",                "Clinical Notes / Admission Notes"),
            ("patient_photo",                 "Patient Photo (on hospital bed)"),
            ("mlc_fir",                       "MLC Copy with FIR Number"),
            ("informed_consent",              "Informed Consent Form"),
            ("referral_letter",               "Referral Letter"),
            ("self_declaration",              "Self Declaration"),
            ("treating_doctor_prescription",  "Treating Doctor Prescription"),
            ("implant_sticker",               "Implant Sticker / Barcode"),
        ]

        DEFAULT_CHECKED = {"clinical_notes", "patient_photo"}

        col_left, col_right = st.columns(2)
        docs_in_hand = []
        for idx, (key, label) in enumerate(DOCUMENT_OPTIONS):
            col = col_left if idx % 2 == 0 else col_right
            with col:
                checked = st.checkbox(label, value=(key in DEFAULT_CHECKED), key=f"doc_{key}")
                docs_in_hand.append({"key": key, "label": label, "available": checked})

        with st.expander("Vitals (optional — improves LLM STG matching for cardiology, ICU cases)"):
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                bp_sys = st.number_input("BP Systolic (mmHg)", min_value=0, max_value=300,
                                          value=0, step=1,
                                          placeholder="e.g. 120", help="0 = not recorded")
                pulse = st.number_input("Pulse (bpm)", min_value=0, max_value=300,
                                         value=0, step=1,
                                         placeholder="e.g. 82", help="0 = not recorded")
            with col2:
                bp_dia = st.number_input("BP Diastolic (mmHg)", min_value=0, max_value=200,
                                          value=0, step=1,
                                          placeholder="e.g. 80", help="0 = not recorded")
                spo2 = st.number_input("SpO2 (%)", min_value=0, max_value=100,
                                        value=0, step=1,
                                        placeholder="e.g. 99", help="0 = not recorded")
            with col3:
                temp = st.number_input("Temperature (°F)", min_value=0.0, max_value=115.0,
                                        value=0.0, step=0.1, format="%.1f",
                                        placeholder="e.g. 98.6", help="0 = not recorded")
                rr = st.number_input("Respiratory Rate (/min)", min_value=0, max_value=100,
                                      value=0, step=1,
                                      placeholder="e.g. 16", help="0 = not recorded")
            with col4:
                gcs = st.number_input("GCS (3–15)", min_value=0, max_value=15,
                                       value=0, step=1,
                                       placeholder="e.g. 15", help="0 = not recorded")

            # Build vitals dict — treat 0 as None (not recorded)
            vitals = {
                "bp_systolic_mmhg": bp_sys if bp_sys > 0 else None,
                "bp_diastolic_mmhg": bp_dia if bp_dia > 0 else None,
                "pulse_bpm": pulse if pulse > 0 else None,
                "spo2_pct": spo2 if spo2 > 0 else None,
                "temperature_f": temp if temp > 0 else None,
                "rr_per_min": rr if rr > 0 else None,
                "gcs": gcs if gcs > 0 else None,
                "blood_glucose_mgdl": None,
            }

        # Validate manual mode fields
        if not chief_complaints.strip() or not provisional_diagnosis.strip():
            validation_failed = True

        raw_json = {
            "session_id": "",  # Will be set with UUID on run
            "created_at": "",  # Will be set on run
            "patient": {"patient_id": "P001"},
            "hospital": {"hospital_id": "H001"},
            "clinical": {
                "admission_date": str(admission_date),
                "bed_category": bed_category,
                "is_emergency": is_emergency,
                "is_medico_legal": is_medico_legal,
                "chief_complaints": chief_complaints.strip(),
                "duration_days": int(duration_days),
                "history_of_present_illness": hpi.strip() if hpi.strip() else None,
                "provisional_diagnosis": provisional_diagnosis.strip(),
                "planned_procedure": planned_procedure.strip() if planned_procedure.strip() else None,
                "weight_kg": None,
                "height_cm": None,
                "vitals": vitals,
                "examination_findings": None,
                "investigations": investigations,
                "comorbidities": comorbidities,
                "past_medical_history": None,
                "past_surgical_history": None,
                "current_medications": [],
                "allergies": [],
                "personal_history": None,
                "family_history": None,
                "non_clinical_documents_in_hand": docs_in_hand,
                "treating_doctor": None,
                "notes": None,
            }
        }

    # 7. Run button
    run_clicked = st.button("▶ Run Pipeline", type="primary", use_container_width=True)

    if run_clicked:
        if mode == "Manual Input Mode" and validation_failed:
            st.error("Chief Complaints and Provisional Diagnosis are required.")
        elif raw_json is None:
            st.error("No valid input data configured.")
        else:
            # Generate session_id and created_at if manual mode
            if mode == "Manual Input Mode":
                raw_json["session_id"] = f"IRIS-MANUAL-{uuid.uuid4().hex[:8].upper()}"
                raw_json["created_at"] = datetime.datetime.now().isoformat()

            with st.spinner("Running IRIS pipeline... (LLM calls may take 10–30 seconds)"):
                output_dict, logs, error_msg, nearest_match = run_with_log_capture(raw_json)
            st.session_state.output_dict = output_dict
            st.session_state.logs = logs
            st.session_state.run_error = error_msg
            st.session_state.nearest_match = nearest_match

    # 8. Output Display
    st.divider()

    if st.session_state.output_dict is None and st.session_state.run_error is None:
        st.info("Select a test case or fill in the clinical form above, then click ▶ Run Pipeline.")
    else:
        if st.session_state.run_error:
            st.error(f"Pipeline Exception: {st.session_state.run_error}")
            # Still render the log expander so the developer can see where it failed.
            with st.expander("Pipeline Logs", expanded=False):
                logs = st.session_state.logs
                if logs.strip():
                    st.code(logs, language=None)
                else:
                    st.write("No logs captured.")
        else:
            try:
                output_dict = st.session_state.output_dict
                
                # 10.1 Status Banner
                STATUS_STYLES = {
                    "READY":                ("success", "🟢 READY"),
                    "READY_WITH_WARNINGS":  ("warning", "🟡 READY WITH WARNINGS"),
                    "CONDITIONAL":          ("warning", "🟠 CONDITIONAL"),
                    "BLOCKED":              ("error",   "🔴 BLOCKED"),
                }
                status = output_dict.get("readiness_status", "UNKNOWN")
                style, label = STATUS_STYLES.get(status, ("info", f"⚪ {status}"))
                getattr(st, style)(f"**Pre-Auth Readiness: {label}**", icon=None)

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Selected Packages", len(output_dict.get("selected_packages", [])))
                col2.metric("Blocked Candidates", len(output_dict.get("blocked_candidates", [])))
                col3.metric("Missing Documents",  len(output_dict.get("preauth_docs_missing", [])))
                col4.metric("Flags Raised",       len(output_dict.get("flags", [])))

                # 10.2 Selected Packages
                st.subheader("Selected Packages")
                packages = output_dict.get("selected_packages", [])
                if not packages:
                    nearest_match = st.session_state.get("nearest_match")
                    if nearest_match is None:
                        st.info("No packages selected. See Blocked Candidates or Flags for reason.")
                    elif not nearest_match.get("is_relevant"):
                        st.warning(
                            "No packages selected. No clinically relevant package was identified "
                            "— USP pathway required."
                        )
                    else:
                        code = nearest_match.get("nearest_code", "?")
                        pkg = nearest_match.get("package_name", "")
                        missing = nearest_match.get("what_is_missing", "reason unavailable")
                        st.warning(
                            f"No packages selected. "
                            f"**Potential Package: {code} — {pkg}**  \n"
                            f"What's missing: {missing}"
                        )
                else:
                    for pkg in packages:
                        validated = pkg.get("validated", {})
                        role = pkg.get("role", "—")
                        factor = pkg.get("deduction_factor", 1.0)
                        group = pkg.get("pre_auth_group", 1)
                        pkg_code = validated.get("package_code", "—")
                        proc_code = validated.get("procedure_code", "—")
                        pkg_name = validated.get("package_name", "—")
                        proc_name = validated.get("procedure_name", "—")
                        billing_type = validated.get("billing_type", "—")
                        base_rate = validated.get("base_rate_inr")
                        specialty = validated.get("specialty", "—")
                        stg_eligible = validated.get("stg_eligible", None)
                        stg_reasoning = validated.get("stg_reasoning", None)
                        enhancement_applicable = validated.get("enhancement_applicable", False)
                        enhancement_requests = validated.get("enhancement_requests_needed", 0)

                        rate_display = f"₹{base_rate:,}" if base_rate is not None else "—"
                        stg_icon = "✅" if stg_eligible else ("❌" if stg_eligible is False else "⚠️")

                        with st.expander(
                            f"{pkg_code} / {proc_code} — {pkg_name}  |  {role.upper()}  |  {rate_display}",
                            expanded=True
                        ):
                            c1, c2, c3, c4 = st.columns(4)
                            c1.markdown(f"**Procedure**\n\n{proc_name}")
                            c2.markdown(f"**Specialty**\n\n{specialty}")
                            c3.markdown(f"**Billing Type**\n\n{billing_type}")
                            c4.markdown(f"**Pre-Auth Group**\n\nGroup {group}")

                            c5, c6, c7, c8 = st.columns(4)
                            c5.markdown(f"**Role**\n\n{role}")
                            c6.markdown(f"**Deduction Factor**\n\n{factor}")
                            c7.markdown(f"**Base Rate**\n\n{rate_display}")
                            c8.markdown(f"**STG Eligible**\n\n{stg_icon}")

                            if stg_reasoning:
                                st.caption(f"STG Reasoning: {stg_reasoning}")
                            if enhancement_applicable:
                                st.caption(
                                    f"Enhancement Applicable — estimated {enhancement_requests} "
                                    f"enhancement request(s) needed"
                                )

                # 10.3 Blocked Candidates
                blocked = output_dict.get("blocked_candidates", [])
                with st.expander(f"Blocked Candidates ({len(blocked)})", expanded=False):
                    if not blocked:
                        st.write("None.")
                    else:
                        for b in blocked:
                            st.markdown(
                                f"- **{b.get('procedure_code', '—')}** — "
                                f"`{b.get('reason_code', '—')}` — {b.get('message', '—')}"
                            )

                # 10.4 Flags
                st.subheader("Pipeline Flags")
                flags = output_dict.get("flags", [])
                if not flags:
                    st.write("No flags raised.")
                else:
                    SEVERITY_ICON = {"block": "🔴", "warning": "🟡", "info": "🔵"}
                    # Group by severity: block first, then warning, then info
                    for severity_order in ["block", "warning", "info"]:
                        group = [f for f in flags if f.get("severity") == severity_order]
                        for flag in group:
                            icon = SEVERITY_ICON.get(flag.get("severity"), "⚪")
                            st.markdown(
                                f"{icon} **{flag.get('code', '—')}** — {flag.get('message', '—')}"
                            )

                # 10.5 Document Checklist
                st.subheader("Pre-Auth Document Checklist")

                col_req, col_miss = st.columns(2)

                with col_req:
                    st.markdown("**All Required Documents**")
                    required = output_dict.get("preauth_docs_required", [])
                    if not required:
                        st.write("None.")
                    for doc in required:
                        available = doc.get("available", False)
                        criticality = doc.get("criticality", "")
                        icon = "✅" if available else ("🔴" if criticality == "hard_block" else "🟡")
                        pkg_tag = f" *(for {doc.get('package_code')})*" if doc.get("package_code") else ""
                        st.markdown(f"{icon} {doc.get('label', doc.get('key', '—'))}{pkg_tag}")

                with col_miss:
                    st.markdown("**Missing Documents**")
                    missing = output_dict.get("preauth_docs_missing", [])
                    if not missing:
                        st.success("All required documents are in hand.")
                    else:
                        for doc in missing:
                            criticality = doc.get("criticality", "")
                            icon = "🔴" if criticality == "hard_block" else "🟡"
                            crit_label = "HARD BLOCK" if criticality == "hard_block" else "PPD Query Risk"
                            pkg_tag = f" *(for {doc.get('package_code')})*" if doc.get("package_code") else ""
                            st.markdown(
                                f"{icon} **{doc.get('label', doc.get('key', '—'))}**{pkg_tag} — `{crit_label}`"
                            )

                # 10.6 Financial Summary
                st.subheader("Financial Summary")
                col_f1, col_f2, col_f3 = st.columns(3)

                estimated = output_dict.get("estimated_total_inr", 0)
                copayment_required = output_dict.get("copayment_required", False)
                copayment_gap = output_dict.get("copayment_gap_inr")

                col_f1.metric("Estimated Package Total", f"₹{estimated:,}")
                col_f2.metric("Co-Payment Required", "Yes ⚠️" if copayment_required else "No ✅")
                if copayment_gap is not None:
                    col_f3.metric("Co-Payment Gap", f"₹{copayment_gap:,}")
                else:
                    col_f3.metric("Co-Payment Gap", "—")

                st.caption(
                    "⚠️ Financial estimate is approximate — uses base rate × deduction factor only. "
                    "Tier/accreditation/geo multipliers and implant costs are not yet applied."
                )

                # 10.7 Enhancement Plan
                enhancement_plan = output_dict.get("enhancement_plan", [])
                if enhancement_plan:
                    st.subheader("Enhancement Plan")
                    for ep in enhancement_plan:
                        st.markdown(
                            f"- **{ep.get('procedure_code', '—')}** — "
                            f"{ep.get('estimated_requests', '—')} request(s) × "
                            f"{ep.get('batch_size_used', '—')} days/request"
                        )
                        if ep.get("caveat"):
                            st.caption(ep["caveat"])

                # 10.8 STG Coverage
                stg_coverage = output_dict.get("stg_coverage", {})
                if stg_coverage:
                    stg_coverage_val = stg_coverage.get("validated", 0)
                    stg_missing_val = stg_coverage.get("stg_missing", 0)
                    st.subheader("STG Coverage")
                    c1, c2 = st.columns(2)
                    c1.metric("STG-Validated Packages", stg_coverage_val)
                    c2.metric("STG File Missing (Plausibility Used)", stg_missing_val)

                # 10.9 Comorbidity Notes
                comorbidity_notes = output_dict.get("comorbidity_notes", [])
                if comorbidity_notes:
                    with st.expander("Comorbidity Notes", expanded=False):
                        for note in comorbidity_notes:
                            st.markdown(f"- {note}")

                # 10.10 Pipeline Logs
                with st.expander("Pipeline Logs", expanded=False):
                    logs = st.session_state.logs
                    if logs.strip():
                        st.code(logs, language=None)
                    else:
                        st.write("No logs captured.")

                # 10.11 Raw JSON Output
                with st.expander("Raw JSON Output", expanded=False):
                    st.json(output_dict)

            except Exception as e:
                st.error(f"Error rendering output: {e}")

with tab2:
    st.subheader("Claims Verification — Stage 3")

    claim_mode = st.radio(
        "Input Mode",
        ["Test Case JSON", "Upload Documents", "Use Stage 1/2 Session Output"],
        horizontal=True,
        key="claim_mode_radio"
    )

    def render_claim_output(co: dict):
        # D1 — STATUS BANNER
        claim_status = co.get("claim_status", "UNKNOWN")
        CLAIM_STATUS_STYLES = {
            "CLAIM_READY":     ("success", "✅ CLAIM READY"),
            "CLAIM_GAPS":      ("warning", "🟡 CLAIM GAPS"),
            "CLAIM_DEVIATION": ("warning", "🟠 CLAIM DEVIATION"),
            "CLAIM_BLOCKED":   ("error",   "🔴 CLAIM BLOCKED"),
        }
        style, label = CLAIM_STATUS_STYLES.get(claim_status, ("info", f"⚪ {claim_status}"))
        getattr(st, style)(f"**{label}** — {co.get('package_name', '')} ({co.get('procedure_code', '')})")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Claim Docs Required", len(co.get("claim_docs_required", [])))
        col2.metric("Docs Missing", len(co.get("claim_docs_missing", [])))
        col3.metric("Deviations", len(co.get("deviations_detected", [])))
        
        cpd_verdict = co.get("cpd_verdict", "unknown").upper()
        CPD_ICONS = {"CLEAN": "✅", "GAPS_PRESENT": "🟡", "LIKELY_DEDUCTED": "🔴"}
        col4.metric("CPD Verdict", CPD_ICONS.get(cpd_verdict, "❓") + " " + cpd_verdict)

        # D2 — TWO COLUMNS
        col_docs, col_cpd = st.columns(2)

        with col_docs:
            st.subheader("Claim Documents")
            for doc in co.get("claim_docs_required", []):
                available = doc.get("available", False)
                criticality = doc.get("criticality", "")
                label = doc.get("label", doc.get("key", "—"))
                pkg = doc.get("package_code")
                pkg_tag = f" *({pkg})*" if pkg else " *(universal)*"
                notes = doc.get("notes")
                if available:
                    icon = "✅"
                elif criticality == "hard_block":
                    icon = "🔴"
                else:
                    icon = "🟡"
                line = f"{icon} {label}{pkg_tag}"
                if notes:
                    line += f" — *{notes}*"
                st.markdown(line)

        with col_cpd:
            st.subheader("CPD Evaluation")
            cpd_verdict = co.get("cpd_verdict", "unknown").upper()
            cpd_summary = co.get("cpd_verdict_summary", "")
            CPD_VERDICT_STYLES = {
                "CLEAN": ("success", "✅ CLEAN"),
                "GAPS_PRESENT": ("warning", "🟡 GAPS PRESENT"),
                "LIKELY_DEDUCTED": ("error", "🔴 LIKELY DEDUCTED"),
            }
            v_style, v_label = CPD_VERDICT_STYLES.get(cpd_verdict, ("info", cpd_verdict))
            getattr(st, v_style)(v_label)
            if cpd_summary:
                st.caption(cpd_summary)

            llm_status = co.get("llm_evaluation_status", "unknown")
            if llm_status != "success":
                st.warning(f"LLM evaluation {llm_status.upper()} — checklist results unavailable")

            for res in co.get("cpd_checklist_results", []):
                risk = res.get("risk_level", "pass")
                question = res.get("question", "")
                reasoning = res.get("reasoning", "")
                if risk == "pass":
                    icon = "✅"
                elif risk == "medium":
                    icon = "🟡"
                elif risk == "high":
                    icon = "🔴"
                else:
                    icon = "⚪"
                st.markdown(f"{icon} {question}")
                if risk in ("medium", "high") and reasoning:
                    st.caption(f"↳ {reasoning}")

        # D3 — DEVIATIONS DETECTED
        st.subheader("Deviations Detected")
        deviations = co.get("deviations_detected", [])
        if not deviations:
            st.success("No deviations detected.")
        else:
            SEVERITY_ICONS = {
                "none": "✅", "info": "ℹ️", "warning": "⚠️", "block": "🔴"
            }
            for dev in deviations:
                severity = dev.get("severity", "warning")
                dev_type = dev.get("deviation_type", "—")
                icon = SEVERITY_ICONS.get(severity, "⚠️")
                with st.container(border=True):
                    st.markdown(f"{icon} **{dev_type.replace('_', ' ').title()}** — severity: `{severity}`")
                    if severity == "none":
                        st.success("Assessed as no real deviation — wording difference only.")
                    else:
                        from_val = dev.get("from_value", "—")
                        to_val = dev.get("to_value", "—")
                        desc = dev.get("description", "")
                        st.markdown(f"**From:** {from_val}")
                        st.markdown(f"**To:** {to_val}")
                        if desc:
                            st.caption(desc)
                        just = dev.get("justification_draft")
                        if just is None:
                            st.caption("📝 Justification draft: pending LLM")
                        elif just.strip() == "":
                            st.caption("📝 No justification required")
                        else:
                            st.info(f"📝 Draft justification: {just}")

        # D4 — THREE COLUMNS
        col_los, col_pay, col_flags = st.columns(3)

        with col_los:
            st.subheader("Length of Stay")
            los_actual = co.get("los_actual", 0)
            los_indicative = co.get("los_approved_indicative", 0)
            c1, c2 = st.columns(2)
            c1.metric("Actual LOS", f"{los_actual} days")
            c2.metric("Indicative LOS", f"{los_indicative} days")
            los_note = co.get("los_deviation_note")
            if los_note:
                st.warning(los_note)
            else:
                st.success("LOS within indicative range.")

        with col_pay:
            st.subheader("Special Payment")
            sp = co.get("special_payment")
            if not sp:
                st.success("None — normal discharge")
            else:
                st.warning(f"**{sp.get('trigger', '—').replace('_', ' ').title()}**")
                st.metric("Payable Amount", f"₹{sp.get('payable_amount_inr', 0):,}")
                st.caption(f"{sp.get('payable_percentage', 0)}% of ₹{sp.get('base_package_rate_inr', 0):,}")
                st.caption(sp.get("computation_note", ""))

        with col_flags:
            st.subheader("Audit Flags")
            audit_flags = co.get("audit_flags_triggered", [])
            sha_warning = co.get("sha_notification_warning")
            if not audit_flags and not sha_warning:
                st.success("No audit flags triggered.")
            else:
                for flag in audit_flags:
                    st.error(f"🚩 {flag}")
                if sha_warning:
                    st.warning(f"⏰ SHA: {sha_warning}")

        # D5 — SPECIALTY NOTES + IMAGE DOCS
        col_spec, col_img = st.columns(2)

        with col_spec:
            st.subheader("Specialty Notes")
            notes = co.get("specialty_specific_notes", [])
            if not notes:
                st.write("None.")
            else:
                for note in notes:
                    st.info(note)

        with col_img:
            st.subheader("Image Docs — Upload to TMS")
            reminders = co.get("image_docs_reminder", [])
            if not reminders:
                st.write("None required.")
            else:
                for item in reminders:
                    st.markdown(f"• {item}")

        # D6 — STEP 0 CONSISTENCY FLAGS
        flags = co.get("flags", [])
        if flags:
            with st.expander(f"⚠️ Step 0 Consistency Flags ({len(flags)})", expanded=True):
                SEVERITY_ICON = {"block": "🔴", "warning": "🟡", "info": "🔵"}
                for flag in flags:
                    sev = flag.get("severity", "info")
                    icon = SEVERITY_ICON.get(sev, "⚪")
                    st.markdown(f"{icon} **{flag.get('code', '—')}** — {flag.get('message', '—')}")

        # D7 — ERRORS
        errors = co.get("errors", [])
        if errors:
            with st.expander(f"Pipeline Errors ({len(errors)})", expanded=True):
                for err in errors:
                    st.error(err)

        # D8 — COLLAPSIBLE EXPANDERS
        with st.expander("Pipeline Logs", expanded=False):
            logs = st.session_state.claim_logs
            if logs and logs.strip():
                st.code(logs, language=None)
            else:
                st.write("No logs captured.")

        with st.expander("Raw JSON Output", expanded=False):
            st.json(co)

    if claim_mode == "Test Case JSON":
        DISCHARGE_DIR = Path(__file__).parent / "tests" / "inputs"
        discharge_files = sorted(DISCHARGE_DIR.glob("TC*_discharge.json"))
        if not discharge_files:
            st.warning("No TC*_discharge.json files found in tests/inputs/")
        else:
            labels_list = []
            file_map = {}
            for f in discharge_files:
                try:
                    with open(f, "r", encoding="utf-8") as file_obj:
                        data = json.load(file_obj)
                    test_id = data.get("_test_id", f.stem)
                    desc = data.get("_description", "No description")
                    if len(desc) > 80:
                        desc = desc[:80] + "..."
                    label = f"{test_id} — {desc}"
                except Exception:
                    label = f.stem
                labels_list.append(label)
                file_map[label] = f

            selected_discharge_label = st.selectbox(
                "Select Discharge Test Case",
                options=labels_list,
                key="discharge_tc_selector"
            )
            
            selected_file = file_map[selected_discharge_label]
            try:
                with open(selected_file, "r", encoding="utf-8") as file_obj:
                    discharge_dict = json.load(file_obj)
            except Exception as e:
                st.error(f"Failed to parse JSON file {selected_file.name}: {e}")
                discharge_dict = None

            if discharge_dict:
                with st.container(border=True):
                    col_left, col_mid, col_right = st.columns(3)
                    
                    with col_left:
                        st.caption("PATIENT")
                        patient = discharge_dict.get("patient", {})
                        st.write(f"**{patient.get('name', '—')}**")
                        st.write(f"ID: {patient.get('pmjay_id', '—')} | Age: {patient.get('age', '—')} | {patient.get('gender', '—')}")

                    with col_mid:
                        st.caption("ADMISSION")
                        admission = discharge_dict.get("admission", {})
                        st.write(f"{admission.get('date_of_admission', '—')} → {admission.get('date_of_discharge', '—')}")
                        st.write(f"LOS: {admission.get('actual_los_days', '—')} days | Status: {admission.get('discharge_status', '—')}")
                        st.write(f"Package: {admission.get('package_booked', '—')}")

                    with col_right:
                        st.caption("PROCEDURE")
                        clinical = discharge_dict.get("clinical", {})
                        st.write(clinical.get("final_procedure_performed", "—"))
                        st.caption("HOSPITAL")
                        hospital = discharge_dict.get("hospital", {})
                        st.write(hospital.get("name", "—"))

                test_id = selected_file.stem.replace("_discharge", "")
                cache_path = Path(__file__).parent / "tests" / "outputs" / f"{test_id}_output.json"
                
                preauth_input_val = discharge_dict.get("preauth_input_path")
                if preauth_input_val:
                    preauth_input_path = Path(__file__).parent / preauth_input_val
                else:
                    preauth_input_path = None

                if not cache_path.exists():
                    st.warning(
                        f"Pre-auth cache not found at {cache_path}. "
                        f"Run Stage 1/2 for {test_id} first (python main.py tests/inputs/{test_id}.json), "
                        f"then return here."
                    )
                    st.button("▶ Run Claims Verification",
                        type="primary", use_container_width=True, disabled=True)
                else:
                    try:
                        with open(cache_path, "r", encoding="utf-8") as f_cache:
                            preauth_output_dict = json.load(f_cache)
                    except Exception as e:
                        st.error(f"Failed to load cache from {cache_path}: {e}")
                        preauth_output_dict = {}

                    preauth_input_dict = {}
                    if preauth_input_path and preauth_input_path.exists():
                        try:
                            with open(preauth_input_path, "r", encoding="utf-8") as f_in:
                                preauth_input_dict = json.load(f_in)
                        except Exception:
                            preauth_input_dict = {}

                    claim_run_clicked = st.button("▶ Run Claims Verification",
                        type="primary", use_container_width=True, key="claim_run_btn")

                    if claim_run_clicked:
                        with st.spinner("Running Stage 3 claims verification... (LLM call may take 10–30 seconds)"):
                            claim_output_dict, claim_logs, claim_error = run_claim_with_log_capture(
                                discharge_dict,
                                preauth_output_dict,
                                preauth_input_dict,
                            )
                        st.session_state.claim_output_dict = claim_output_dict
                        st.session_state.claim_logs = claim_logs
                        st.session_state.claim_run_error = claim_error

                    if st.session_state.claim_output_dict is None and st.session_state.claim_run_error is None:
                        st.info("Select a discharge test case and click ▶ Run Claims Verification.")
                    else:
                        if st.session_state.claim_run_error:
                            st.error(f"Stage 3 Exception: {st.session_state.claim_run_error}")
                            with st.expander("Pipeline Logs", expanded=False):
                                st.code(st.session_state.claim_logs or "No logs.", language=None)
                        elif st.session_state.claim_output_dict is not None:
                            render_claim_output(st.session_state.claim_output_dict)

    elif claim_mode == "Upload Documents":
        case_id_input = st.text_input(
            "Case ID",
            placeholder="e.g. TC_CASE1 or TC14",
            help=(
                "Must match the pre-auth output cache filename: "
                "tests/outputs/{Case ID}_output.json. "
                "Leave blank to run without pre-auth cross-check."
            ),
            key="upload_case_id"
        )

        uploaded_files = st.file_uploader(
            "Upload Discharge Documents (PDF or DOCX)",
            type=["pdf", "docx"],
            accept_multiple_files=True,
            key="discharge_uploader",
            help="Upload one or more documents. The LLM will parse all of them together."
        )

        if uploaded_files:
            for f in uploaded_files:
                size_kb = len(f.getvalue()) / 1024.0
                st.caption(f"📄 {f.name} ({size_kb:.1f} KB)")

        preauth_output_dict = None
        cache_found = False
        if case_id_input.strip():
            cache_path = Path(__file__).parent / "tests" / "outputs" / f"{case_id_input.strip()}_output.json"
            if cache_path.exists():
                try:
                    with open(cache_path, "r", encoding="utf-8") as f_cache:
                        preauth_output_dict = json.load(f_cache)
                    cache_found = True
                    st.success(f"Pre-auth cache found for {case_id_input.strip()}")
                except Exception as e:
                    st.error(f"Failed to load cache from {cache_path}: {e}")
            else:
                st.warning(
                    f"No pre-auth cache found at {cache_path}. "
                    f"Stage 3 will run without cross-consistency checks."
                )
        else:
            st.info(
                "No Case ID provided. Stage 3 will run without pre-auth "
                "cross-consistency checks."
            )

        effective_case_id = case_id_input.strip() if case_id_input.strip() else "UPLOAD_SESSION"

        run_disabled = not bool(uploaded_files)
        claim_run_clicked = st.button(
            "▶ Run Claims Verification",
            type="primary",
            use_container_width=True,
            key="claim_run_upload_btn",
            disabled=run_disabled
        )

        if claim_run_clicked and uploaded_files:
            files_payload = []
            for f in uploaded_files:
                files_payload.append({
                    "filename": f.name,
                    "bytes": f.read(),
                    "suffix": Path(f.name).suffix.lower()
                })

            with st.spinner("Running intake layer... extracting and parsing documents"):
                intake_log_buffer = io.StringIO()
                intake_handler = logging.StreamHandler(intake_log_buffer)
                intake_handler.setFormatter(
                    logging.Formatter("[%(levelname)s][%(name)s] %(message)s")
                )
                intake_handler.setLevel(logging.DEBUG)
                root_logger = logging.getLogger()
                prev_level = root_logger.level
                root_logger.setLevel(logging.DEBUG)
                root_logger.addHandler(intake_handler)

                try:
                    parsed_discharge_dict = run_intake_from_bytes(
                        files_payload,
                        effective_case_id,
                        preauth_output_dict,
                    )
                    intake_succeeded = True
                    st.session_state.claim_intake_error = None
                except IntakeError as e:
                    intake_succeeded = False
                    parsed_discharge_dict = None
                    st.session_state.claim_output_dict = None
                    st.session_state.claim_intake_error = str(e)
                except Exception as e:
                    intake_succeeded = False
                    parsed_discharge_dict = None
                    st.session_state.claim_output_dict = None
                    st.session_state.claim_intake_error = f"Unexpected error: {e}"
                finally:
                    root_logger.removeHandler(intake_handler)
                    root_logger.setLevel(prev_level)

                st.session_state.claim_logs = intake_log_buffer.getvalue()

            if intake_succeeded:
                st.success("Documents parsed successfully. Running Stage 3...")
                preauth_input_dict = {}
                if case_id_input.strip():
                    preauth_input_path = Path(__file__).parent / "tests" / "inputs" / f"{case_id_input.strip()}.json"
                    if preauth_input_path.exists():
                        try:
                            with open(preauth_input_path, "r", encoding="utf-8") as f_in:
                                preauth_input_dict = json.load(f_in)
                        except Exception:
                            pass

                with st.spinner("Running Stage 3 claims verification..."):
                    claim_output_dict, claim_logs, claim_error = run_claim_with_log_capture(
                        parsed_discharge_dict,
                        preauth_output_dict or {},
                        preauth_input_dict,
                    )
                st.session_state.claim_output_dict = claim_output_dict
                st.session_state.claim_logs += "\n" + (claim_logs or "")
                st.session_state.claim_run_error = claim_error

        if st.session_state.claim_intake_error:
            st.error(f"Intake failed: {st.session_state.claim_intake_error}")
        if st.session_state.claim_run_error:
            st.error(f"Stage 3 failed: {st.session_state.claim_run_error}")

        if st.session_state.claim_output_dict is not None:
            render_claim_output(st.session_state.claim_output_dict)

    elif claim_mode == "Use Stage 1/2 Session Output":
        preauth_available = (
            st.session_state.output_dict is not None
            and st.session_state.output_dict.get("selected_packages")
        )
        if not preauth_available:
            st.warning(
                "No Stage 1/2 output in this session. "
                "Go to the Pre-Auth tab, run the pipeline for your case, "
                "then return here."
            )
        else:
            selected = st.session_state.output_dict.get("selected_packages", [])
            if selected:
                proc_code = selected[0].get("validated", {}).get("procedure_code", "")
                pkg_name = selected[0].get("validated", {}).get("package_name", "")
                st.success(
                    f"Stage 1/2 output loaded from session: "
                    f"{proc_code} — {pkg_name}"
                )

            uploaded_files_s3 = st.file_uploader(
                "Upload Discharge Documents (PDF or DOCX)",
                type=["pdf", "docx"],
                accept_multiple_files=True,
                key="discharge_uploader_session",
                help="Upload discharge documents to verify against the Stage 1/2 result."
            )

            if uploaded_files_s3:
                for f in uploaded_files_s3:
                    size_kb = len(f.getvalue()) / 1024.0
                    st.caption(f"📄 {f.name} ({size_kb:.1f} KB)")

            session_case_id = (
                st.session_state.output_dict
                .get("session_id", "SESSION_CASE")
                or "SESSION_CASE"
            )
            st.caption(f"Session case ID: {session_case_id}")

            run_disabled_s3 = not bool(uploaded_files_s3)
            claim_run_s3 = st.button(
                "▶ Run Claims Verification",
                type="primary",
                use_container_width=True,
                key="claim_run_session_btn",
                disabled=run_disabled_s3
            )

            if claim_run_s3 and uploaded_files_s3:
                files_payload = []
                for f in uploaded_files_s3:
                    files_payload.append({
                        "filename": f.name,
                        "bytes": f.read(),
                        "suffix": Path(f.name).suffix.lower()
                    })

                with st.spinner("Running intake layer... extracting and parsing documents"):
                    intake_log_buffer = io.StringIO()
                    intake_handler = logging.StreamHandler(intake_log_buffer)
                    intake_handler.setFormatter(
                        logging.Formatter("[%(levelname)s][%(name)s] %(message)s")
                    )
                    intake_handler.setLevel(logging.DEBUG)
                    root_logger = logging.getLogger()
                    prev_level = root_logger.level
                    root_logger.setLevel(logging.DEBUG)
                    root_logger.addHandler(intake_handler)

                    try:
                        parsed_discharge_dict = run_intake_from_bytes(
                            files_payload,
                            session_case_id,
                            st.session_state.output_dict,
                        )
                        intake_succeeded = True
                        st.session_state.claim_intake_error = None
                    except IntakeError as e:
                        intake_succeeded = False
                        parsed_discharge_dict = None
                        st.session_state.claim_output_dict = None
                        st.session_state.claim_intake_error = str(e)
                    except Exception as e:
                        intake_succeeded = False
                        parsed_discharge_dict = None
                        st.session_state.claim_output_dict = None
                        st.session_state.claim_intake_error = f"Unexpected error: {e}"
                    finally:
                        root_logger.removeHandler(intake_handler)
                        root_logger.setLevel(prev_level)

                    st.session_state.claim_logs = intake_log_buffer.getvalue()

                if intake_succeeded:
                    st.success("Documents parsed successfully. Running Stage 3...")
                    preauth_input_dict = {}

                    with st.spinner("Running Stage 3 claims verification..."):
                        claim_output_dict, claim_logs, claim_error = run_claim_with_log_capture(
                            parsed_discharge_dict,
                            st.session_state.output_dict,
                            preauth_input_dict,
                        )
                    st.session_state.claim_output_dict = claim_output_dict
                    st.session_state.claim_logs += "\n" + (claim_logs or "")
                    st.session_state.claim_run_error = claim_error

            if st.session_state.claim_intake_error:
                st.error(f"Intake failed: {st.session_state.claim_intake_error}")
            if st.session_state.claim_run_error:
                st.error(f"Stage 3 failed: {st.session_state.claim_run_error}")

            if st.session_state.claim_output_dict is not None:
                render_claim_output(st.session_state.claim_output_dict)

with tab3:
    st.markdown("### IRIS — Intelligence for Rules and Integration of Schemes")
    st.markdown(
        "IRIS is a PM-JAY pre-authorization and claims verification engine "
        "built for 1Hat, an AI-powered healthcare startup. It processes "
        "admission data through an 11-phase pre-authorization pipeline and a "
        "12-step post-discharge claims verification pipeline."
    )
    st.markdown("**Stage 1/2** — Package selection and pre-auth readiness check.")
    st.markdown("**Stage 3** — Post-discharge claims verification and CPD audit.")
    st.caption("Internal testing interface. Not for production use.")
