# CRA 打分规则（MOCK · 权重参考演示模板）

## 总分构成
Overall Score = 70% × Financial Score + 30% × Qualitative Score。
分数越高代表风险越高。Overall Score 映射到 Risk Class 00–05。

## 财务分（Financial Score）
财务分由四组比率加权得出：
- 流动性（LIQ）：利息保障倍数、经营现金流相关比率，权重 41%。
- 杠杆（LVG）：经营现金流对负债、总负债对权益，权重 19%。
- 盈利（PROF）：营业利润率、资产回报率，权重 37%。
- 财报质量（Quality of Financials）：是否审计、是否合并报表，权重 3%。

## 定性分（Qualitative Score）
定性分由五项组成：
- Twelve Months Pay Habits（近 12 个月付款习惯）：权重 10%，数据来自 SAP 应收账款记录。档位：Prompt、1 to 15 days arrear、15 to 20 arrear、Over 20 days arrear、No history。
- Company Type（公司类型）：权重 10%，由分析师判断。档位：Public listed、Private - audited、Private - unaudited、Bankruptcy/Chapter 11。
- Sovereign Rating（国家风险）：权重 20%。档位：grade A or above、grade B、grade C or Not rated。
- Customer Distinction（客户战略重要性）：权重 20%，由商务部门提供。档位：Priority - Above Average Margin、Strategic、Standard、Not classified。选择 Priority - Above Average Margin 时，该项分数固定为 2.0。
- Corporate Credit Risk Rating（外部信用评级）：权重 40%，来自 D&B，按 D&B 评级查表换算，不由分析师主观判断。

## 额度计算方法
- A. % Tangible Net Worth：有形净资产（权益减无形资产和商誉）乘以对应比例。
- C. % Net Worth：净资产乘以对应比例。
- 默认使用方法 A；有形净资产为负数时不得使用方法 A。

## Risk Class 定义
- 00：Minimal
- 01：Low
- 02：Low to Moderate
- 03：Moderate
- 04：High
- 05：Very High
