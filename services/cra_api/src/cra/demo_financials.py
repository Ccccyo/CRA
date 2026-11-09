"""Deterministic, internally consistent financials for the product demo.

The scenario is deliberately simple and fully labelled as synthetic. It turns
one historical period into a reviewable current period without pretending to
forecast a real customer or using an LLM for accounting arithmetic.
"""

from __future__ import annotations

from typing import Dict, Mapping


DEMO_ASSUMPTIONS = (
    "营业收入同比增长 4.0%",
    "营业成本同比增长 3.5%，毛利率小幅改善",
    "应收账款增长 2.7%、存货增长 2.5%，均低于收入增速",
    "短期借款下降 3.5%、长期借款下降 5.0%",
    "经营现金流增长 10.0%、资本性支出增长 8.0%",
    "其他资产和负债按温和增幅延展，并由所有者权益使资产负债表精确平衡",
)


def _amount(prior: Mapping[str, float], field: str) -> float:
    value = prior.get(field)
    if value is None:
        raise ValueError(f"missing prior financial field: {field}")
    return float(value)


def _scaled(value: float, factor: float) -> float:
    return round(value * factor, 2)


def build_demo_financials(prior: Mapping[str, float]) -> Dict[str, float]:
    """Create one coherent synthetic period from an audited historical period."""
    p = lambda field: _amount(prior, field)

    # Profit and cash flow use distinct, explicit drivers instead of applying a
    # uniform uplift that leaves every margin unchanged.
    result: Dict[str, float] = {
        "net_sales": _scaled(p("net_sales"), 1.04),
        "cost_of_goods_sold": _scaled(p("cost_of_goods_sold"), 1.035),
        "operating_income": _scaled(p("operating_income"), 1.08),
        "interest_expense": _scaled(p("interest_expense"), 0.97),
        "net_income": _scaled(p("net_income"), 1.10),
        "depreciation_amortization": _scaled(
            p("depreciation_amortization"), 1.03
        ),
        "operating_cash_flow": _scaled(p("operating_cash_flow"), 1.10),
        "capex": _scaled(p("capex"), 1.08),
        "cash": _scaled(p("cash"), 1.09),
        "accounts_receivable": _scaled(p("accounts_receivable"), 1.027),
        "inventories": _scaled(p("inventories"), 1.025),
        "intangible_assets": _scaled(p("intangible_assets"), 0.97),
        "goodwill": _scaled(p("goodwill"), 1.00),
        "short_term_debt": _scaled(p("short_term_debt"), 0.965),
        "accounts_payable": _scaled(p("accounts_payable"), 1.035),
        "long_term_debt": _scaled(p("long_term_debt"), 0.95),
    }

    # Preserve the undisclosed "other" buckets from the historical statements
    # and grow them modestly. This keeps totals connected to their components.
    prior_other_current_assets = max(
        p("total_current_assets")
        - p("cash")
        - p("accounts_receivable")
        - p("inventories"),
        0.0,
    )
    result["total_current_assets"] = round(
        result["cash"]
        + result["accounts_receivable"]
        + result["inventories"]
        + _scaled(prior_other_current_assets, 1.03),
        2,
    )

    prior_other_assets = max(
        p("total_assets")
        - p("total_current_assets")
        - p("intangible_assets")
        - p("goodwill"),
        0.0,
    )
    result["total_assets"] = round(
        result["total_current_assets"]
        + result["intangible_assets"]
        + result["goodwill"]
        + _scaled(prior_other_assets, 1.03),
        2,
    )

    prior_other_current_liabilities = max(
        p("total_current_liabilities")
        - p("short_term_debt")
        - p("accounts_payable"),
        0.0,
    )
    result["total_current_liabilities"] = round(
        result["short_term_debt"]
        + result["accounts_payable"]
        + _scaled(prior_other_current_liabilities, 1.03),
        2,
    )

    prior_other_liabilities = max(
        p("total_liabilities")
        - p("total_current_liabilities")
        - p("long_term_debt"),
        0.0,
    )
    result["total_liabilities"] = round(
        result["total_current_liabilities"]
        + result["long_term_debt"]
        + _scaled(prior_other_liabilities, 1.02),
        2,
    )
    result["total_equity"] = round(
        result["total_assets"] - result["total_liabilities"], 2
    )

    validate_demo_financials(result)
    return result


def validate_demo_financials(values: Mapping[str, float]) -> None:
    """Fail fast if future edits break the accounting or operating story."""
    tolerance = max(abs(values["total_assets"]) * 1e-9, 0.01)
    if abs(
        values["total_assets"]
        - values["total_liabilities"]
        - values["total_equity"]
    ) > tolerance:
        raise ValueError("demo balance sheet does not balance")
    if values["cost_of_goods_sold"] >= values["net_sales"]:
        raise ValueError("demo cost of goods sold must be below revenue")
    if values["operating_income"] >= (
        values["net_sales"] - values["cost_of_goods_sold"]
    ):
        raise ValueError("demo operating income exceeds gross profit")
    if values["net_income"] >= values["operating_income"]:
        raise ValueError("demo net income must remain below operating income")
    if values["total_current_assets"] < (
        values["cash"] + values["accounts_receivable"] + values["inventories"]
    ):
        raise ValueError("demo current assets are below disclosed components")
    if values["total_current_liabilities"] < (
        values["short_term_debt"] + values["accounts_payable"]
    ):
        raise ValueError("demo current liabilities are below disclosed components")
    if values["operating_cash_flow"] <= 0 or values["total_equity"] <= 0:
        raise ValueError("demo cash flow and equity must be positive")
