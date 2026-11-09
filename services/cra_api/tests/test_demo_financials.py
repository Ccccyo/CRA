import pytest

from cra.demo_financials import build_demo_financials


NORTHSTAR_2024 = {
    "net_sales": 1_250_000_000,
    "cost_of_goods_sold": 987_500_000,
    "operating_income": 150_000_000,
    "interest_expense": 17_016_000,
    "net_income": 93_750_000,
    "depreciation_amortization": 41_250_000,
    "cash": 123_750_000,
    "accounts_receivable": 278_438_000,
    "inventories": 185_625_000,
    "total_current_assets": 618_750_000,
    "intangible_assets": 41_250_000,
    "goodwill": 27_500_000,
    "total_assets": 1_375_000_000,
    "short_term_debt": 119_109_000,
    "accounts_payable": 154_688_000,
    "total_current_liabilities": 386_719_000,
    "long_term_debt": 221_203_000,
    "total_liabilities": 618_750_000,
    "total_equity": 756_250_000,
    "operating_cash_flow": 150_000_000,
    "capex": 48_125_000,
}


def test_demo_financials_are_balanced_and_follow_business_story():
    current = build_demo_financials(NORTHSTAR_2024)

    assert current["net_sales"] == 1_300_000_000
    assert current["total_assets"] == pytest.approx(
        current["total_liabilities"] + current["total_equity"]
    )
    assert (
        current["operating_income"] / current["net_sales"]
        > NORTHSTAR_2024["operating_income"] / NORTHSTAR_2024["net_sales"]
    )
    assert (
        current["accounts_receivable"] / current["net_sales"]
        < NORTHSTAR_2024["accounts_receivable"] / NORTHSTAR_2024["net_sales"]
    )
    assert current["short_term_debt"] < NORTHSTAR_2024["short_term_debt"]
    assert current["long_term_debt"] < NORTHSTAR_2024["long_term_debt"]
    assert current["operating_cash_flow"] > NORTHSTAR_2024["operating_cash_flow"]


def test_demo_financials_reject_incomplete_history():
    incomplete = dict(NORTHSTAR_2024)
    incomplete.pop("total_assets")

    with pytest.raises(ValueError, match="total_assets"):
        build_demo_financials(incomplete)
