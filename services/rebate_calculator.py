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

    @classmethod
    def from_dict(cls, d: dict) -> "Tier":
        return cls(
            threshold=float(d.get("threshold", 0)),
            rate=float(d.get("rate", 0)),
            mode=d.get("mode", "dollar_one"),
        )


@dataclass
class TierResult:
    """Contribution from one tier to the total rebate."""
    tier_number: int
    threshold: float
    rate: float
    mode: str
    applicable_sales: float   # Sales amount this rate was applied to
    rebate_contribution: float


@dataclass
class RebateResult:
    """Full rebate calculation result for one account / period."""
    account_number: str
    period_start: date
    period_end: date
    structure_type: str           # 'tiered' | 'growth'
    structure_name: str

    current_sales: float
    prior_year_sales: float       # 0 for non-growth structures
    growth_amount: float          # current − prior (only meaningful for growth type)

    tier_results: list[TierResult] = field(default_factory=list)
    rebate_amount: float = 0.0
    highest_tier_reached: Optional[int] = None   # 1-based tier index
    override_applied: bool = False
    override_note: str = ""

    @property
    def effective_sales_base(self) -> float:
        """The sales amount that tiers are evaluated against."""
        return self.growth_amount if self.structure_type == "growth" else self.current_sales


# ---------------------------------------------------------------------------
# Core calculation engine
# ---------------------------------------------------------------------------

def _sort_tiers(tiers: list[Tier]) -> list[Tier]:
    return sorted(tiers, key=lambda t: t.threshold)


def calculate_tiered_rebate(
    sales: float,
    tiers: list[Tier],
    structure_type: str = "tiered",
) -> tuple[float, list[TierResult], Optional[int]]:
    """
    Calculate rebate for a given sales amount against a list of tiers.

    Returns:
        (total_rebate, tier_results, highest_tier_index_1_based)
    """
    if not tiers or sales <= 0:
        return 0.0, [], None

    sorted_tiers = _sort_tiers(tiers)
    applicable = [(i, t) for i, t in enumerate(sorted_tiers) if sales >= t.threshold]

    if not applicable:
        return 0.0, [], None

    rebate = 0.0
    tier_results: list[TierResult] = []

    for idx, (tier_idx, tier) in enumerate(applicable):
        # Determine the sales bracket for this tier
        if tier_idx + 1 < len(sorted_tiers):
            next_threshold = sorted_tiers[tier_idx + 1].threshold
            bracket_end = min(sales, next_threshold)
        else:
            bracket_end = sales

        bracket_sales = bracket_end - tier.threshold

        if tier.mode == "dollar_one":
            # This tier applies to ALL sales — override everything calculated so far
            rebate = sales * tier.rate
            tier_results = [
                TierResult(
                    tier_number=tier_idx + 1,
                    threshold=tier.threshold,
                    rate=tier.rate,
                    mode=tier.mode,
                    applicable_sales=sales,
                    rebate_contribution=rebate,
                )
            ]
        else:  # forward_only
            contribution = bracket_sales * tier.rate
            rebate += contribution
            tier_results.append(
                TierResult(
                    tier_number=tier_idx + 1,
                    threshold=tier.threshold,
                    rate=tier.rate,
                    mode=tier.mode,
                    applicable_sales=bracket_sales,
                    rebate_contribution=contribution,
                )
            )

    highest = applicable[-1][0] + 1  # 1-based
    return round(rebate, 2), tier_results, highest


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
    period_start: date,
    period_end: date,
) -> RebateResult:
    """
    Full rebate calculation for one account in a given date range.
    Uses local SQLite cache — no SQL Server connection required.
    """
    tiers = [Tier.from_dict(t) for t in structure.get_tiers()]
    structure_type = structure.structure_type

    # --- Current period sales ---
    with get_session() as session:
        current_sales = get_period_sales(account.account_number, period_start, period_end, session)

        # --- Prior year sales (for growth type and display) ---
        prior_start, prior_end = get_prior_year_period(period_start, period_end)
        prior_sales = get_period_sales(account.account_number, prior_start, prior_end, session)

        # Check override for prior year
        override = (
            session.query(SalesOverride)
            .filter(
                SalesOverride.account_number == account.account_number,
                SalesOverride.period_start <= prior_end,
                SalesOverride.period_end >= prior_start,
            )
            .first()
        )
        override_applied = override is not None
        override_note = f"Override ({override.mode}): ${override.amount:,.2f}" if override else ""

    growth = max(0.0, current_sales - prior_sales)

    # --- Choose what to run tiers against ---
    if structure_type == "growth":
        eval_amount = growth
    else:
        eval_amount = current_sales

    rebate, tier_results, highest = calculate_tiered_rebate(eval_amount, tiers, structure_type)

    return RebateResult(
        account_number=account.account_number,
        period_start=period_start,
        period_end=period_end,
        structure_type=structure_type,
        structure_name=structure.name,
        current_sales=current_sales,
        prior_year_sales=prior_sales,
        growth_amount=growth,
        tier_results=tier_results,
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
    period_start: date,
    period_end: date,
) -> list[dict]:
    """
    Returns per-account summary for the dashboard.
    Each entry:  {account_number, account_name, current_sales, rebate_amount,
                  structure_name, tier_reached}
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
            current_sales = get_period_sales(
                acct.account_number, period_start, period_end, session
            )
            assignment = assignments.get(acct.account_number)
            rebate_amount = 0.0
            structure_name = "—"
            tier_reached = None

            if assignment and assignment.rebate_structure_id in structures:
                struct = structures[assignment.rebate_structure_id]
                structure_name = struct.name
                tiers = [Tier.from_dict(t) for t in struct.get_tiers()]
                if struct.structure_type == "growth":
                    prior_start, prior_end = get_prior_year_period(period_start, period_end)
                    prior_sales = get_period_sales(
                        acct.account_number, prior_start, prior_end, session
                    )
                    eval_amount = max(0.0, current_sales - prior_sales)
                else:
                    eval_amount = current_sales
                rebate_amount, _, tier_reached = calculate_tiered_rebate(
                    eval_amount, tiers, struct.structure_type
                )

            results.append(
                {
                    "account_number": acct.account_number,
                    "account_name": acct.account_name or acct.account_number,
                    "current_sales": current_sales,
                    "rebate_amount": round(rebate_amount, 2),
                    "structure_name": structure_name,
                    "tier_reached": tier_reached,
                }
            )

    results.sort(key=lambda r: r["current_sales"], reverse=True)
    return results
