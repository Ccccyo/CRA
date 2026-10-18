"""财报抽取的科目清单和输出格式。先做 20 个关键科目，跑通后再扩到 CRA 模板的约 80 个。"""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# 科目代码 → 说明（含中英文常见叫法，写进提示词帮助模型对应）
KEY_FIELDS: Dict[str, str] = {
    "net_sales": "营业收入 / 营业总收入 / Net sales / Revenue",
    "cost_of_goods_sold": "营业成本 / Cost of goods sold / Cost of sales",
    "operating_income": "营业利润 / Operating income (EBIT)",
    "interest_expense": "利息费用 / 财务费用中的利息支出 / Interest expense",
    "net_income": "净利润（归属于母公司前的合并净利润）/ Net income",
    "depreciation_amortization": "折旧与摊销 / Depreciation and amortization",
    "cash": "货币资金 / Cash and cash equivalents",
    "accounts_receivable": "应收账款 / Accounts receivable, net",
    "inventories": "存货 / Inventories",
    "total_current_assets": "流动资产合计 / Total current assets",
    "intangible_assets": "无形资产（不含商誉）/ Intangible assets, net",
    "goodwill": "商誉 / Goodwill（报表上和无形资产合在一行时填 null，并在 note 说明）",
    "total_assets": "资产总计 / Total assets",
    "short_term_debt": "短期借款 + 一年内到期的长期借款 / Short-term debt and current portion of LTD",
    "accounts_payable": "应付账款 / Accounts payable",
    "total_current_liabilities": "流动负债合计 / Total current liabilities",
    "long_term_debt": "长期借款 + 应付债券 / Long-term debt",
    "total_liabilities": "负债合计 / Total liabilities",
    "total_equity": "所有者权益合计（含少数股东权益）/ Total equity",
    "operating_cash_flow": "经营活动产生的现金流量净额 / Net cash from operating activities",
    "capex": "购建固定资产等支付的现金 / Capital expenditures",
}


# 界面和 Excel 上显示的中文短名
FIELD_LABELS: Dict[str, str] = {
    "net_sales": "营业收入", "cost_of_goods_sold": "营业成本", "operating_income": "营业利润",
    "interest_expense": "利息费用", "net_income": "净利润", "depreciation_amortization": "折旧与摊销",
    "cash": "货币资金", "accounts_receivable": "应收账款", "inventories": "存货",
    "total_current_assets": "流动资产合计", "intangible_assets": "无形资产", "goodwill": "商誉", "total_assets": "资产总计",
    "short_term_debt": "短期借款", "accounts_payable": "应付账款", "total_current_liabilities": "流动负债合计",
    "long_term_debt": "长期借款", "total_liabilities": "负债合计", "total_equity": "所有者权益合计",
    "operating_cash_flow": "经营活动现金流净额", "capex": "资本性支出",
}


class ExtractedValue(BaseModel):
    field: str
    period_end: str = Field(description="期末日期 YYYY-MM-DD")
    value: Optional[float] = Field(description="报表上显示的数字；找不到填 null")
    unit_multiplier: float = Field(1.0, description="单位倍数：元=1，千元=1000，百万=1000000")
    currency: Optional[str] = None
    page: Optional[int] = None
    source_text: Optional[str] = Field(None, description="报表上包含该数字的原文行")
    confidence: Literal["high", "medium", "low"] = "medium"
    note: Optional[str] = None

    @property
    def normalized(self) -> Optional[float]:
        return None if self.value is None else self.value * self.unit_multiplier


class ExtractionResult(BaseModel):
    company: Optional[str] = None
    statement_scope: Literal["consolidated", "parent", "unknown"] = "unknown"
    values: List[ExtractedValue]
