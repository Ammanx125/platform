from datetime import date, timedelta

import pytest

from app.db.models.semantic import CanonicalConcept
from app.db.seed_concepts import ALL_CONCEPTS
from app.schemas.analytics import CustomerBehaviorSummary
from app.services.analytics.customer_behavior import (
    CustomerTransaction,
    analyze_customer_behavior,
    to_summary_dict,
)
from app.services.understanding.matcher import DeterministicMatcher


@pytest.mark.asyncio
async def test_customer_behavior_demo_columns_resolve_to_required_concepts():
    concepts = [
        CanonicalConcept(
            key=key,
            display_name=name,
            domain=domain,
            kind=kind,
            value_type=value_type,
            synonyms=synonyms,
            description=description,
        )
        for key, name, domain, kind, value_type, synonyms, description in ALL_CONCEPTS
    ]
    proposals = await DeterministicMatcher().propose(
        columns=["Customer Name", "Transaction ID", "Transaction Day", "Revenue"],
        concepts=concepts,
    )

    assert {
        proposal.source_column: proposal.canonical_concept_key
        for proposal in proposals
    } == {
        "Customer Name": "Sales.Customer",
        "Transaction ID": "Sales.TransactionId",
        "Transaction Day": "Sales.TransactionDate",
        "Revenue": "Finance.Revenue",
    }


def test_customer_behavior_consolidates_order_lines_and_segments_deterministically():
    as_of = date(2026, 10, 4)

    def order(
        customer: str,
        order_id: str,
        age_days: int,
        amount: float,
        *,
        source: str = "source-a",
    ) -> CustomerTransaction:
        return CustomerTransaction(
            customer_key=customer,
            transaction_id=order_id,
            transaction_date=as_of - timedelta(days=age_days),
            revenue=amount,
            source_id=source,
        )

    transactions = [
        order("Champion", "c-1", 2, 50),
        order("Champion", "c-1", 2, 50),
        *[order("Champion", f"c-{index}", index * 10, 100)
          for index in range(2, 7)],
        order("Growing", "g-1", 20, 200),
        order("Growing", "g-2", 40, 100),
        order("Declining", "d-1", 10, 50),
        order("Declining", "d-2", 40, 200),
        *[order("At Risk", f"r-{index}", age, 100)
          for index, age in enumerate((100, 120, 150), start=1)],
        order("Inactive", "i-1", 200, 100),
        order("Low Value", "l-1", 4, 5),
    ]

    result = analyze_customer_behavior(transactions, as_of_date=as_of)
    profiles = {profile.customer_key: profile for profile in result.customers}

    assert result.customer_count == 6
    assert result.transaction_count == 15
    assert profiles["Champion"].purchase_count == 6
    assert profiles["Champion"].total_revenue == 600
    assert profiles["Champion"].segment == "champions"
    assert profiles["Growing"].segment == "growing"
    assert profiles["Growing"].trend_pct == 100
    assert profiles["Declining"].segment == "declining"
    assert profiles["At Risk"].segment == "at_risk"
    assert profiles["Inactive"].segment == "inactive"
    assert profiles["Low Value"].segment == "low_value"
    assert result.at_risk_count == 1
    assert result.inactive_count == 1
    response = CustomerBehaviorSummary.model_validate(to_summary_dict(result))
    assert response.customers[0].customer_key == "Champion"


def test_declining_trend_takes_precedence_over_high_value_champion():
    as_of = date(2026, 10, 4)
    transactions = [
        CustomerTransaction(
            customer_key="Bluebird Travel",
            transaction_id=f"bluebird-{age}",
            transaction_date=as_of - timedelta(days=age),
            revenue=amount,
            source_id="source-a",
        )
        for age, amount in (
            (10, 500),
            (40, 1500),
            (70, 1200),
            (100, 1000),
            (130, 900),
            (160, 800),
        )
    ]
    transactions.append(CustomerTransaction(
        customer_key="Low-spend customer",
        transaction_id="low-spend-1",
        transaction_date=as_of - timedelta(days=5),
        revenue=10,
        source_id="source-a",
    ))

    result = analyze_customer_behavior(transactions, as_of_date=as_of)
    bluebird = next(
        profile for profile in result.customers
        if profile.customer_key == "Bluebird Travel"
    )

    assert bluebird.high_value
    assert bluebird.trend_pct == pytest.approx(-66.7, abs=0.1)
    assert bluebird.segment == "declining"
