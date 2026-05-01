"""
services/rebate_calculator.py
------------------------------
Pure-Python rebate calculation engine.  No UI or DB imports — keeps business
logic isolated and easily testable.

TIER MODES
----------
'dollar_one'   When this tier threshold is crossed, the tier's rate applies
               to ALL sales from dollar one.  A higher dollar_one tier
               overrides all lower tiers entirely (not additive).

'forward_only' When this tier threshold is crossed, the rate applies only to
               incremental sales above this tier's threshold.  Stacks on top
               of whatever rebate was calculated for lower tiers.

STRUCTURE TYPES
---------------
'tiered'  Standard tier structure applied to total sales in the period.
'growth'  Tiers are applied to the GROWTH AMOUNT (current − prior year).
          Only the growth portion earns the rebate; base sales do not.

EXAMPLE — tiered with mixed modes
  Tier 1: $0,      1 %, dollar_one   → if sales ≥ $0   rebate = sales × 1 %
  Tier 2: $50 000, 2 %, dollar_one   → if sales ≥ $50k  rebate = sales × 2 %  (replaces T1)
  Tier 3: $100 000, 0.5%, forward_only → if sales ≥$100k add (sales − 100k) × 0.5%

Sales = $120 000
  T1 dollar_one  : rebate = 120 000 × 1 % = 1 200
  T2 dollar_one  : rebate = 120 000 × 2 % = 2 400  (overrides T1)
  T3 forward_only: rebate += (120 000 − 100 000) × 0.5 % = 100
  TOTAL           = $2 500
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from db.local_db import Account, RebateStructure, SalesCache, SalesOverride, get_session


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Tier:
    threshold: float       # Minimum sales (or growth) to activate this tier
    rate: float            # Decimal rate, e.g. 0.02 for 2 %
    mode: str              # 'dollar_one' | 'forward_only'
    applies_to: str = "sales"   # 'sales' | 'growth' | 'freight'

    @classmethod
    def from_dict(cls, d: dict, structure_type: str = "tiered") -> "Tier":
        # Backward compat: old 'growth' structure type → all tiers apply to growth
        applies_to = d.get("applies_to") or (
            "growth" if structure_type == "growth" else "sales"
        )
        return cls(
            threshold=float(d.get("threshold", 0)),
            rate=float(d.get("rate", 0)),
            mode=d.get("mode", "dollar_one"),
            applies_to=applies_to,
        )


@dataclass
class TierResult:
    """Contribution from one tier to the total rebate."""
    tier_number: int
    threshold: float
    rate: float
    mode: str
    applies_to: str
    applicable_sales: float   # Sales amount this rate was applied to
    rebate_contribution: float


@dataclass
class FreightQualification:
    """Records that an account qualifies for freight rebate at a given tier."""
    tier_number: int
    threshold: float
    freight_rate: float   # % of freight to be returned


@dataclass
class RebateResult:
    """Full rebate calculation result for one account / period."""
    account_number: str
    period_start: date
    period_end: date
    structure_type: str           # 'tiered' | 'growth'
    structure_name: str

    current_sales: float
    prior_year_sales: float
    growth_amount: float

    tier_results: list[TierResult] = field(default_factory=list)
    freight_qualifications: list[FreightQualification] = field(default_factory=list)
    rebate_amount: float = 0.0
    highest_tier_reached: Optional[int] = None
    override_applied: bool = False
    override_note: str = ""

    @property
    def effective_sales_base(self) -> float:
        return self.growth_amount if self.structure_type == "growth" else self.current_sales


# ---------------------------------------------------------------------------
# Core calculation engine
# ---------------------------------------------------------------------------

def _sort_tiers(tiers: list[Tier]) -> list[Tier]:
    return sorted(tiers, key=lambda t: t.threshold)


def calculate_tiered_rebate(
    sales: float,
    growth: float,
    tiers: list[Tier],
    structure_type: str = "tiered",
) -> tuple[float, list[TierResult], list[FreightQualification], Optional[int]]:
    """
    Calculate rebate for a given sales/growth amount against a list of tiers.
    Tiers are split by applies_to: sales, growth, freight.

    Returns:
        (total_rebate, tier_results, freight_qualifications, highest_tier_index_1_based)
    """
    if not tiers:
        return 0.0, [], [], None

    # Separate by type
    sales_tiers   = [t for t in tiers if t.applies_to == "sales"]
    growth_tiers  = [t for t in tiers if t.applies_to == "growth"]
    freight_tiers = [t for t in tiers if t.applies_to == "freight"]

    all_tier_results: list[TierResult] = []
    freight_quals: list[FreightQualification] = []
    total_rebate = 0.0
    highest_applied = None

    def _run_tiers(amount: float, tier_subset: list[Tier], applies_label: str):
        nonlocal total_rebate, highest_applied
        if not tier_subset or amount <= 0:
            return
        sorted_t = sorted(tier_subset, key=lambda t: t.threshold)
        applicable = [(i, t) for i, t in enumerate(sorted_t) if amount >= t.threshold]
        if not applicable:
            return
        running = 0.0
        local_results: list[TierResult] = []
        for tier_idx, tier in applicable:
            if tier_idx + 1 < len(sorted_t):
                next_thresh = sorted_t[tier_idx + 1].threshold
                bracket_end = min(amount, next_thresh)
            else:
                bracket_end = amount
            bracket_sales = bracket_end - tier.threshold
            if tier.mode == "dollar_one":
                running = amount * tier.rate
                local_results = [TierResult(
                    tier_number=tier_idx + 1,
                    threshold=tier.threshold,
                    rate=tier.rate,
                    mode=tier.mode,
                    applies_to=applies_label,
                    applicable_sales=amount,
                    rebate_contribution=running,
                )]
            else:
                contribution = bracket_sales * tier.rate
                running += contribution
                local_results.append(TierResult(
                    tier_number=tier_idx + 1,
                    threshold=tier.threshold,
                    rate=tier.rate,
                    mode=tier.mode,
                    applies_to=applies_label,
                    applicable_sales=bracket_sales,
                    rebate_contribution=contribution,
                ))
            highest_idx = applicable[-1][0] + 1
            if highest_applied is None or highest_idx > highest_applied:
                highest_applied = highest_idx
        total_rebate += round(running, 2)
        all_tier_results.extend(local_results)

    _run_tiers(sales,  sales_tiers,  "sales")
    _run_tiers(growth, growth_tiers, "growth")

    # Freight: just check qualification
    for i, ft in enumerate(sorted(freight_tiers, key=lambda t: t.threshold)):
        if sales >= ft.threshold:
            freight_quals.append(FreightQualification(
                tier_number=i + 1,
                threshold=ft.threshold,
                freight_rate=ft.rate,
            ))

    return round(total_rebate, 2), all_tier_results, freight_quals, highest_applied


# ---------------------------------------------------------------------------
# Account period helpers
# ---------------------------------------------------------------------------

def get_account_period(account: Account, period_end: date) -> tuple[date, date]:
    """
    Return (effective_start, period_end) for an account.
    Effective start = account.start_date if set, else Jan 1 of period_end year.
    """
    if account.start_date:
        effective_start = account.start_date
    else:
        effective_start = date(period_end.year, 1, 1)
    return effective_start, period_end


# ---------------------------------------------------------------------------
# Period-aware calculation
# ---------------------------------------------------------------------------

def get_period_sales(
    account_number: str,
    period_start: date,
    period_end: date,
    session=None,
) -> float:
    """
    Sum sales from the local SalesCache for a given account and date range.
    Checks for SalesOverride records and applies them.
    """
    own_session = session is None
    if own_session:
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            with get_session() as s:
                yield s

        cm = ctx()
    else:
        from contextlib import contextmanager

        @contextmanager
        def cm_existing():
            yield session

        cm = cm_existing()

    with (get_session() if own_session else _null_ctx(session)) as s:
        the_session = s if own_session else session

        # Check for override
        override = (
            the_session.query(SalesOverride)
            .filter(
                SalesOverride.account_number == account_number,
                SalesOverride.period_start <= period_end,
                SalesOverride.period_end >= period_start,
            )
            .first()
        )

        raw_sales = (
            the_session.query(SalesCache)
            .filter(
                SalesCache.account_number == account_number,
                SalesCache.invoice_date >= period_start,
                SalesCache.invoice_date <= period_end,
            )
            .all()
        )
        sql_total = sum(r.total_sales for r in raw_sales)

        if override:
            if override.mode == "replace":
                return override.amount
            else:  # add
                return sql_total + override.amount

        return sql_total


from contextlib import contextmanager


@contextmanager
def _null_ctx(session):
    """No-op context manager that yields the provided session."""
    yield session


def get_prior_year_period(period_start: date, period_end: date) -> tuple[date, date]:
    """Return the equivalent period shifted back exactly one year."""
    try:
        prior_start = period_start.replace(year=period_start.year - 1)
    except ValueError:
        # Feb 29 → Feb 28
        prior_start = period_start - timedelta(days=365)
    try:
        prior_end = period_end.replace(year=period_end.year - 1)
    except ValueError:
        prior_end = period_end - timedelta(days=365)
    return prior_start, prior_end


def calculate_account_rebate(
    account: Account,
    structure: RebateStructure,
    period_end: date,
) -> RebateResult:
    """
    Full rebate calculation for one account.
    Period starts from account.start_date (or Jan 1 if not set).
    Prior year is the same relative window shifted back one year.
    """
    tiers = [Tier.from_dict(t, structure.structure_type) for t in structure.get_tiers()]

    # Compute account-specific period
    effective_start, effective_end = get_account_period(account, period_end)
    prior_start, prior_end = get_prior_year_period(effective_start, effective_end)

    with get_session() as session:
        current_sales = get_period_sales(account.account_number, effective_start, effective_end, session)
        prior_sales   = get_period_sales(account.account_number, prior_start, prior_end, session)

        override = (
            session.query(SalesOverride)
            .filter(
                SalesOverride.account_number == account.account_number,
                SalesOverride.period_start <= prior_end,
                SalesOverride.period_end   >= prior_start,
            )
            .first()
        )
        override_applied = override is not None
        override_note = f"Override ({override.mode}): ${override.amount:,.2f}" if override else ""

    growth = max(0.0, current_sales - prior_sales)

    rebate, tier_results, freight_quals, highest = calculate_tiered_rebate(
        current_sales, growth, tiers, structure.structure_type
    )

    return RebateResult(
        account_number=account.account_number,
        period_start=effective_start,
        period_end=effective_end,
        structure_type=structure.structure_type,
        structure_name=structure.name,
        current_sales=current_sales,
        prior_year_sales=prior_sales,
        growth_amount=growth,
        tier_results=tier_results,
        freight_qualifications=freight_quals,
        rebate_amount=rebate,
        highest_tier_reached=highest,
        override_applied=override_applied,
        override_note=override_note,
    )


# ---------------------------------------------------------------------------
# Monthly breakdown helper
# ---------------------------------------------------------------------------

def get_monthly_sales(
    account_number: str,
    period_start: date,
    period_end: date,
) -> list[dict]:
    """
    Return a list of monthly sales totals for the given account and period.
    Each entry: {"year": int, "month": int, "label": str, "sales": float, "cumulative": float}
    """
    with get_session() as session:
        rows = (
            session.query(SalesCache)
            .filter(
                SalesCache.account_number == account_number,
                SalesCache.invoice_date >= period_start,
                SalesCache.invoice_date <= period_end,
            )
            .order_by(SalesCache.invoice_date)
            .all()
        )

    # Aggregate by year-month
    monthly: dict[tuple[int, int], float] = {}
    for r in rows:
        key = (r.invoice_date.year, r.invoice_date.month)
        monthly[key] = monthly.get(key, 0.0) + r.total_sales

    result = []
    cumulative = 0.0
    import calendar
    for (yr, mo), total in sorted(monthly.items()):
        cumulative += total
        result.append(
            {
                "year": yr,
                "month": mo,
                "label": f"{calendar.month_abbr[mo]} {yr}",
                "sales": round(total, 2),
                "cumulative": round(cumulative, 2),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Dashboard summary helper
# ---------------------------------------------------------------------------

def get_dashboard_summary(
    period_end: date,
) -> list[dict]:
    """
    Returns per-account summary for the dashboard.
    Each account's period starts from its own start_date.
    """
    from db.local_db import AccountRebateAssignment

    with get_session() as session:
        accounts = session.query(Account).filter_by(is_active=True).all()
        assignments = {
            a.account_number: a
            for a in session.query(AccountRebateAssignment).all()
        }
        structures = {
            s.id: s for s in session.query(RebateStructure).all()
        }

        results = []
        for acct in accounts:
            # Use account start_date for the current period
            effective_start, effective_end = get_account_period(acct, period_end)
            prior_start, prior_end = get_prior_year_period(effective_start, effective_end)

            current_sales = get_period_sales(
                acct.account_number, effective_start, effective_end, session
            )
            prior_sales = get_period_sales(
                acct.account_number, prior_start, prior_end, session
            )
            growth = max(0.0, current_sales - prior_sales)

            assignment = assignments.get(acct.account_number)
            rebate_amount = 0.0
            structure_name = "—"
            tier_reached = None

            if assignment and assignment.rebate_structure_id in structures:
                struct = structures[assignment.rebate_structure_id]
                structure_name = struct.name
                tiers = [Tier.from_dict(t, struct.structure_type) for t in struct.get_tiers()]
                rebate_amount, _, _, tier_reached = calculate_tiered_rebate(
                    current_sales, growth, tiers, struct.structure_type
                )

            results.append(
                {
                    "account_number": acct.account_number,
                    "account_name": acct.account_name or acct.account_number,
                    "current_sales": current_sales,
                    "prior_year_sales": prior_sales,
                    "growth_amount": growth,
                    "rebate_amount": round(rebate_amount, 2),
                    "structure_name": structure_name,
                    "tier_reached": tier_reached,
                }
            )

    results.sort(key=lambda r: r["current_sales"], reverse=True)
    return results
