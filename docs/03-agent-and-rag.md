# Agent 与 RAG 设计

## 1. Agent 状态机

| 步骤 | 主要工具 | 校验 | 出口 |
|---|---|---|---|
| intake_excel | Excel parser | 模板、BP、年份、币种、历史数据 | 案件预览或更正任务 |
| resolve_entity | matcher, GLEIF adapter | 阈值、国家、地址、规模 | 人工主体确认 |
| ingest_report | PDF/OCR | 可读页、公司预检、报表期 | 抽取任务 |
| extract_financials | extraction model | schema、单位、页码、原文 | 财务校验 |
| validate_financials | deterministic validators | 平衡、小计、正值、数量级、历史对比 | 人工财务签署 |
| retrieve_context | RAG | 权限、版本、案件、有效日期 | 证据包 |
| draft_risk | DeepSeek structured generation | schema、选项白名单、逐句引用、数字一致性 | 人工风险签署 |
| write_workbook | Excel writer | 工作表、命名区域、目标单元格、重新打开 | 新 Excel 版本 |
| external_score | demo scoring / external wait | 输入快照、可重现性 | 风险等级和建议额度 |
| final_review | signoff service | 偏离理由、内容哈希、角色 | 完成或退回 |

## 2. Agent 循环

```text
读取最新案件版本
→ 计算唯一允许的下一步
→ 创建幂等步骤记录
→ 调用工具
→ 校验工具结果
→ 保存产物和审计事件
→ 通过 / 有限重试 / 转人工 / 阻断
```

- 结构或引用可修复问题最多自动重试 2 次。
- 业务数字冲突不自动重写，直接转人工。
- 同一步使用 input hash 实现幂等。
- 应用重启后从数据库状态继续。

## 3. RAG 知识空间

| Namespace | 内容 | 典型过滤 |
|---|---|---|
| policy | 信用政策、评分规则、字段说明、权限 | effective_at, region, business, version |
| case_evidence | Excel、财报、D&B、人工补充 | tenant_id, case_id, document_version |
| case_history | 历史申报、审批、Bad Case | BP/DUNS, country, industry, decision_type |
| public_reference | OECD、SEC、GLEIF、World Bank、会计定义 | source_date, country, industry, issuer |

## 4. 分块

- Markdown/规则：按标题路径分块，并保留生效日期和版本。
- 财报 PDF：按页和报表区块分块，表格保留行列头和页码。
- Excel：不把整个工作簿盲目嵌入。字段先解析为结构化数据，仅将说明和可引用文本进入索引。
- 历史案件：一次评审一个摘要分块，审批理由和 Bad Case 单独分块。
- 公开结构化数据：保持结构化查询，RAG 只生成可读的来源摘要。

## 5. 检索链路

1. 识别任务类型与需要的 Namespace。
2. 应用 tenant、case、role、version 和 effective date 过滤。
3. BM25 召回精确字段、错误码和标识符。
4. BGE-M3 召回语义相近段落。
5. RRF 融合两路排名。
6. BGE Reranker 对前 20 个候选重排，保留前 5 个。
7. 检查证据是否足以支撑任务。
8. 生成结构化答案。
9. 验证引用 ID、数字、选项和绝对化表述。

## 6. 来源权威规则

- 规则问题：生效中的内部政策优先。
- 客户事实：当前案件原文和 SAP/D&B 交接数据优先。
- 会计数字：结构化字段及原页证据优先，不从自然语言回答反向填数。
- 行业与国家背景：公开来源只做参考。
- 历史决策：只做类比，不自动复用结论。

## 7. Bad Case

记录字段：Case ID、任务类型、用户输入、Agent 输出、人工修改、问题节点、问题原因、流出层级、影响、修改方案和回归测试。

暴露层级：

- L1：被自动校验或规则拦截。
- L2：穿过自动防线，被人工拦截。
- L3：进入回填或模拟执行。
- L4：造成实际损失。

优先级：P0 立即止损；P1 逐例归因；P2 批量统计；P3 双周或月度调优。
