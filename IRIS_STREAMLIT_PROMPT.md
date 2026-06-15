# IRIS DEBUG CONSOLE — Streamlit App Build Specification
### For Antigravity IDE — Build `app.py` in project root

---

## 1. WHAT YOU ARE BUILDING

A single-file Streamlit debug console (`app.py`) for an internal AI pipeline called
IRIS. IRIS is a PM-JAY (Indian government health scheme) pre-authorisation
recommendation engine. The app lets an internal mentor/developer:

1. Select a pre-built test case and run the IRIS pipeline against it, OR
2. Manually enter clinical information and run the pipeline against it

After running, the app displays the pipeline output in a structured, readable
format — including the final recommendation status, selected packages, flags
raised, document checklist, and captured logs.

This is an internal debug tool. Clean and functional. No animations, no marketing
copy, no decorative elements. Prioritise clarity.

---

## 2. TECH STACK AND FILE PLACEMENT

- **Framework:** Streamlit only. One file: `app.py` placed in the project root
  directory (same level as `main.py`, `config.py`, `session.py`, etc.)
- **Python:** 3.11+
- **No new dependencies.** Only use: `streamlit`, `logging`, `io`, `json`,
  `pathlib`, `datetime`, `uuid` — all standard library or already installed.
- **Run command:** `streamlit run app.py` from the project root.
- **Page config:** wide layout, title "IRIS Debug Console", no sidebar.

---

## 3. HOW THE PIPELINE IS INVOKED (critical — read carefully)

Do NOT use `subprocess` or shell calls to invoke the pipeline.
Import and call Python functions directly.

```python
from main import build_session, run_pipeline
from phases.phase10_output import serialize_output
```

`build_session(raw_json: dict) -> IRISSession`
`run_pipeline(session: IRISSession) -> IRISOutput`
`serialize_output(output: IRISOutput) -> dict`

Call sequence:
```python
session = build_session(raw_json)
output = run_pipeline(session)
output_dict = serialize_output(output)
```

`output_dict` is a plain Python dict — display it directly.

---

## 4. LOG CAPTURE MECHANISM

The pipeline uses Python's `logging` module internally. Capture logs for display
WITHOUT calling `setup_logging()` from `logger_setup.py` (that function writes to
stdout which would pollute Streamlit output).

Use this exact pattern every time the pipeline is run:

```python
import logging
import io

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
        error_msg = None
    except Exception as e:
        output_dict = None
        error_msg = str(e)
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(prev_level)

    logs = log_buffer.getvalue()
    return output_dict, logs, error_msg
```

Call `run_with_log_capture(raw_json)` on every Run button press.

---

## 5. PAGE LAYOUT OVERVIEW

```
[IRIS Debug Console]  ← st.title

[Mode selector: radio — "Test Case Mode" | "Manual Input Mode"]

━━━ if Test Case Mode ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[TC dropdown]
[TC description card]
[▶ Run Pipeline button]

━━━ if Manual Input Mode ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Patient/Hospital info label (static)]
[Section A: Core Clinical]
[Section B: Admission Flags]
[Section C: Comorbidities]
[Section D: Investigations (dynamic rows)]
[Section E: Documents in Hand]
[Section F: Vitals (collapsible expander)]
[▶ Run Pipeline button]

━━━ Output (rendered after run) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Status banner]
[Selected Packages]
[Blocked Candidates]
[Flags]
[Document Checklist]
[Financial Summary]
[Enhancement Plan]
[STG Coverage]
[Pipeline Logs  ← expander]
[Raw JSON Output ← expander]
```

Use `st.divider()` between the input area and the output area.

---

## 6. SESSION STATE INITIALISATION

At the top of the app (before any UI code), initialise session state:

```python
import streamlit as st

if "output_dict" not in st.session_state:
    st.session_state.output_dict = None
if "logs" not in st.session_state:
    st.session_state.logs = ""
if "run_error" not in st.session_state:
    st.session_state.run_error = None
if "num_investigations" not in st.session_state:
    st.session_state.num_investigations = 1
```

---

## 7. TC MODE — DETAILED SPEC

### 7.1 Loading TC Files

TC files live at `tests/inputs/` relative to the project root.
Load them like this:

```python
from pathlib import Path

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
```

### 7.2 TC Mode UI

```
tc_list = load_tc_list()

if not tc_list:
    st.warning("No TC files found in tests/inputs/")
else:
    labels = [item[0] for item in tc_list]
    selected_idx = st.selectbox("Select Test Case", range(len(labels)),
                                 format_func=lambda i: labels[i])
    selected_path = tc_list[selected_idx][1]
    tc_data = json.loads(selected_path.read_text(encoding="utf-8"))

    # Show info card about this TC
    with st.container(border=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.caption("TEST CASE")
            st.write(tc_data.get("_test_id", "—"))
        with col2:
            st.caption("EXPECTED STATUS")
            st.write(tc_data.get("_expected_status", "—"))
        with col3:
            st.caption("EXPECTED SPECIALTY")
            st.write(tc_data.get("_expected_specialty", "—"))
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
```

When Run is clicked in TC mode, pass `tc_data` directly to `run_with_log_capture(tc_data)`.
The TC JSON already has the correct structure with patient_id and hospital_id — pass it as-is.

---

## 8. MANUAL INPUT MODE — DETAILED SPEC

### 8.1 Static Patient/Hospital Header

Show this as a static info box at the top of the manual form.
Do NOT make it editable.

```python
st.info(
    "🏥 Running as: **P001 — Ravi Kumar** (58M, Tamil Nadu) | "
    "**H001 — Apollo Hospitals Chennai** (Private · Tier 1 · NABH Full · PM-JAY)",
    icon=None
)
```

The pipeline will use `patient_id = "P001"` and `hospital_id = "H001"` hardcoded.

### 8.2 Section A — Core Clinical

Label: `"### Core Clinical Information"`

**Field 1 — Chief Complaints**
```python
chief_complaints = st.text_area(
    "Chief Complaints *",
    height=100,
    placeholder=(
        "e.g. Left groin swelling and dragging pain for 6 months, "
        "worsening on exertion, reducible on lying down, no bowel complaints"
    ),
    help="Describe the patient's main symptoms as they would appear in the admission note."
)
```

**Field 2 — Provisional Diagnosis**
```python
provisional_diagnosis = st.text_area(
    "Provisional Diagnosis *",
    height=68,
    placeholder="e.g. Left indirect inguinal hernia",
    help="Clinical diagnosis at the time of admission."
)
```

**Field 3 — Planned Procedure**
```python
planned_procedure = st.text_input(
    "Planned Procedure",
    placeholder="e.g. Laparoscopic inguinal hernioplasty (TEP repair) — leave blank if unknown at admission",
    help="Optional. If known, improves package matching significantly."
)
```

**Field 4 — History of Present Illness (HPI)**
```python
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
```

**Field 5 — Duration of Illness**
Use two columns:
```python
col1, col2 = st.columns(2)
with col1:
    duration_days = st.number_input(
        "Duration of Illness (days)",
        min_value=0,
        value=0,
        step=1,
        help="0 = same-day onset (e.g. acute emergency). 180 = 6 months."
    )
with col2:
    admission_date = st.date_input(
        "Admission Date",
        value=datetime.date.today(),
        help="Date of hospital admission."
    )
```

### 8.3 Section B — Admission Flags

Label: `"### Admission Flags"`

Use three columns:
```python
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
```

### 8.4 Section C — Comorbidities

Label: `"### Comorbidities"`

```python
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
# Parse into list:
comorbidities = [c.strip() for c in comorbidities_text.split(",") if c.strip()]
```

### 8.5 Section D — Investigations (Dynamic Rows)

Label: `"### Investigations"`
Add a small caption: `st.caption("Add each investigation type separately. Result summary is the human-readable report finding.")`

Use `st.session_state.num_investigations` to control number of rows.

**Investigation type options** (display label → JSON value):
```python
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
```

Render each row as three columns: [Type dropdown | Result Summary text input | Doc Available checkbox]:
```python
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
```

Below the rows, add two buttons in a row:
```python
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
```

### 8.6 Section E — Documents in Hand

Label: `"### Documents Currently in Hand"`
Caption: `st.caption("Check all physical documents the MEDCO has in hand right now. Drives the pre-auth document gap analysis.")`

Use two columns of checkboxes. These are `non_clinical_documents_in_hand`.

```python
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
```

### 8.7 Section F — Vitals (Collapsible)

```python
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
```

### 8.8 Building the Manual Mode JSON

When Run is clicked in Manual mode, assemble the raw_json:

```python
import uuid

raw_json = {
    "session_id": f"IRIS-MANUAL-{uuid.uuid4().hex[:8].upper()}",
    "created_at": datetime.datetime.now().isoformat(),
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
```

**Validation before running:** Check that `chief_complaints` and
`provisional_diagnosis` are non-empty. If either is blank, show
`st.error("Chief Complaints and Provisional Diagnosis are required.")` and do NOT
run the pipeline. Do not use `st.form` — use a plain `st.button`.

---

## 9. RUN BUTTON

In both modes, the Run button is:
```python
run_clicked = st.button("▶ Run Pipeline", type="primary", use_container_width=True)
```

When clicked:
```python
if run_clicked:
    with st.spinner("Running IRIS pipeline... (LLM calls may take 10–30 seconds)"):
        output_dict, logs, error_msg = run_with_log_capture(raw_json)
    st.session_state.output_dict = output_dict
    st.session_state.logs = logs
    st.session_state.run_error = error_msg
```

After storing in session_state, immediately fall through to the output rendering
section (which checks `st.session_state.output_dict is not None`).

---

## 10. OUTPUT DISPLAY — DETAILED SPEC

Render output only when `st.session_state.output_dict is not None`.

```python
if st.session_state.run_error:
    st.error(f"Pipeline Exception: {st.session_state.run_error}")
    # Still show logs below even on error
```

Otherwise render all sections below in order.

### 10.1 Status Banner

```python
STATUS_STYLES = {
    "READY":                ("success", "🟢 READY"),
    "READY_WITH_WARNINGS":  ("warning", "🟡 READY WITH WARNINGS"),
    "CONDITIONAL":          ("warning", "🟠 CONDITIONAL"),
    "BLOCKED":              ("error",   "🔴 BLOCKED"),
}
status = output_dict.get("readiness_status", "UNKNOWN")
style, label = STATUS_STYLES.get(status, ("info", f"⚪ {status}"))
getattr(st, style)(f"**Pre-Auth Readiness: {label}**", icon=None)
```

Then show a quick 4-column metric summary:
```python
col1, col2, col3, col4 = st.columns(4)
col1.metric("Selected Packages", len(output_dict.get("selected_packages", [])))
col2.metric("Blocked Candidates", len(output_dict.get("blocked_candidates", [])))
col3.metric("Missing Documents",  len(output_dict.get("preauth_docs_missing", [])))
col4.metric("Flags Raised",       len(output_dict.get("flags", [])))
```

### 10.2 Selected Packages

```python
st.subheader("Selected Packages")
packages = output_dict.get("selected_packages", [])
if not packages:
    st.info("No packages selected. See Blocked Candidates or Flags for reason.")
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

        rate_display = f"₹{base_rate:,}" if base_rate else "—"
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
```

### 10.3 Blocked Candidates

```python
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
```

### 10.4 Flags

```python
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
```

### 10.5 Document Checklist

```python
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
```

### 10.6 Financial Summary

```python
st.subheader("Financial Summary")
col1, col2, col3 = st.columns(3)

estimated = output_dict.get("estimated_total_inr", 0)
copayment_required = output_dict.get("copayment_required", False)
copayment_gap = output_dict.get("copayment_gap_inr")

col1.metric("Estimated Package Total", f"₹{estimated:,}")
col2.metric("Co-Payment Required", "Yes ⚠️" if copayment_required else "No ✅")
if copayment_gap:
    col3.metric("Co-Payment Gap", f"₹{copayment_gap:,}")
else:
    col3.metric("Co-Payment Gap", "—")

st.caption(
    "⚠️ Financial estimate is approximate — uses base rate × deduction factor only. "
    "Tier/accreditation/geo multipliers and implant costs are not yet applied."
)
```

### 10.7 Enhancement Plan

```python
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
```

### 10.8 STG Coverage

```python
stg_coverage = output_dict.get("stg_coverage", {})
if stg_coverage:
    st.subheader("STG Coverage")
    c1, c2 = st.columns(2)
    c1.metric("STG-Validated Packages", stg_coverage.get("validated", 0))
    c2.metric("STG File Missing (Plausibility Used)", stg_coverage.get("stg_missing", 0))
```

### 10.9 Comorbidity Notes

```python
comorbidity_notes = output_dict.get("comorbidity_notes", [])
if comorbidity_notes:
    with st.expander("Comorbidity Notes", expanded=False):
        for note in comorbidity_notes:
            st.markdown(f"- {note}")
```

### 10.10 Pipeline Logs

```python
with st.expander("Pipeline Logs", expanded=False):
    logs = st.session_state.logs
    if logs.strip():
        st.code(logs, language=None)
    else:
        st.write("No logs captured.")
```

### 10.11 Raw JSON Output

```python
with st.expander("Raw JSON Output", expanded=False):
    st.json(output_dict)
```

---

## 11. ERROR HANDLING RULES

- If `run_with_log_capture` returns `error_msg is not None`:
  Show `st.error(f"Pipeline crashed: {error_msg}")` AND still render the log
  expander so the developer can see where it failed.
- If TC directory doesn't exist or is empty: `st.warning("No test cases found.")`
- If manual mode fields are blank (chief_complaints or provisional_diagnosis):
  `st.error("Chief Complaints and Provisional Diagnosis are required.")` — do not
  run pipeline.
- Wrap all output rendering in a try/except so a bad output_dict doesn't crash
  the whole page.

---

## 12. IMPORTS AT THE TOP OF app.py

```python
import streamlit as st
import json
import logging
import io
import uuid
import datetime
from pathlib import Path

from main import build_session, run_pipeline
from phases.phase10_output import serialize_output
```

---

## 13. COMPLETE FILE STRUCTURE EXPECTED

```
1hat-phase1/
├── app.py           ← THE FILE YOU ARE CREATING
├── main.py
├── config.py
├── session.py
├── models.py
├── ...
└── tests/
    └── inputs/
        ├── TC01.json
        ├── TC02.json
        ...
        └── TC15.json
```

`app.py` must import from `main.py` using a direct import (`from main import ...`),
NOT `from 1hat-phase1.main import ...`. The working directory when running
`streamlit run app.py` from the project root makes all sibling modules importable
directly.

---

## 14. FINAL UX RULES

1. `st.set_page_config(page_title="IRIS Debug Console", layout="wide")`
2. Page title: `st.title("🏥 IRIS Debug Console")` with subtitle
   `st.caption("PM-JAY Pre-Authorisation Engine — Internal Testing Interface")`
3. Mode selector immediately below title:
   `mode = st.radio("Mode", ["Test Case Mode", "Manual Input Mode"], horizontal=True)`
4. Use `st.divider()` between the input form and the output section.
5. When no run has been executed yet, show:
   `st.info("Select a test case or fill in the clinical form above, then click ▶ Run Pipeline.")` where the output section would appear.
6. The Run button should always be clearly visible — place it just above the divider,
   full width, primary styled.
7. Do NOT use `st.form()` anywhere. Use plain widgets and a plain button.
8. Do NOT use `st.sidebar`. Everything on the main page.
9. Keep all section headers consistent: `st.subheader(...)` for major sections.
10. After a successful run, keep the input form visible above so the mentor can
    modify fields and re-run without scrolling back up. The output renders below.
```
