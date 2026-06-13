"""
phases/phase5_financial.py — IRIS Pipeline Phase 5
====================================================
Wallet sufficiency check.

Reads  : session.final_package_set, session.patient
Writes : session.estimated_total_inr, session.wallet_sufficient,
         session.copayment_required, session.copayment_gap_inr
Flags  : RATE_NULL_FOR_PERDAY, VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS,
         WALLET_INSUFFICIENT, FINANCIAL_ESTIMATE_APPROXIMATE

Critical Rules applied (from SYSTEM_DESIGN.md):
  #17  Vay Vandana wallet (age ≥70): NHA doesn't specify debit order.
       Show both balances, flag ambiguity (VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS).
       Never silently pick one wallet; always expose both.
  #18  rates_inr can be null for per_day packages (rate comes from bed category
       in pmjay.json). Skip null-rate packages from estimate, flag the count.

TODO: Real pricing formula —
  Final Price = (Procedure_rate + Stratification_uplift)
                × Level_multiplier
                × Tier_multiplier
                × Accreditation_multiplier
                × Geo_multiplier
              + Implant_flat_cost
  Currently only base_rate_inr × deduction_factor is used (phase5 is a stub
  until the multiplier KB is wired in).
"""

from __future__ import annotations

import logging

from config import SENIOR_CITIZEN_AGE
from session import IRISSession

logger = logging.getLogger(__name__)


def run_phase5(session: IRISSession) -> IRISSession:
    """Phase 5 — simplified financial / wallet sufficiency check.

    If session.final_package_set is empty, marks wallet sufficient and returns
    without further work (Phase 4 USP path or all packages blocked).

    Steps:
    1. Estimate total cost across all FinalPackage objects.
       Per-day packages with a null base_rate_inr are excluded from the estimate
       and counted separately for the RATE_NULL_FOR_PERDAY flag.
    2. Determine available wallet.
       For patients aged ≥ SENIOR_CITIZEN_AGE with a Vay Vandana balance, the
       combined total is used as available capacity, but the debit order ambiguity
       is flagged (Critical Rule #17).
    3. Compare total against available wallet. Set copayment fields if over budget.
    4. Always emit FINANCIAL_ESTIMATE_APPROXIMATE (estimate is base-rate only).

    Args:
        session: IRISSession with final_package_set and patient populated.

    Returns:
        session with estimated_total_inr, wallet_sufficient, copayment_required,
        and copayment_gap_inr written.

    Side effects:
        Appends flags to session.flags. No session.errors writes — no I/O in
        this phase.
    """
    logger.info(
        "Phase 5 — start | final_package_set=%d", len(session.final_package_set)
    )

    # Guard: no packages → wallet is trivially sufficient
    if not session.final_package_set:
        logger.info("Phase 5 — final_package_set empty; wallet_sufficient=True, skipping.")
        session.wallet_sufficient = True
        return session

    # ------------------------------------------------------------------
    # Step 1: Estimate total cost
    # ------------------------------------------------------------------
    total: int = 0
    null_rate_count: int = 0

    for fp in session.final_package_set:
        rate = fp.validated.base_rate_inr
        if rate is None:
            null_rate_count += 1
            logger.debug(
                "Phase 5 — %s has null base_rate_inr; excluded from estimate.",
                fp.validated.procedure_code,
            )
            continue
        contribution = int(rate * fp.deduction_factor)
        logger.debug(
            "Phase 5 — %s: rate=₹%d × factor=%.2f → ₹%d",
            fp.validated.procedure_code,
            rate,
            fp.deduction_factor,
            contribution,
        )
        total += contribution

    session.estimated_total_inr = total
    logger.info(
        "Phase 5 — estimated_total_inr=₹%d (null_rate_packages=%d)",
        total,
        null_rate_count,
    )

    if null_rate_count > 0:
        session.add_flag(
            "RATE_NULL_FOR_PERDAY",
            (
                f"{null_rate_count} per-day package(s) excluded from estimate — "
                "rate depends on bed category and LoS."
            ),
            "info",
        )

    # ------------------------------------------------------------------
    # Step 2: Determine available wallet balance
    # ------------------------------------------------------------------
    family_balance: int = session.patient.wallet.family_balance_inr
    vay_vandana: int = session.patient.wallet.vay_vandana_balance_inr or 0

    if session.patient.age >= SENIOR_CITIZEN_AGE and vay_vandana > 0:
        # Critical Rule #17 — flag debit-order ambiguity; never silently pick one
        session.add_flag(
            "VAY_VANDANA_DEBIT_ORDER_AMBIGUOUS",
            (
                f"Patient has dual wallet: family ₹{family_balance:,} + "
                f"Vay Vandana ₹{vay_vandana:,}. "
                "NHA does not specify debit order — verify with SHA before submission."
            ),
            "warning",
        )
        available: int = family_balance + vay_vandana
        logger.info(
            "Phase 5 — senior citizen dual wallet | family=₹%d + vay_vandana=₹%d → available=₹%d",
            family_balance,
            vay_vandana,
            available,
        )
    else:
        available = family_balance
        logger.info(
            "Phase 5 — single wallet | family_balance=₹%d", family_balance
        )

    # ------------------------------------------------------------------
    # Step 3: Sufficiency check
    # ------------------------------------------------------------------
    if total <= available:
        session.wallet_sufficient = True
        logger.info(
            "Phase 5 — wallet sufficient | total=₹%d ≤ available=₹%d",
            total,
            available,
        )
    else:
        gap: int = total - available
        session.wallet_sufficient = False
        session.copayment_required = True
        session.copayment_gap_inr = gap
        logger.warning(
            "Phase 5 — wallet INSUFFICIENT | total=₹%d > available=₹%d | gap=₹%d",
            total,
            available,
            gap,
        )
        session.add_flag(
            "WALLET_INSUFFICIENT",
            (
                f"Estimated cost ₹{total:,} exceeds available wallet ₹{available:,}. "
                f"Gap: ₹{gap:,}."
            ),
            "warning",
        )

    # ------------------------------------------------------------------
    # Step 4: Always emit approximation caveat
    # ------------------------------------------------------------------
    session.add_flag(
        "FINANCIAL_ESTIMATE_APPROXIMATE",
        (
            "Cost estimate uses base rates only. Final rates include tier, level, "
            "accreditation, and geo multipliers."
        ),
        "info",
    )

    logger.info(
        "Phase 5 — complete | wallet_sufficient=%s, copayment_required=%s, "
        "copayment_gap_inr=%s, estimated_total_inr=₹%d",
        session.wallet_sufficient,
        session.copayment_required,
        session.copayment_gap_inr,
        session.estimated_total_inr,
    )
    return session
