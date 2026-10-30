# CASE-003 财务证据与冲突卡（项目内模拟案件）

> 样例元数据：`source_kind=mock_case_evidence`；`case_id=CASE-003`；`bp=1000601`；`access_scope=case_only`；`status=needs_human_review`；`document_version=example-1`。来源为仓库 `data/cases/CASE-003.json`、`data/samples/extraction_CASE-003.json` 和 `data/history/cra_submissions_mock.csv`；均是演示资料，不是真实客户材料。本卡是检索视图，原始 PDF/Excel 与人工签署状态仍应另存。

## DEMO-EV-01 · 案件对象与边界

模拟案件名称为 Quimica del Pacifico 年度复审，BP `1000601`，信用分段 `0001`，客户国家 `MX`、城市 Guadalajara，申请额度 `800,000 USD`，报表币种 `MXN`，财报期末 `2025-12-31`。案件 JSON 将财报标为扫描件；公司类型提示是“私营公司，管理层报表，未审计”；SAP 模拟输入给出近 12 个月付款档位 `15 to 20 arrear`、平均逾期 17 天。名称、BP、申请额来自模拟案件记录，不代表已完成主体绑定。

## DEMO-EV-02 · 原页证据：2025 资产负债表

模拟 OCR 第 3 页表头为 `ESTADO DE SITUACION FINANCIERA`、`Miles de pesos mexicanos`，列顺序为 `2025 | 2024`。关键原文：`Inventarios  241,000  228,000`；`Activo total  1,320,000  1,280,000`；`Pasivo total  905,000  870,000`；`Capital contable  415,000  410,000`。报表单位是千 MXN，因此仅在页行经核对后，2025 资产、负债和权益的基本单位值分别为 1,320,000,000、905,000,000 和 415,000,000 MXN，三者满足资产 = 负债 + 权益。该算式是演示数据的确定性校验结果，不是信用评级。

## DEMO-EV-03 · 抽取冲突：存货

录制的模型抽取输出把 2025 `inventories` 写为 `214,000 × 1,000 MXN`，并声称原文是 `Inventarios 214,000 228,000`、置信度 `low`。第 3 页模拟 OCR 实际是 `241,000`，两处原文/数值不一致。当前不能选择任一数字作为已签署存货值，不能静默把 214,000 改成 241,000；应由分析师打开原始扫描页确认后，记录更正人、理由、时间和原页引用，再重跑受影响校验与草稿。

## DEMO-EV-04 · 报表范围、其他低置信与限制

录制输出的 `statement_scope=unknown`；仅从当前摘要无法证明报表是合并报表。第 4 页 2025 `Utilidad neta 29,000` 行在录制输出中被标记为低置信，需核原页；第 5 页折旧摊销为 `### ilegible ###`，不得补零或沿用上年值。财务证据签署前须确认合并范围、低置信行与缺失字段。若实际 PDF 与模拟 OCR 不同，以受控原始文件为准；本卡不得反过来覆盖原件。

## DEMO-EV-05 · 风险意见可用与不可用的事实

可作为待核事实的还有第 4 页 2025 `Ventas netas 1,240,000`、`Utilidad de operacion 74,000`、`Gastos por intereses 38,000`，第 5 页经营现金流 `41,000`，均以千 MXN 表示；正式写入前须经财务复核。`74,000 / 38,000 ≈ 1.95` 是基于模拟原页的演示计算，应由确定性代码复算并展示口径。不能据此断言“会违约”或推算未经来源支持的 2026 增长率。D&B 评级和最终额度未在本卡确认为有效事实，须查获授权的本案结果/评分快照。
