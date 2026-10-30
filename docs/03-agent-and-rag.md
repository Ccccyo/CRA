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
- 自然语言回答中的数字查无出处时，允许携带失败句和数字重新检索、重写 1 次；第二次仍不合格则阻断并转人工。财务抽取与结构化业务数字冲突仍直接转人工，不靠生成模型改数。
- 同一步使用 input hash 实现幂等。
- 应用重启后从数据库状态继续。

## 3. 当前 RAG 边界

当前数据库中有三个用户可见知识空间，以及一个仅供内部检索的案件证据空间。案件证据不出现在公共知识目录，并通过 `case_id` 强制隔离。

| 空间 | 当前内容 | 数据位置 | Analyst | Senior Analyst | Admin |
|---|---|---|---|---|---|
| `policy` | 信用政策、评分规则、字段口径、操作规范 | `cra_knowledge_document/chunk` | 读 | 读 | 读写 |
| `public_reference` | 国家风险、D&B 释义、会计口径、行业参考 | `cra_knowledge_document/chunk` | 读 | 读 | 读写 |
| `case_history` | 历史申报和审批结果 | `cra_knowledge_document/chunk` | 读 | 读 | 读写 |
| `case_evidence`（隐藏） | CRA Excel、财报、D&B 结果、流程状态和人工记录 | `cra_knowledge_document/chunk`，带 `case_id` | 仅当前案件 | 仅当前案件 | 仅当前案件，不参与签署 |

RAG 提供可引用的背景材料，不保存财务计算结果，也不替代确定性校验和人工签署。

## 4. 文档写入全流程

```text
Admin 上传源文件
→ Next.js 校验 admin.access 并转发身份
→ FastAPI 再校验 admin / super_admin
→ 校验格式、10 MB 大小和非空内容
→ 解析为一份权威完整正文
→ 保存原始二进制文件
→ 按格式建立内部 chunks
→ 在一个数据库事务中替换 document + chunks
→ 进程缓存按数据库 revision 失效
→ 向量索引只 upsert 变更 chunks、删除消失 chunks
→ 新索引立即供案件问答和风险意见使用
```

支持格式及当前解析器：

| 格式 | 完整正文生成方式 | 内部分块方式 |
|---|---|---|
| `.md` | UTF-8/GB18030 解码 | Markdown 标题路径 + 段落 |
| `.txt` | UTF-8/GB18030 解码 | 段落 |
| `.pdf` | 文字层；扫描页使用 PaddleOCR 或 GLM-OCR；提取表格并为跨页续表补上上一页表头 | 页码 + 页内段落 |
| `.png/.jpg/.jpeg` | OCR | 页内段落 |
| `.docx` | 解析 Word XML 中的段落、标题和表格 | 标题路径 + 段落 |
| `.doc` | macOS `textutil` 或 `antiword` | 段落 |

上传成功后：

- 原文件写入 `services/cra_api/runtime/knowledge-sources`。
- 完整解析正文写入 `cra_knowledge_document.source_content`。
- 文件名、MIME、解析器、存储键和维护人写入文档元数据。
- 检索分段写入 `cra_knowledge_chunk.content`。
- 编辑完整正文时只重建该文档的数据库 chunks；Chroma 依据 chunk 内容哈希只更新变更项，不重算全库向量。删除文档时同时删除其 chunks、向量项和原文件。
- Markdown/TXT 默认每块约 800 字符、重叠 100；PDF 默认每块约 1000 字符、重叠 120。用户阅读始终看到完整源文档，不看到这些内部边界。
- 写入失败会回滚数据库操作并清理刚保存的文件，不保留半成品。

## 5. 内置知识初始化

仓库内置 Markdown 和历史 CSV 在启动迁移时进入同一套 document/chunk 模型：

1. 根据受控源引用生成 `builtin-<hash>` 形式的稳定、不含路径字符的文档 ID。
2. 将原始 Markdown/CSV 全文写入 `source_content`，文件名写入 `source_filename`。
3. 内部检索仍使用标题分段或“一次历史评审一段”的 chunks。
4. `knowledge-seed-v2` 会删除旧的 `公开资料/文件名` 路径型 ID，避免 URL 将 `/` 误判为路由层级。

内置文档只迁移一次。管理员后续在工作台修改正文后，重启不会用仓库文件静默覆盖人工版本。

## 6. 阅读全流程

```text
用户打开知识空间
→ Next.js 校验 cra.knowledge.read
→ FastAPI 按角色过滤三个知识空间
→ 目录只返回文档元数据，不返回 chunks
→ 用户选择文档
→ 详情接口返回完整 source_content
→ PDF/图片有原始文件时直接预览；其他格式显示完整解析正文
```

- Analyst 与 Senior Analyst 能阅读三个知识空间的完整源文档。
- 侧边栏阅读不展示 chunk 数组、chunk 文本或切分边界。
- “打开源文件”优先返回上传时保存的原始二进制；内置文档返回完整源文本。
- Admin 使用同一阅读界面，并额外拥有上传、编辑和删除入口。

## 7. 当前检索与生成全流程

在线链路默认开启 BM25 + BGE-M3 向量召回，经 RRF 合并后使用 BGE Reranker 重排。开关为 `RAG_USE_VECTOR`、`RAG_USE_RERANK`，默认均为 `true`。模型由进程级缓存只加载一次；索引也按知识库 revision 在进程内复用。未安装 `requirements-ml.txt` 时会记录警告并降级为 BM25，`/health.rag` 会显示实际是否启用，避免静默误判。

权限不再靠“每个请求临时重建一份索引”实现：所有 chunks 共用一份索引，`library_id`、`case_id` 作为标签，关键词和向量两路都在候选阶段过滤。相同知识 revision、问题与权限范围的检索结果使用进程内 LRU 缓存复用；文档变化后因检索器实例更新而自动失效。

### 风险意见

1. 根据案件国家、付款表现、公司类型、D&B 评级和有形净资产生成固定检索问题。
2. 在当前角色允许的三个知识空间内进行混合检索。
3. 历史案件额外限制为当前 BP，避免引用其他客户的历史决定。
4. 每个问题先取第一条，再补第二条，总计最多 8 个 RAG chunks。
5. 将结构化案件事实放在前面，RAG chunks 放在后面，统一编号为 `[S1]...`。
6. 模型只依据编号材料生成结构化风险意见和四项定性评分。
7. 代码校验引用 ID、数字来源、选项白名单和禁止的绝对化措辞。
8. 校验失败或模型不可用时，改用带引用的确定性草稿，并要求分析师复核。

### 案件问答

1. 案件概况、财务证据、D&B 结果、流程状态和人工记录按 `case_id` 写入隐藏的 `case_evidence`；内容哈希不变时不重复写入。
2. 用户问题分别检索本案证据和当前角色可读的共享知识，只取相关块，不再把整个案件对象和全部备注塞进模型上下文。
3. 两类命中统一编号后发送给模型，并共用逐句引用和数字出处规则。
4. 第一次校验失败时，用失败句和无出处数字重新检索并重写一次。
5. 第二次仍失败时不返回原答案，创建 `qa_review` 人工任务和 L1/P1 badcase；无材料时明确拒答。
6. 前端展示本次回答使用的引用来源，但知识库阅读界面仍只展示完整源文件。

## 8. 权限与信任边界

- 浏览器只访问 Next.js `/api/cra` 代理；FastAPI 通过共享密钥拒绝直接业务请求。
- Next.js 使用数据库 RBAC 校验 `cra.knowledge.read` 或 `admin.access`。
- FastAPI 根据转发角色再次限制可读知识空间，并对写操作再次要求 `admin/super_admin`。
- 检索器在构建候选集之前按角色过滤 chunks，禁止先检索再在回答阶段隐藏。
- 历史案例只是经验参考；与内部制度冲突时必须以制度为准。

## 9. 来源权威规则

- 规则问题：生效中的内部政策优先。
- 客户事实：当前案件原文和 SAP/D&B 交接数据优先。
- 会计数字：结构化字段及原页证据优先，不从自然语言回答反向填数。
- 行业与国家背景：公开来源只做参考。
- 历史决策：只做类比，不自动复用结论。

## 10. 当前缺口与演进顺序

1. 当前没有 tenant、地区、生效日期和政策版本字段级过滤；生产化前需加入文档元数据和检索前过滤。
2. 部署环境必须安装 `requirements-ml.txt` 并预热/持久化 BGE 模型；未安装时健康检查会显示语义检索未激活。
3. 原文件在本地运行时目录；多实例部署前迁移到 S3/R2，并在数据库保存对象键、校验和与版本。
4. 上传文件已有解析失败保护，但还需要病毒扫描、对象存储生命周期和文档版本审计。
5. 当前 22 题离线集在本次切块调整后为 `Hit@5=1.0`、`Recall@20=1.0`，仍只适合作为回归种子，不能作为生产准确率声明。

## 11. RAG 故障定位顺序

1. 目录不可见：检查账号角色与 `cra.knowledge.read`。
2. 目录可见但详情 404：检查文档 ID 是否为 `builtin-*` 或 `rag-*`，以及数据库文档是否存在。
3. 正文为空：检查 `source_content` 和解析器元数据；不应依赖 chunks 拼接作为长期数据源。
4. 原文件打不开：检查 `source_storage_name` 指向的运行时文件；内置文档应回退到完整源文本。
5. 检索不到：检查 `/health.rag`、文档 chunks 数量、`library_id/case_id` 过滤、检索词和 citation 的知识空间前缀。
6. 回答无引用或数字不一致：检查两次校验报告、`qa_review` 人工任务与 badcase；风险意见应回退到确定性草稿，案件问答应阻断。

## 12. Bad Case

记录字段：Case ID、任务类型、用户输入、Agent 输出、人工修改、问题节点、问题原因、流出层级、影响、修改方案和回归测试。

暴露层级：

- L1：被自动校验或规则拦截。
- L2：穿过自动防线，被人工拦截。
- L3：进入回填或模拟执行。
- L4：造成实际损失。

优先级：P0 立即止损；P1 逐例归因；P2 批量统计；P3 双周或月度调优。
