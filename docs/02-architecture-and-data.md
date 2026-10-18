# 系统架构与数据契约

## 1. 架构

```text
Browser
  └─ Next.js Web
       ├─ Landing / Workspace / Case UI
       ├─ Session and role checks
       └─ CRA API client and event stream

CRA Service (Python)
  ├─ Case API
  ├─ Agent state machine
  ├─ Excel parser and writer
  ├─ Financial extraction and validation
  ├─ RAG retrieval and citation validation
  ├─ Deterministic demo scoring
  └─ Audit and Bad Case service

Persistence
  ├─ PostgreSQL for accounts, RBAC, cases, workflow snapshots and RAG catalog
  ├─ SQLite only as a temporary fallback and one-time legacy migration source
  ├─ Local object storage for demo, S3-compatible storage for deployment
  └─ BM25 + Chroma retrieval index for the first release
```

## 2. 责任边界

- Next.js 负责界面、会话、路由、交互和事件展示。
- Python 服务是案件业务真相来源，避免重写已验证的 Excel 和财务逻辑。
- 结构化数字存数据库，文档原文存文件库，向量库仅保存检索索引和元数据。
- 第一版使用单 Worker 及数据库任务表，不引入 Redis 或分布式队列。

## 3. 核心数据对象

| 对象              | 作用                | 关键字段                                                |
| ----------------- | ------------------- | ------------------------------------------------------- |
| cases             | 案件主记录          | case_id, BP, review_cycle, owner, status, current_stage |
| source_files      | 原始文件与版本      | type, path, checksum, version, immutable                |
| excel_imports     | Excel 解析快照      | template_version, parsed_json, issues, imported_at      |
| entity_candidates | 企业候选            | source, external_id, score, matched_fields              |
| entity_binding    | 人工确认的主体      | BP, DUNS/LEI, approver, timestamp                       |
| financial_values  | 21 个财务字段       | period, value, currency, source_page, status, edited_by |
| workflow_runs     | Agent 运行          | state, started_at, stopped_at, current_step             |
| workflow_steps    | 可恢复步骤          | input_hash, attempt, result, validation, status         |
| human_tasks       | 人工关卡            | type, assignee, status, payload, resolved_at            |
| artifacts         | 草稿、Excel、审计包 | type, version, checksum, parent_id                      |
| signoffs          | 内容签署            | type, user, role, content_hash, declaration             |
| agent_events      | 用户可见事件        | event_type, summary, source_refs, created_at            |
| bad_cases         | 质量回流            | error_type, exposure, impact, priority, root_cause, fix |
| knowledge_sources | RAG 文档            | namespace, version, effective dates, authority          |
| knowledge_chunks  | 可引用分块          | source_id, section, page, text, metadata, embedding_id  |

## 4. Excel 契约

### 必要 Sheet

- `Financial Statements`
- `Executive Summary`
- `Header params`

### 预期 Sheet

- `TCURR`
- 隐藏 `Lists`
- 回填后的 `AI Import`

### 年份开关

`Financial Statements!C2:E2` 只接受：

- `Yes - Bring in Prior Year Financials`
- `No - Enter Manually`
- `No Action Needed`

当期手工年份必须为 C 列。至少一个手工年份。标记为 SAP 历史的年份必须有数值。

### 重要单元格

- C3: BP
- C4: Credit Segment
- C5:E5: Period End Date
- C6:E6: Currency
- Executive Summary B5: Requested Credit Limit USD
- Executive Summary: 四个定性项、D&B Rating、DUNS、Company Description、Risk Comments

### 版本和不可变性

- 每次上传计算 SHA-256。
- 原上传文件不覆盖。
- 更正后的重新上传建立新版本并展示差异。
- 影响已签署内容的更改会使对应签署失效。

## 5. 21 个财务字段

`net_sales`, `cost_of_goods_sold`, `operating_income`, `interest_expense`, `net_income`, `depreciation_amortization`, `cash`, `accounts_receivable`, `inventories`, `total_current_assets`, `intangible_assets`, `goodwill`, `total_assets`, `short_term_debt`, `accounts_payable`, `total_current_liabilities`, `long_term_debt`, `total_liabilities`, `total_equity`, `operating_cash_flow`, `capex`.

## 6. 数据来源策略

### 直接迁移

- 原项目案件、模拟企业、模拟 SAP、历史 CRA、规则、Excel 样例和评估集。

### 公开数据

- OECD 国家风险，作为参考，不直接覆盖公司规则。
- SEC EDGAR XBRL，用于美国上市公司行业对标。
- GLEIF LEI，用于企业名称、地址和所有权辅助核验。
- World Bank，用于国家宏观背景。

### Mock

- D&B 企业级 Rating、PAYDEX、Failure Score。
- SAP BP、AR、历史财务、额度和汇率。
- SCASE 审批。
- 公司内部规则与权限。

Mock 数据在 UI、API 和导出文档中统一标记。
