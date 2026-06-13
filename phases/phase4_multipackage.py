"""
phases/phase4_multipackage.py — IRIS Pipeline Phase 4
=======================================================
Multi-package combination rule resolution.

Reads  : session.validated_packages
Writes : session.final_package_set
Flags  : SURGICAL_PERDAY_BLOCKED, PERDAY_MULTIPLE_BLOCKED, STANDALONE_SPLIT,
         ADDON_PARENT_UNKNOWN, ADDON_PARENT_MISSING, DIAGNOSTIC_ADDON_BLOCKED,
         DEDUCTION_APPROXIMATE

Combination rules applied (from SYSTEM_DESIGN.md Critical Rules #5-12):
  5.  Surgical/day_care + per_day  → remove all per_day (SURGICAL_PERDAY_BLOCKED)
  6.  Multiple per_day             → keep only highest match_score (PERDAY_MULTIPLE_BLOCKED)
  7.  Surgical/day_care + fixed_medical → 100% each, allowed
  8.  Surgical + surgical           → 100-50-25 sorted by base_rate_inr desc (DEDUCTION_APPROXIMATE)
  9.  Add-on + primary              → 100% on top of primary, no deduction
  10. Standalone                    → separate pre_auth_group=2
  11. Add-on without parent         → drop (ADDON_PARENT_MISSING / ADDON_PARENT_UNKNOWN)
  12. Diagnostic HD add-on with non-per_day primary → drop (DIAGNOSTIC_ADDON_BLOCKED)
"""

from __future__ import annotations

import logging

from models import FinalPackage, ValidatedPackage
from session import IRISSession

logger = logging.getLogger(__name__)

# Billing types that trigger surgical combination rules
_SURGICAL_TYPES: frozenset[str] = frozenset({"surgical", "day_care"})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_phase4(session: IRISSession) -> IRISSession:
    """Phase 4 — multi-package resolution.

    If session.validated_packages is empty → return session immediately (usp path).

    Steps:
    1. Classify into buckets by billing_type.
    2. Apply combination rules — drop incompatible packages, emit flags.
    3. Isolate standalone packages into pre_auth_group=2.
    4. Attach/validate add-ons — drop orphaned ones.
    5. Assign deduction factors and roles.
    6. Populate session.final_package_set.

    Args:
        session: The current IRISSession object carrying validated_packages from Phase 3.

    Returns:
        session with session.final_package_set populated.

    Side effects:
        Appends flags to session.flags.
        Does NOT append to session.errors (no external I/O in this phase).
    """
    logger.info("Phase 4 — start | validated_packages=%d", len(session.validated_packages))

    if not session.validated_packages:
        logger.info("Phase 4 — no validated packages; USP path — returning immediately.")
        return session

    # Step 1: classify
    buckets = _classify_buckets(session.validated_packages)
    logger.debug(
        "Phase 4 buckets — surgical=%d, fixed_medical=%d, per_day=%d, day_care=%d",
        len(buckets["surgical"]),
        len(buckets["fixed_medical"]),
        len(buckets["per_day"]),
        len(buckets["day_care"]),
    )

    # Step 2: apply combination rules
    allowed = _check_combination_rules(buckets, session)
    logger.info("Phase 4 — after combination rules: %d packages remain", len(allowed))

    # Step 3: separate standalones
    standalone_list, regular_list = _isolate_standalones(allowed, session)

    # Step 4: validate add-ons within the regular list only
    regular_list = _attach_addons(regular_list, session)

    # Step 5 & 6: assign roles/deduction factors → populate final_package_set
    session.final_package_set = _assign_deduction_factors(regular_list, standalone_list)

    # Emit DEDUCTION_APPROXIMATE whenever surgical packages are in the regular set.
    # base_rate_inr is used as a proxy for final price when ordering 100-50-25 — the
    # real rule requires final price after tier/level/accreditation multipliers which
    # are not computed until Phase 5.
    has_surgical_in_regular = any(
        fp.validated.billing_type in _SURGICAL_TYPES
        for fp in session.final_package_set
        if fp.pre_auth_group == 1
    )
    if has_surgical_in_regular:
        session.add_flag(
            "DEDUCTION_APPROXIMATE",
            (
                "100-50-25 deduction order uses base_rate as proxy for final price. "
                "Actual order should use final price after tier/level/accreditation multipliers."
            ),
            "info",
        )

    logger.info(
        "Phase 4 — complete | final_package_set=%d (regular=%d, standalone=%d)",
        len(session.final_package_set),
        len(regular_list),
        len(standalone_list),
    )
    return session


# ---------------------------------------------------------------------------
# Step 1: classify into billing-type buckets
# ---------------------------------------------------------------------------

def _classify_buckets(packages: list[ValidatedPackage]) -> dict:
    """Group validated packages by billing_type.

    day_care is kept in its own bucket here for logging clarity; it is treated
    identically to "surgical" in all combination rule checks.

    Args:
        packages: List of ValidatedPackage objects from Phase 3.

    Returns:
        dict with keys "surgical", "fixed_medical", "per_day", "day_care",
        each mapping to a (possibly empty) list of ValidatedPackage objects.

    Side effects:
        None.
    """
    buckets: dict[str, list[ValidatedPackage]] = {
        "surgical": [],
        "fixed_medical": [],
        "per_day": [],
        "day_care": [],
    }
    for pkg in packages:
        bt = pkg.billing_type
        if bt in buckets:
            buckets[bt].append(pkg)
        else:
            # Unknown billing_type — treat as fixed_medical (safest default)
            logger.warning(
                "Phase 4 — unknown billing_type '%s' for %s; treating as fixed_medical.",
                bt,
                pkg.procedure_code,
            )
            buckets["fixed_medical"].append(pkg)
    return buckets


# ---------------------------------------------------------------------------
# Step 2: combination rules
# ---------------------------------------------------------------------------

def _check_combination_rules(buckets: dict, session: IRISSession) -> list[ValidatedPackage]:
    """Apply PM-JAY combination rules. Returns allowed packages after drops.

    Rules applied (Critical Rules #5-8):
      Rule 5: surgical/day_care + per_day → remove ALL per_day packages.
      Rule 6: multiple per_day packages → keep only the one with highest match_score.
      Rules 7, 8 (fixed_medical + surgical, surgical + surgical) are allowed at
        the package level; deduction factors are handled in _assign_deduction_factors.

    Args:
        buckets: dict from _classify_buckets.
        session: IRISSession — add_flag is called for each emission.

    Returns:
        Combined list of allowed ValidatedPackage objects (may still include add-ons
        and standalones — further filtered by later steps).

    Side effects:
        Appends SURGICAL_PERDAY_BLOCKED and/or PERDAY_MULTIPLE_BLOCKED flags
        to session.flags when packages are dropped.
    """
    surgical_pkgs: list[ValidatedPackage] = buckets["surgical"] + buckets["day_care"]
    per_day_pkgs: list[ValidatedPackage] = buckets["per_day"]
    fixed_medical_pkgs: list[ValidatedPackage] = buckets["fixed_medical"]

    # Rule 5: surgical/day_care + per_day cannot coexist
    if surgical_pkgs and per_day_pkgs:
        dropped_codes = [p.procedure_code for p in per_day_pkgs]
        logger.warning(
            "Phase 4 rule 5 — surgical + per_day conflict; dropping per_day: %s",
            dropped_codes,
        )
        session.add_flag(
            "SURGICAL_PERDAY_BLOCKED",
            (
                "Surgical and per-day medical packages cannot be combined in same pre-auth. "
                f"Per-day package(s) removed: {', '.join(dropped_codes)}."
            ),
            "warning",
        )
        per_day_pkgs = []

    # Rule 6: only one per_day allowed — keep highest match_score
    if len(per_day_pkgs) > 1:
        per_day_pkgs.sort(key=lambda p: p.match_score, reverse=True)
        dropped_codes = [p.procedure_code for p in per_day_pkgs[1:]]
        logger.warning(
            "Phase 4 rule 6 — multiple per_day; dropping lower-scored: %s",
            dropped_codes,
        )
        session.add_flag(
            "PERDAY_MULTIPLE_BLOCKED",
            (
                "Multiple per-day packages not allowed in same pre-auth. "
                "Keeping highest-matched only. "
                f"Dropped: {', '.join(dropped_codes)}."
            ),
            "warning",
        )
        per_day_pkgs = [per_day_pkgs[0]]

    # Combine into single allowed list (rules 7 and 8 require no drops at this stage)
    allowed = surgical_pkgs + fixed_medical_pkgs + per_day_pkgs
    return allowed


# ---------------------------------------------------------------------------
# Step 3: isolate standalones
# ---------------------------------------------------------------------------

def _isolate_standalones(
    packages: list[ValidatedPackage],
    session: IRISSession,
) -> tuple[list[ValidatedPackage], list[ValidatedPackage]]:
    """Separate standalone packages from the main pre-auth group.

    A package is standalone when procedure_label == "standalone". Per Critical
    Rule #10, standalones must be raised as a separate pre-auth (pre_auth_group=2).

    Args:
        packages: Combined allowed list from _check_combination_rules.
        session:  IRISSession — add_flag is called when standalones are present.

    Returns:
        Tuple (standalone_list, regular_list) where both elements are lists of
        ValidatedPackage. One or both lists may be empty.

    Side effects:
        Appends STANDALONE_SPLIT info flag to session.flags when standalones exist
        alongside regular packages.
    """
    standalone_list: list[ValidatedPackage] = []
    regular_list: list[ValidatedPackage] = []

    for pkg in packages:
        if pkg.procedure_label == "standalone":
            standalone_list.append(pkg)
        else:
            regular_list.append(pkg)

    if standalone_list and regular_list:
        logger.info(
            "Phase 4 — %d standalone package(s) split into pre_auth_group=2.",
            len(standalone_list),
        )
        session.add_flag(
            "STANDALONE_SPLIT",
            (
                f"{len(standalone_list)} standalone package(s) must be raised as a "
                "separate pre-auth (pre_auth_group=2)."
            ),
            "info",
        )
    elif standalone_list and not regular_list:
        # All packages are standalone — still valid, no split flag needed
        logger.info(
            "Phase 4 — all %d package(s) are standalone; single pre_auth_group=2 submission.",
            len(standalone_list),
        )

    return standalone_list, regular_list


# ---------------------------------------------------------------------------
# Step 4: validate add-ons
# ---------------------------------------------------------------------------

def _attach_addons(
    packages: list[ValidatedPackage],
    session: IRISSession,
) -> list[ValidatedPackage]:
    """Validate add-on packages have their parent in the allowed set.

    Applies Critical Rules #11 and #12:
      Rule 11: add-on whose is_addon_to parent is absent from the set → drop.
      Rule 12: diagnostic HD add-on (addon_type == "diagnostic_highend") without a
               per_day medical primary in the set → drop.

    Add-ons are validated against the non-add-on subset of packages to avoid
    add-on-to-add-on parent chains (which are not valid in HBP).

    Args:
        packages: Regular (non-standalone) allowed list.
        session:  IRISSession — add_flag called for each dropped add-on.

    Returns:
        New list with orphaned/invalid add-ons removed.

    Side effects:
        Appends ADDON_PARENT_UNKNOWN, ADDON_PARENT_MISSING, or
        DIAGNOSTIC_ADDON_BLOCKED flags to session.flags for each dropped add-on.
    """
    # Build lookup of non-add-on procedure codes (the potential parents)
    parent_codes_in_set: set[str] = {
        p.procedure_code
        for p in packages
        if p.procedure_label != "add_on"
    }

    # Billing types of all non-add-on primaries (for diagnostic add-on rule)
    primary_billing_types: set[str] = {
        p.billing_type
        for p in packages
        if p.procedure_label != "add_on"
    }

    kept: list[ValidatedPackage] = []

    for pkg in packages:
        if pkg.procedure_label != "add_on":
            kept.append(pkg)
            continue

        # --- Rule 11a: is_addon_to is None or empty ---
        if not pkg.is_addon_to:
            logger.warning(
                "Phase 4 rule 11 — %s has no is_addon_to parent list; dropping add-on.",
                pkg.procedure_code,
            )
            session.add_flag(
                "ADDON_PARENT_UNKNOWN",
                f"{pkg.procedure_code} add-on parent unknown — removed.",
                "warning",
            )
            continue

        # --- Rule 11b: none of the declared parents are in the validated set ---
        if not any(parent in parent_codes_in_set for parent in pkg.is_addon_to):
            logger.warning(
                "Phase 4 rule 11 — %s parent(s) %s not in validated set; dropping add-on.",
                pkg.procedure_code,
                pkg.is_addon_to,
            )
            session.add_flag(
                "ADDON_PARENT_MISSING",
                f"{pkg.procedure_code} parent not in validated set — removed.",
                "warning",
            )
            continue

        # --- Rule 12: diagnostic high-end add-on requires per_day primary ---
        if pkg.addon_type == "diagnostic_highend":
            if "per_day" not in primary_billing_types:
                logger.warning(
                    "Phase 4 rule 12 — %s diagnostic add-on requires per_day primary; "
                    "primary billing types are %s — dropping.",
                    pkg.procedure_code,
                    primary_billing_types,
                )
                session.add_flag(
                    "DIAGNOSTIC_ADDON_BLOCKED",
                    (
                        f"{pkg.procedure_code} diagnostic add-on only allowed with "
                        "per-day medical primary."
                    ),
                    "warning",
                )
                continue

        kept.append(pkg)

    dropped_count = len(packages) - len(kept)
    if dropped_count:
        logger.info("Phase 4 — _attach_addons dropped %d add-on(s).", dropped_count)

    return kept


# ---------------------------------------------------------------------------
# Step 5: assign roles and deduction factors → build FinalPackage list
# ---------------------------------------------------------------------------

def _assign_deduction_factors(
    regular_packages: list[ValidatedPackage],
    standalone_packages: list[ValidatedPackage],
) -> list[FinalPackage]:
    """Assign billing roles, deduction factors, and pre_auth_group to each package.

    Implements Critical Rule #8 (surgical 100-50-25) and the DEDUCTION_APPROXIMATE
    flag advisory.

    Deduction factor logic for regular packages (pre_auth_group=1):
      - Add-ons                 → role="addon",      deduction_factor=1.0
      - Per-day                 → role="primary",    deduction_factor=1.0
      - Fixed-medical           → role="primary",    deduction_factor=1.0 (each)
      - Surgical/day_care:
          Index 0 (highest base_rate_inr) → role="primary",   deduction_factor=1.0
          Index 1                          → role="secondary", deduction_factor=0.5
          Index 2+                         → role="tertiary",  deduction_factor=0.25

    For standalone packages (pre_auth_group=2):
      - All → role="standalone", deduction_factor=1.0

    DEDUCTION_APPROXIMATE flag is always emitted when any surgical package is present
    in regular_packages, because base_rate_inr is used as a proxy for final price.

    Args:
        regular_packages:   Non-standalone ValidatedPackage list after add-on filtering.
        standalone_packages: Standalone ValidatedPackage list.

    Returns:
        Combined list of FinalPackage objects (regular first, standalone appended).

    Side effects:
        None — session is intentionally not passed into this helper to keep it
        pure. The DEDUCTION_APPROXIMATE flag is emitted by run_phase4 after
        inspecting the returned list.
    """
    final: list[FinalPackage] = []

    # ---- Regular packages (pre_auth_group=1) ----

    # Separate by billing type role categories
    addons: list[ValidatedPackage] = [
        p for p in regular_packages if p.procedure_label == "add_on"
    ]
    surgicals: list[ValidatedPackage] = [
        p for p in regular_packages
        if p.billing_type in _SURGICAL_TYPES and p.procedure_label != "add_on"
    ]
    fixed_medicals: list[ValidatedPackage] = [
        p for p in regular_packages
        if p.billing_type == "fixed_medical" and p.procedure_label != "add_on"
    ]
    per_days: list[ValidatedPackage] = [
        p for p in regular_packages
        if p.billing_type == "per_day" and p.procedure_label != "add_on"
    ]

    # Surgical: sort by base_rate_inr descending (None treated as 0 for sort stability)
    surgicals.sort(key=lambda p: (p.base_rate_inr or 0), reverse=True)

    _SURGICAL_ROLES = ["primary", "secondary", "tertiary"]
    _SURGICAL_FACTORS = [1.0, 0.5, 0.25]

    for i, pkg in enumerate(surgicals):
        role = _SURGICAL_ROLES[min(i, 2)]
        factor = _SURGICAL_FACTORS[min(i, 2)]
        logger.debug(
            "Phase 4 surgical[%d] — %s → role=%s, factor=%.2f",
            i, pkg.procedure_code, role, factor,
        )
        final.append(FinalPackage(validated=pkg, role=role, deduction_factor=factor, pre_auth_group=1))

    for pkg in fixed_medicals:
        logger.debug("Phase 4 fixed_medical — %s → role=primary, factor=1.0", pkg.procedure_code)
        final.append(FinalPackage(validated=pkg, role="primary", deduction_factor=1.0, pre_auth_group=1))

    for pkg in per_days:
        logger.debug("Phase 4 per_day — %s → role=primary, factor=1.0", pkg.procedure_code)
        final.append(FinalPackage(validated=pkg, role="primary", deduction_factor=1.0, pre_auth_group=1))

    for pkg in addons:
        logger.debug("Phase 4 add_on — %s → role=addon, factor=1.0", pkg.procedure_code)
        final.append(FinalPackage(validated=pkg, role="addon", deduction_factor=1.0, pre_auth_group=1))

    # ---- Standalone packages (pre_auth_group=2) ----
    for pkg in standalone_packages:
        logger.debug("Phase 4 standalone — %s → role=standalone, factor=1.0", pkg.procedure_code)
        final.append(FinalPackage(validated=pkg, role="standalone", deduction_factor=1.0, pre_auth_group=2))

    return final
