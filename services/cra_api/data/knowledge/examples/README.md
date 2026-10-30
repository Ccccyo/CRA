# CRA RAG 知识样例（隔离的开发/验证资料）

本目录位于现有知识目录之下，但当前内置加载器只读取 `data/knowledge/*.md`，不会自动装载本子目录。样例只用于测试环境中的解析、检索、引用和拒答验证。不要直接复制到生产知识库，也不要将模拟材料称为真实客户、D&B 响应或公司制度。

| 文件 | 身份 | 目标空间 | 使用前提 |
|---|---|---|---|
| `demo_credit_policy_v1.md`、`demo_credit_policy_v2.md` | 完全模拟的历史/现行演练版本；V2 变更为假设 | `policy` | 按评审日只取一个有效版本；测试库中明确替代未版本化 mock 政策 |
| `demo_case_003_evidence.md` | 根据仓库模拟 CASE-003/OCR 抽取整理，仍为模拟案件 | `case_evidence` | 绑定 `CASE-003`，仅本案授权人员可见；不覆盖原 PDF/Excel |
| `demo_case_003_history.md` | 根据仓库模拟历史 CSV 整理，非真实审批 | `case_history` | 仅同 BP 趋势查询；禁止视为现行规则 |
| `oecd_2026-10-16_reference.md` | 真实公开 OECD 原表的有限转述；无内部评分映射 | `public_reference` | 核对官网当前版本、来源页及许可后发布 |
| `rag_eval_cases.jsonl` | 设计用例和金标草案，未运行 | 不入库 | 按 `docs/10-rag-knowledge-design.md` 执行三层评测 |

每个 Markdown 样例开头的 `样例元数据` 是人可读的设计约定；当前上传 API 不会将这些字段解析为系统强制过滤。未经发布门禁和服务端权限/日期过滤，不能把样例当现行权威规则。

评测使用步骤：在隔离测试库导入对应样例并记录实际 `document_id/chunk_id`；把 JSONL 的 `gold_sections` 映射到实际 chunk ID；给每题设置用户、案件和评审日期；先检查资料齐全与授权候选，再检查答案和引用。不要只用 `gold_source` 文件名算通过。

方案全文见 [CRA 知识设计](../../../../../docs/10-rag-knowledge-design.md)。
