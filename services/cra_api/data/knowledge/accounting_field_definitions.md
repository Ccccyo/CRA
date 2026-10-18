# 会计科目口径：CRA 字段 ↔ US-GAAP / IFRS / 中文 / 西文报表（公开资料）

> 来源：根据 FASB US-GAAP 财务报告分类标准（XBRL 元素名）、IFRS 会计准则分类标准中的标准科目名称，
> 以及中国企业会计准则报表格式整理。元素名是公开的标准名称；释义为中文转述。
> 层级：公开资料。录入 CRA 模板时的口径以公司制度为准。
> 用途：同一个科目在不同报表里写法不同，读财报时用这张表把它们对应到同一个 CRA 字段。

## 通用规则
- **合并报表优先**：用 Consolidated（合并）报表，不用母公司报表。
- **单位**：注意表头的 thousands / millions / 千元 / 万元，录入时换算成基本单位。
- **括号是负数**：(1,234) 表示 −1,234；现金流量表里的资本性支出通常写在括号里。
- **报表没有单列的科目**：只抄报表上有的数字，不自己加总；需要计算的（如有形净资产）由程序完成。

## 利润表
| CRA 字段 | US-GAAP 元素 | IFRS 常见名称 | 中文报表 | 西文报表 | 说明 |
|---|---|---|---|---|---|
| net_sales 营业收入 | Revenues；RevenueFromContractWithCustomerExcludingAssessedTax | Revenue | 营业收入 / 营业总收入 | Ventas netas / Ingresos | 有「营业总收入」和「营业收入」两行时取营业收入 |
| cost_of_goods_sold 营业成本 | CostOfGoodsAndServicesSold；CostOfRevenue | Cost of sales | 营业成本 | Costo de ventas | |
| operating_income 营业利润 | OperatingIncomeLoss | Operating profit（IFRS 18 起为必列小计） | 营业利润 | Utilidad de operación | 很多美国公司（如 Dow）不列营业利润，此时填 null，不要用税前利润代替 |
| interest_expense 利息费用 | InterestExpense；InterestExpenseNonoperating | Finance costs | 财务费用中的利息费用 | Gastos financieros / Intereses | 只列「净利息费用」时在备注说明 |
| net_income 净利润 | ProfitLoss（含少数股东）；NetIncomeLoss（归母） | Profit (loss) | 净利润 | Utilidad neta | CRA 用合并净利润（含少数股东损益） |
| depreciation_amortization 折旧与摊销 | DepreciationDepletionAndAmortization | Depreciation and amortisation expense | 固定资产折旧、无形资产摊销（现金流量表补充资料） | Depreciación y amortización | 常在现金流量表里找 |

## 资产负债表
| CRA 字段 | US-GAAP 元素 | IFRS 常见名称 | 中文报表 | 西文报表 | 说明 |
|---|---|---|---|---|---|
| cash 货币资金 | CashAndCashEquivalentsAtCarryingValue | Cash and cash equivalents | 货币资金 | Efectivo y equivalentes | 受限资金单列时不计入 |
| accounts_receivable 应收账款 | AccountsReceivableNetCurrent | Trade receivables | 应收账款 | Cuentas por cobrar / Clientes | 「应收账款及票据」合在一行时在备注说明 |
| inventories 存货 | InventoryNet | Inventories | 存货 | Inventarios | |
| total_current_assets 流动资产合计 | AssetsCurrent | Total current assets | 流动资产合计 | Total activo circulante | |
| intangible_assets 无形资产 | IntangibleAssetsNetExcludingGoodwill | Intangible assets other than goodwill | 无形资产 | Activos intangibles | **不含商誉** |
| goodwill 商誉 | Goodwill | Goodwill | 商誉 | Crédito mercantil | 与无形资产合在一行时，goodwill 填 null 并备注 |
| total_assets 资产总计 | Assets | Total assets | 资产总计 | Total activo | |
| short_term_debt 短期借款 | DebtCurrent；ShortTermBorrowings + LongTermDebtCurrent | Current borrowings | 短期借款 + 一年内到期的非流动负债 | Deuda a corto plazo | |
| accounts_payable 应付账款 | AccountsPayableCurrent | Trade payables | 应付账款 | Proveedores / Cuentas por pagar | 「应付及其他流动负债」合在一行时不能直接当应付账款 |
| total_current_liabilities 流动负债合计 | LiabilitiesCurrent | Total current liabilities | 流动负债合计 | Total pasivo circulante | |
| long_term_debt 长期借款 | LongTermDebtNoncurrent | Non-current borrowings | 长期借款 + 应付债券 | Deuda a largo plazo | |
| total_liabilities 负债合计 | Liabilities | Total liabilities | 负债合计 | Total pasivo | 很多美国公司不列总负债，由程序用总资产 − 权益推算 |
| total_equity 所有者权益合计 | StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest | Total equity | 所有者权益（或股东权益）合计 | Total capital contable | 含少数股东权益 |

## 现金流量表
| CRA 字段 | US-GAAP 元素 | IFRS 常见名称 | 中文报表 | 西文报表 | 说明 |
|---|---|---|---|---|---|
| operating_cash_flow 经营现金流 | NetCashProvidedByUsedInOperatingActivities | Net cash flows from operating activities | 经营活动产生的现金流量净额 | Flujo neto de actividades de operación | |
| capex 资本性支出 | PaymentsToAcquirePropertyPlantAndEquipment | Purchase of property, plant and equipment | 购建固定资产、无形资产和其他长期资产支付的现金 | Adquisición de propiedades, planta y equipo | 报表上为括号（负数），比较时看绝对值 |

## 常见坑
- **年份错位**：报表通常并列两到三年，确认列标题的期末日期，不要把上年数抄成本年。
- **母公司报表**：中国年报同时有合并和母公司报表，两者科目名相同，看表头「合并」二字。
- **重述**：本年报表里的上年数字可能和上年报表不同（重述），SAP 里已有的往年数据以 SAP 为准，差异在意见里说明。
- **IFRS 16 租赁负债**：IFRS 下经营租赁也确认租赁负债，杠杆会比 US-GAAP 口径高，跨准则比较时要说明。
- **有形净资产**：= 所有者权益 − 无形资产 − 商誉，用于额度方法 A；为负数时不能用方法 A。
