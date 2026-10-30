# CRA 当前模拟模板字段位置：代码现状卡（非正式财务口径）

> 类型：2026-10-23 从项目源码核对的实现说明；来源：`services/cra_api/src/cra/extract/schema.py`、`services/cra_api/src/cra/excel_mock.py`。仅适用于代码中的 `CRA-2026.1` 模拟 Excel；不是用户真实 CRA 模板或财务政策。代码变更或真实模板接入后必须重建并复核。

## CRA-FIELD-01 · 当前输入结构与来源

抽取器的 `KEY_FIELDS` 共 21 个字段；`ExtractedValue` 保存 `field`、`period_end`、原始 `value`、`unit_multiplier`、`currency`、`page`、`source_text`、模型自评 `confidence` 和 `note`。`normalized=value×unit_multiplier` 由代码计算，缺失值保留 `null`。`statement_scope` 允许 `consolidated`、`parent`、`unknown`；这不等于已经确认可用哪套报表。模拟 Excel 的 `Financial Statements` 中 C 列是本年 AI 待确认值，D/E 是往年值；表头 C5:E5 为期末、C6:E6 为币种。当前代码并未提供正式的多会计准则映射、重述选择或审计门槛。

## CRA-FIELD-02 · 21 字段与模拟 Excel 单元格

下表位置来自 `FIRST_FIELD_ROW=10` 与 `FIELD_ROW` 顺序，列出本年 C 列；往年同一行在 D/E 列。名称是代码显示标签，不是经批准的会计定义。

| 字段代码 | 显示标签 | 本年格 |
|---|---|---|
| net_sales | 营业收入 | C10 |
| cost_of_goods_sold | 营业成本 | C11 |
| operating_income | 营业利润 | C12 |
| interest_expense | 利息费用 | C13 |
| net_income | 净利润 | C14 |
| depreciation_amortization | 折旧与摊销 | C15 |
| cash | 货币资金 | C16 |
| accounts_receivable | 应收账款 | C17 |
| inventories | 存货 | C18 |
| total_current_assets | 流动资产合计 | C19 |
| intangible_assets | 无形资产 | C20 |
| goodwill | 商誉 | C21 |
| total_assets | 资产总计 | C22 |
| short_term_debt | 短期借款 | C23 |
| accounts_payable | 应付账款 | C24 |
| total_current_liabilities | 流动负债合计 | C25 |
| long_term_debt | 长期借款 | C26 |
| total_liabilities | 负债合计 | C27 |
| total_equity | 所有者权益合计 | C28 |
| operating_cash_flow | 经营活动现金流净额 | C29 |
| capex | 资本性支出 | C30 |

## CRA-FIELD-03 · 单位、核验与禁止推断

模拟模板第 31 行为空行，第 32 行是 `Balance Sheet Check`；其公式用 `abs(total_assets-(total_liabilities+total_equity)) ≤ 0.005×abs(total_assets)` 显示 `OK/CHECK`，是演示校验，不是外部准则。代码中的 `goodwill` 说明：与无形资产合并披露时填 `null` 并在 note 说明。利息费用、短期/长期债务等显示标签比提示词定义短，不能只凭标签确定报表科目映射；需原财报页、单位、期间、币种、合并范围和人工确认。提问真实 CRA 模板坐标、口径或审批阈值时，本卡不能给出答案。
