# PM-JAY / Government-Scheme KB Specification

This document is the authoritative spec for the knowledge bases that power the IRIS
government-scheme flow (PM-JAY national + state variants like CMCHIS). It defines
**what each JSON must contain, how the files are organised (code-wise vs condition-wise),
and how the package picker uses them without reading the whole corpus.**

Sample files implementing every schema below live in `./samples/`.

---

## 0. The core question: code-wise or condition-wise?

**Files are code-wise. Search is condition-wise. They are different layers — don't conflate them.**

- `procedure_code` (e.g. `SE020A`) is the **primary key** and the **foreign key** that links
  the package master → the STG → the claim line → TMS. Everything keys off it.
- A **condition is one-to-many** to codes (Cataract → `SE020A` phaco **and** `SE020B` SICS),
  so a condition cannot be the file boundary. Condition is a **search/alias field**, not a key.

So:

| Concern | Unit | File |
|---|---|---|
| Canonical package detail | **code-wise** | `data/hbp/<specialty>.json` (procedures keyed by code) |
| Clinical + documentation protocol | **code-wise** | `data/stg/<procedure_code>.json` |
| **Picking** (the search layer) | **condition-searchable index** | `data/hbp/_index.json` (DERIVED) |

### How the picker works WITHOUT reading all JSON

The picker never scans the detail files. It scans **one thin index**:

```
diagnosis + procedure text
  → filter _index.json by specialty            (cheap categorical narrowing)
  → semantic/LLM match the text against
    procedure_name + package_name + aliases     (within that specialty only)
  → shortlist 1-5 candidate procedure_codes
  → open ONLY those code-wise STG + package files
  → confirm against clinical_thresholds → pick the winning code
```

`_index.json` is **derived** — auto-generated from the specialty shards by a build step.
Never hand-edit it; regenerate it whenever a shard changes (avoids double-maintenance).

---

## 1. File layout (production locations)

```
data/
  schemes/
    pmjay.json              # KB 1 — scheme-wide rules ("the flag config")
    cmchis.json             # KB 5 — state variant
  hbp/
    _index.json             # DERIVED — the picker's only scan target
    ophthalmology.json      # KB 2 — package master, one shard per specialty
    general_surgery.json
    orthopaedics.json
    ...                     # ~27 specialty shards
  stg/
    SE020A.json             # KB 3 — one file per procedure_code
    SE020B.json
    SG017B.json
    ...
  query_taxonomy.json       # KB 4 — scheme-wide query/rejection/audit-flag catalog
```

KB-projection slicers (mirroring the existing `src/v3/kb/projections.py::kb_bundle_for_*`)
will read these and hand LLM-friendly bundles to the agents. Do not let agents read raw files.

---

## 2. KB 1 — Scheme rules  (`data/schemes/pmjay.json`)

**One object per scheme.** Holds every rule that is NOT per-package: coverage, pricing model,
city-tier uplifts, quality incentives, per-day bed rates, multi-procedure & combination rules,
special-case payment ratios (LAMA/death/referral), USP rules, exclusions, SLAs, IT systems,
adjudication roles. This file *is* the `scheme` flag the orchestrator branches on.

- **Source:** authored from the National HBP Guidelines + Claims Adjudication Manual (no external fetch).
- **Sample:** `samples/schemes/pmjay.json`.
- **Required top-level keys:** `scheme_id`, `name`, `version`, `coverage`, `pricing`, `exclusions`,
  `slas`. `usp`, `it_systems`, `adjudication_roles` recommended.
- **Currency:** all amounts INR, integer rupees, key suffix `_inr`.
- **Percentages:** integers, key suffix `_pct`.

---

## 3. KB 2 — HBP package master  (`data/hbp/<specialty>.json`)

**One shard per specialty.** Canonical, code-wise package/procedure detail.

Hierarchy: `specialty → packages[] → procedures[]`. The **procedure** is the billable unit.

### 3.1 Design rule — typed `pricing` + freeform `additional_information`

Not everything is a typed field. The discriminator: **"Does an agent compute or branch on a
number from this?"**

- **Yes → typed, first-class field.** Anything that produces a rupee amount or gates eligibility
  must be deterministic, computable, and auditable — never inferred from a free-text note. This is
  `billing_unit` + the structured `pricing` block.
- **No (context / display / rare quirk) → `additional_information` dict.** A freeform,
  loosely-conventional dict that keeps the base schema stable and absorbs the long tail without
  schema churn. Our agents are LLM-primary, so they read semi-structured values here fine.

Every record also carries **`source_refs`** — a map documenting which document/section each fact
came from, so nothing in `pricing` or `additional_information` is untraceable (no hallucinated rates).

### 3.2 Required fields per procedure

| Field | Type | Notes |
|---|---|---|
| `procedure_code` | string | **Primary key.** e.g. `SE020A` |
| `procedure_name` | string | Full description |
| `billing_unit` | enum | `one_time` \| `per_day` \| `per_session` \| `per_sitting` \| `per_cycle` \| `per_fraction` \| `per_unit` \| `per_dose` \| `per_month` \| `per_visit` |
| `quantity_basis` | enum | multiplier dimension: `none` \| `bed_category` \| `eye` \| `limb` \| `weight_kg` |
| `rates_inr` | object\|null | `{tier3, tier2, tier1}` for flat-rate packages; `null` when price comes from `pricing` (per-day/increment/sequence) |
| `implant` | object\|null | `{name, cost_inr}`; priced separately, not subject to incentives |
| `pricing` | object | **the deterministic money block — see 3.3** |
| `reserved_public_only` | bool | true → private hospital claim auto-rejects |
| `stg_ref` | string | FK into `data/stg/<code>.json` (usually == procedure_code) |
| `additional_information` | object | freeform long-tail — see 3.4 |
| `source_refs` | object | provenance map — see 3.5 |

### 3.3 The `pricing` block (typed — covers every billing shape found in the master)

```jsonc
"pricing": {
  "base_rate_inr": 1500,                 // flat/base amount, or null
  "bed_rates_inr": {                     // for billing_unit=per_day, quantity_basis=bed_category
    "ward": 1800, "hdu": 2700, "icu_no_vent": 3600, "icu_vent": 4500
  },
  "increment": {                         // base + incremental-with-cap (e.g. radiotherapy fractions)
    "extra_unit_rate_inr": 500, "max_extra_units": 18, "cap_inr": 9000
  },
  "quantity_cap": {                      // recurrence cap (e.g. dialysis)
    "max": 3, "per_window": "week", "on_overflow": "separate_preauth"  // | "extra_rate" | "not_payable"
  },
  "sequence": {                          // ordinal/time-gated follow-up packages
    "min_gap_days": 90, "ordinal_rates_inr": { "3": 0, "4": 5000, "5": 2500 }
  }
}
```

Unused sub-keys are `null`. A simple one-time surgical package uses only `base_rate_inr`
(+ top-level `rates_inr` for the tier split). See the worked examples:
- **`one_time`** — `samples/hbp/ophthalmology.json` (Cataract SE020A)
- **`per_session` + `quantity_cap`** — `samples/hbp/general_medicine.json` (Dialysis MG072A)
- **`per_day` + `bed_rates_inr`** — `samples/hbp/general_medicine.json` (AKI MG045A)
- **`per_fraction` + `increment`** — `samples/hbp/radiation_oncology.json` (Radiotherapy MR001D)
- **`per_visit` + `sequence`** — `samples/hbp/cardiology.json` (Cardiology follow-up MC022)

Add-ons: a procedure that is booked on top of a parent uses `additional_information.is_addon_to`
(array of parent codes) — no deduction applies (per scheme rules).

### 3.4 `additional_information` — conventional keys

Freeform, but **reuse these key names across packages** (so cross-package logic and the picker stay
sane — documented convention, not enforced schema):
`alos`, `min_doctor_qualification`, `recurrence_rule`, `doc_cadence`, `special_empanelment`,
`is_addon_to`, `total_formula`, `laterality_note`, `vintage_note`, plus `note` / `*_caveat` for
free remarks.

### 3.5 `source_refs` — provenance (required)

A map from a field (or field group) to the document + section it was taken from. Purpose: every
rate and every `additional_information` fact is auditable back to an NHA source — no unsourced data.
Example:
```jsonc
"source_refs": {
  "pricing.quantity_cap": "Haemodialysis ... _PPD.CPD.pdf §1.4 pre-auth header + §2.2.1 PPD checklist",
  "additional_information.alos": "same STG — ALOS section ('Day care')"
}
```

- **Source: GET the official NHA HBP 2022 master spreadsheet (Excel/CSV)** for `rates_inr`/`pricing`.
  This is the one external fetch — do **not** parse the PDF (tier columns don't separate cleanly;
  see the rate caveats in the radiotherapy sample). `billing_unit`, caps, and `additional_information`
  are enriched from the per-procedure STGs already in the repo.
- **Sample:** `samples/hbp/ophthalmology.json` (+ general_medicine / radiation_oncology / cardiology).

---

## 4. KB 2b — Derived picker index  (`data/hbp/_index.json`)

**DERIVED — auto-generated, never hand-edited.** The only file the picker scans. One thin row
per procedure_code.

Required per row: `procedure_code`, `package_code`, `specialty`, `specialty_code`,
`package_name`, `procedure_name`, **`aliases[]`**, `billing_unit`, `reserved_public_only`,
`base_rate_inr`, `stg_ref`. (`base_rate_inr` is a representative figure for display/ranking only —
authoritative pricing lives in the shard's `pricing` block.)

- `aliases[]` is the search surface — lay terms, abbreviations, synonyms, common diagnosis
  phrasings (e.g. cataract → `["cataract","phaco","IOL","lens opacity","senile cataract"]`).
  This is what makes condition-wise search work over code-wise storage. **Curate aliases well —
  picker quality depends on them.**
- **Sample:** `samples/hbp/_index.json`.

---

## 5. KB 3 — STG store  (`data/stg/<procedure_code>.json`)

**One file per procedure_code.** The heart of the engine — powers Documentation Checker,
Query Predictor, and Medical Justification. Filename = `procedure_code`.

Distilled from the matching pair in
`PM JAY/Standard Treatment Guideline/master/<condition>/`:
- `*_Medco.pdf`   → clinical indications + mandatory documents (Part I)
- `*_PPD.CPD.pdf` → PPD (pre-auth) + CPD (claim) processing checklists (Part II)

Required keys:

| Field | Type | Purpose |
|---|---|---|
| `procedure_code` | string | FK back to the package master |
| `condition`, `procedure_name`, `specialty`, `version` | string | identity |
| `alos` | string | average length of stay (`daycare`, `2 days`, …) |
| `min_doctor_qualification` | string[] | empanelment check |
| `clinical_indications` | string[] | when the procedure is justified |
| `clinical_thresholds` | object[] | machine-checkable `{field, operator, value, note}` — feeds Documentation Checker |
| `mandatory_documents` | object | `{preauth[], claim[]}`, each item `{key, label}` — feeds the doc checklist |
| `checklist` | object | `{ppd_preauth[], cpd_claim[]}`, each `{q, expected}` — the adjudicator questionnaire |
| `common_queries` | string[] | the specific queries this procedure tends to trigger — feeds Query Predictor |

- **Source: already in the repo — a parse/extraction job, no external fetch.** Run an LLM
  extraction pass over each Medco+PPD.CPD pair into this schema. Start with the ~50 highest-volume
  procedures (cataract, hernia, LSCS, cholecystectomy, dialysis, TKR, appendicectomy, …).
- **Sample:** `samples/stg/SE020A.json`.

---

## 6. KB 4 — Query / deduction taxonomy  (`data/query_taxonomy.json`)

**One scheme-wide file.** The master catalog of standardized pre-auth rejection reasons, claim
rejection reasons, and audit red-flags. Per-procedure `common_queries[]` in the STGs reference
these. Powers QueryPredictor scoring + DeviationDetector.

- Each entry: `{code, label}`. Three arrays: `preauth_rejection_reasons`,
  `claim_rejection_reasons`, `audit_red_flags`.
- **Source:** authored from the Claims Adjudication Manual (no external fetch).
- **Sample:** `samples/query_taxonomy.json`.

---

## 7. KB 5 — State variant (CMCHIS)  (`data/schemes/cmchis.json` + state package master)

Phase 2. Same `schemes/*.json` shape as PM-JAY, plus a TN-specific package master
(`data/hbp/cmchis_*.json`) and the per-hospital specialty grid. Key differences vs national:

- TN-specific code namespace (`CMU/CM/DF/TA/DPU`) — does **not** map to national HBP; needs its own master.
- **Grade-tiered pricing** (A1–A6) instead of a single national rate.
- TPA/insurer model.
- **Package eligibility gated per-hospital** by a 47-specialty boolean grid — pre-auth must verify
  the hospital is flagged for the requested specialty; DPU diagnostics routed to permitted centres.

- **Source:** already in the repo (CMCHIS CSVs) — a parse job.
- **Sample:** `samples/schemes/cmchis.json`.

---

## 8. Conventions (apply to every file)

- All money: integer INR, key suffix `_inr`. All percentages: integer, suffix `_pct`.
- `procedure_code` is canonical everywhere; filenames for STGs == the code.
- `_index.json` and any `cmchis_*` masters are **derived** — generated, not hand-edited.
- A leading `_comment` string is allowed in every file for human notes (ignored by loaders).
- Enums lowercase snake_case. Booleans explicit (`reserved_public_only: false`, not omitted).
- **Money is typed, never freeform.** Anything that yields a rupee amount lives in `rates_inr` /
  `pricing`; descriptive context lives in `additional_information` (loosely-conventional keys, §3.4).
- **Every record carries `source_refs`** mapping facts → source document/section. No unsourced rates
  or claims; `null` a field rather than guessing.

---

## 9. Build order (dependency-driven)

1. `schemes/pmjay.json` + `query_taxonomy.json` — **authored now**, unblock orchestrator branch & QueryPredictor.
2. **GET** the HBP 2022 master Excel → generate `hbp/<specialty>.json` shards → generate `hbp/_index.json`.
3. Parse top-50 STGs from the `master/` tree → `stg/<code>.json`.  ← biggest value, enables doc-checker + justification.
4. CMCHIS state variant (parse existing CSVs).

The only thing that requires an external fetch is step 2's **HBP 2022 master spreadsheet**.
Everything else is either authored from documents already read, or parsed from files already in the repo.
