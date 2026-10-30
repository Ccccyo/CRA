# 企业级上传与文件持久化设计

## 1. 目标与结论

本项目的生产上传链路采用以下边界：

```text
浏览器
  ├─ 业务控制请求 ──> Next.js / Vercel ──> Neon PostgreSQL
  └─ 文件二进制直传 ──> Cloudflare R2 私有桶
                                  │
                                  └─> FastAPI Worker 校验、解析、OCR、入库
```

- **Neon PostgreSQL** 保存案件、权限、上传会话、文件元数据、版本、处理任务、解析结果和审计记录。
- **Cloudflare R2 私有桶** 保存 Excel、PDF、图片、知识原件和生成的工作簿等二进制对象。
- **Vercel** 只处理登录、RBAC、签发短期上传凭证和业务状态，不代理文件正文。
- **FastAPI/Worker** 在独立的长期运行环境处理病毒扫描、格式校验、Excel/PDF 解析、OCR 和 Agent 任务。
- 浏览器不能直接连接 PostgreSQL，也不能持有 R2 长期密钥；只能得到限定到单个对象和单个操作的短期预签名 URL。

这套设计解决当前生产部署中的两个关键问题：Vercel Function 请求体上限不适合代理业务文件，以及本地 `runtime/` 文件在无状态部署中不能作为持久化存储。

## 2. 当前事实与设计假设

### 2.1 已确认的当前实现

- 前端通过 `/api/cra` 的 Next.js 代理，以 `multipart/form-data` 上传文件。
- 代理会把请求体完整读入内存，再转发到 FastAPI。
- CRA Excel 原件、财务报告、知识库原件和导出文件保存在 `services/cra_api/runtime`。
- PostgreSQL 已保存 CRA 案件、流程、快照、人工任务、审计事件、RAG 完整解析正文和 chunks。
- 当前限制为知识文档 10 MB、财务报告 20 MB；CRA Excel 仅接受 `.xlsx`。
- Python 服务通过共享密钥接受 Next.js 服务端请求，浏览器不应直接访问 FastAPI。
- 项目已有 R2/S3 兼容配置入口，但 Python 案件附件层尚未接入对象存储。

### 2.2 本设计采用的假设

- 生产对象存储默认使用 Cloudflare R2；接口保持 S3 兼容，以便未来迁移到 AWS S3、MinIO 等实现。
- 本地开发继续支持文件系统后端，不要求开发者连接生产 R2。
- 当前 10–20 MB 文件使用单次预签名 PUT；未来文件超过 100 MB 或需要断点续传时再启用 multipart upload。
- FastAPI 部署在支持后台任务和较长执行时间的容器平台，而不是把 OCR、解析和 Agent 长任务放进 Vercel Function。
- 下文性能指标是拟定的验收 SLO，不是当前系统已测得的数据。

## 3. 数据所有权

| 数据类型                               | 权威存储        | 说明                                 |
| -------------------------------------- | --------------- | ------------------------------------ |
| 用户、角色、权限、会话                 | PostgreSQL      | 继续使用现有 Better Auth 与 RBAC 表  |
| 案件结构化数据、财务指标、流程状态     | PostgreSQL      | 可查询、可事务化、可审计             |
| 文件原件和生成文件                     | R2 私有桶       | 数据库不保存大二进制字段             |
| 文件名、大小、类型、哈希、对象键、版本 | PostgreSQL      | 对象键是存储定位信息，不是公开 URL   |
| 知识文档解析正文与引用 chunks          | PostgreSQL      | 保留当前可检索、可引用的数据模型     |
| OCR/解析中间状态与错误                 | PostgreSQL      | 用户可见错误使用错误码和脱敏消息     |
| 临时转换文件                           | Worker 临时磁盘 | 任务结束立即清理，不作为业务事实     |
| 操作和访问审计                         | PostgreSQL      | 不记录预签名 URL、密钥或原文敏感内容 |

数据库中的一条文件记录和对象存储中的一个对象共同构成业务文件。只有二者校验一致且处理完成时，文件状态才是 `READY`。

## 4. 文件状态机

```text
INITIATED -> UPLOADING -> RECEIVED -> VALIDATING -> PROCESSING -> READY
                    │          │             │             │
                    └──────────┴─────────────┴────> FAILED_RETRYABLE
                               │             └────> FAILED_FINAL
                               ├──────────────────> REJECTED
                               └──────────────────> QUARANTINED

READY -> DELETED
```

| 状态               | 含义                                   | 用户界面                     |
| ------------------ | -------------------------------------- | ---------------------------- |
| `INITIATED`        | 已创建上传会话，尚未确认对象           | 准备上传                     |
| `UPLOADING`        | 客户端正在向 R2 上传                   | 展示上传进度                 |
| `RECEIVED`         | 已通过 R2 HEAD 核对基础属性            | 上传完成，等待校验           |
| `VALIDATING`       | 检测真实格式、大小、恶意文件和压缩炸弹 | 文件校验中                   |
| `PROCESSING`       | Excel/PDF/OCR/知识分块处理中           | 解析中，可刷新状态           |
| `READY`            | 原件和解析结果可供业务使用             | 可查看、引用、下载           |
| `REJECTED`         | 类型、大小或业务格式不符合要求         | 展示可修复原因               |
| `QUARANTINED`      | 安全扫描不通过                         | 不允许下载或解析，通知管理员 |
| `FAILED_RETRYABLE` | 暂时性依赖失败                         | 系统自动重试                 |
| `FAILED_FINAL`     | 达到重试上限或不可恢复                 | 展示追踪编号，允许重新上传   |
| `DELETED`          | 业务软删除，等待或已完成物理清理       | 默认不可见                   |

接口不能在文件到达 `READY` 前返回“处理成功”。上传完成和业务可用是两个不同事件。

## 5. 生产上传流程

### 5.1 初始化上传

浏览器向受登录和 RBAC 保护的 Next.js API 请求：

```http
POST /api/files/init
Idempotency-Key: <uuid>
Content-Type: application/json

{
  "purpose": "financial_report",
  "entity_type": "case",
  "entity_id": "<case-id>",
  "filename": "report.pdf",
  "content_type": "application/pdf",
  "size_bytes": 18340211,
  "sha256": "<optional-client-sha256>"
}
```

服务端按顺序执行：

1. 校验登录、租户、案件访问权限和对应的上传权限。
2. 根据 `purpose` 校验后缀、声明 MIME 和大小上限。
3. 生成不可猜测的对象键，不把原始文件名直接作为路径。
4. 在一个数据库事务中创建 `cra_file_object` 和 `cra_upload_session`。
5. 生成只允许对该对象执行 PUT、约 5 分钟有效的预签名 URL。
6. 返回 `file_id`、对象上传 URL、必须携带的请求头和过期时间。

推荐对象键格式：

```text
prod/<tenant_id>/<purpose>/<yyyy>/<mm>/<uuid>/<version>
```

原始文件名仅作为经过清理的展示元数据保存，不能参与权限判断，也不能决定真实对象路径。

### 5.2 浏览器直传对象存储

浏览器把文件直接 PUT 到 R2，不经过 Next.js 或 FastAPI。前端使用上传进度事件更新界面；失败时可在预签名 URL 未过期前重试相同对象。

当前大小范围使用单次 PUT。若未来启用 multipart upload，上传会话还需记录 R2 upload ID 和 part ETag，并提供 abort 清理流程。

### 5.3 完成上传

对象上传成功后，浏览器调用：

```http
POST /api/files/<file_id>/complete
Idempotency-Key: <uuid>
```

服务端必须执行 R2 HEAD，核对对象键、大小、允许的 Content-Type 和上传会话有效期。不能仅相信浏览器的“上传成功”回调。通过后，在同一数据库事务中：

- 把文件状态更新为 `RECEIVED`；
- 写入实际大小、ETag 和可用的对象元数据；
- 写入 `cra_outbox_event`，请求后台校验和解析；
- 将上传会话标记为 `COMPLETED`。

`complete` 必须幂等。网络重试不能创建第二份文件、第二条绑定或第二套业务结果。

### 5.4 安全校验和业务解析

Worker 领取任务后以流式方式下载对象，并执行：

1. 重新检查对象大小和状态。
2. 根据 magic bytes 检测真实文件类型，不只信任后缀和浏览器 MIME。
3. 对文件执行恶意内容扫描。
4. 对 XLSX/DOCX 等压缩容器检查文件数、展开大小和压缩比，阻止 zip bomb。
5. 以流式 SHA-256 计算内容哈希；不能把 multipart ETag 当作内容哈希。
6. 调用已有的 Excel、PDF、OCR 或知识文档解析器。
7. 用数据库事务写入解析结果、文件绑定、版本和审计记录。
8. 将状态更新为 `READY`；异常按错误类型进入重试、拒绝或隔离状态。

解析出的文本、数字和 OCR 结论仍是“机器提取结果”，需保留原件页码/单元格引用和人工签署关卡，不能因为写入数据库就自动升级为已核验事实。

### 5.5 下载与在线查看

```http
POST /api/files/<file_id>/download-url
```

服务端先按当前用户、租户、案件和文件角色重新做权限检查，再签发只允许 GET 单个对象的短期 URL，或在需要内容脱敏时由受控服务流式返回。每次签发记录下载审计，但不保存完整预签名 URL。

R2 bucket 保持私有，不提供稳定公开 URL。前端不缓存预签名地址为业务字段。

## 6. PostgreSQL 模型

以下表名使用 CRA 前缀，便于与现有模型统一。字段类型和命名可在实现时按现有迁移工具调整。

### 6.1 `cra_file_object`

| 字段                                     | 约束/用途                                                                     |
| ---------------------------------------- | ----------------------------------------------------------------------------- |
| `id`                                     | UUID 主键                                                                     |
| `tenant_id`                              | 必填，所有权限查询必须带租户范围                                              |
| `purpose`                                | `case_excel`、`financial_report`、`knowledge_source`、`generated_workbook` 等 |
| `original_filename`                      | 清理后的展示名称                                                              |
| `storage_provider`                       | `local` 或 `r2`                                                               |
| `bucket` / `object_key`                  | 对象定位；`object_key` 唯一                                                   |
| `version` / `parent_file_id`             | 不可变版本链                                                                  |
| `size_bytes`                             | 服务端实际核对值                                                              |
| `mime_declared` / `mime_detected`        | 分开保存声明和检测结果                                                        |
| `sha256`                                 | 服务端流式计算后的标准哈希                                                    |
| `etag`                                   | 存储层并发/诊断信息，不替代 SHA-256                                           |
| `status`                                 | 文件状态机                                                                    |
| `retention_class`                        | 保留策略分类                                                                  |
| `uploaded_by`                            | 上传用户 ID                                                                   |
| `metadata`                               | 非核心扩展信息 JSONB                                                          |
| `created_at` / `ready_at` / `deleted_at` | 生命周期时间                                                                  |
| `row_version`                            | 乐观并发控制                                                                  |

### 6.2 `cra_upload_session`

保存 `file_id`、创建人、幂等键、预期大小/MIME/哈希、会话状态、过期时间和完成时间。幂等键按用户或租户建立唯一约束。

### 6.3 `cra_file_binding`

将文件绑定到案件、知识文档或其他业务实体：

- `file_id`
- `entity_type` / `entity_id`
- `role`
- `version`
- `is_current`
- `created_at`

同一实体、同一角色、同一版本唯一；同一实体和角色最多只有一个 `is_current=true`。替换文件时创建新版本，不覆盖已签署流程引用的旧原件。

### 6.4 `cra_file_processing_job`

保存任务类型、状态、尝试次数、最大重试次数、可执行时间、锁持有者、锁时间、脱敏错误码和错误消息。任务必须可安全重放，Worker 崩溃后可重新领取。

### 6.5 `cra_outbox_event`

业务状态更新和 outbox 事件在同一 PostgreSQL 事务中提交。后台发布器再把事件转成处理任务，避免出现“数据库已提交但任务消息丢失”。

### 6.6 `cra_file_audit_log`

记录 `actor_id`、动作、文件、关联实体、结果、request ID、时间和必要的客户端信息。审计记录不保存口令、对象存储密钥、预签名 URL 或文件全文。

## 7. 业务接口调整

现有上传接口在迁移期可同时支持 multipart 和 `file_id`，完成切换后只接受 `file_id`：

| 当前接口                            | 目标请求                                                    |
| ----------------------------------- | ----------------------------------------------------------- |
| `POST /cases/preview`               | `{ "file_id": "..." }`                                      |
| `POST /cases`                       | `{ "file_id": "...", "duplicate_mode": "..." }`             |
| `POST /cases/{id}/financial-report` | `{ "file_id": "..." }`                                      |
| `POST /knowledge/documents`         | `{ "file_id": "...", "library_id": "...", "title": "..." }` |

新增通用接口：

- `POST /api/files/init`
- `POST /api/files/{id}/complete`
- `GET /api/files/{id}/status`
- `POST /api/files/{id}/download-url`
- `DELETE /api/files/{id}`

Next.js 仍是外部身份和权限边界。FastAPI 接收内部任务时继续验证服务身份，并携带、记录已验证的 actor、tenant、request ID；不能仅凭调用来自内网就跳过资源级权限。

## 8. 安全控制

- R2 bucket 永久保持私有，访问密钥仅保存在服务端环境变量中。
- 生产和测试使用不同 bucket/凭证；凭证按 bucket 和动作最小授权并定期轮换。
- 预签名 URL 限定到一个随机对象键、一种 HTTP 方法和短有效期，按 bearer token 保护。
- 初始化和完成两个阶段都检查大小；完成阶段以对象存储实际值为准。
- 文件类型采用后缀、声明 MIME、magic bytes 和解析器结果的组合判断。
- 所有文件先校验再进入业务流程；隔离对象不能被普通用户下载。
- 使用 TLS；R2 静态加密不能替代应用层访问控制。
- 每次上传、完成、解析、下载、替换和删除都写入可追踪审计记录。
- 文件内容和 OCR 文本都按不可信输入处理，不能作为系统指令执行，也不能直接拼入 SQL、命令或 HTML。
- 删除采用业务软删除、保留期和异步物理删除；受审计/签署保留策略约束的原件不能被普通删除请求移除。

## 9. 性能与可靠性

### 9.1 性能设计

- 文件直传 R2，避免 Vercel 和 FastAPI 双重转发、内存缓冲和额外带宽。
- Worker 全程流式下载与计算哈希，避免把 PDF/Excel 一次性读入内存。
- 上传完成立即返回 `202 Accepted`，解析异步执行；前端轮询状态，后续可升级为 SSE。
- OCR、向量化和 LLM 与 Web 请求线程隔离，并设置独立并发上限和队列背压。
- PostgreSQL 使用小连接池/Neon pooled URL；不要为每个文件分片创建数据库连接。
- 相同 SHA-256 可用于重复检测，但是否复用必须结合租户、权限、保留策略和业务版本，不能跨租户暴露文件是否存在。

拟定 SLO：

| 指标                   | 目标                  |
| ---------------------- | --------------------- |
| 初始化上传接口 p95     | < 500 ms              |
| 完成确认接口 p95       | < 1 s（不含文件传输） |
| 上传完成后状态首次可见 | < 2 s                 |
| 下载授权接口 p95       | < 500 ms              |
| 上传/完成接口可用性    | >= 99.9%              |

解析时长按文件类型和页数单独建立基线，不把 OCR/LLM 时长算入上传接口延迟。

### 9.2 跨存储一致性

PostgreSQL 和 R2 无法共享数据库事务，因此必须通过状态机、outbox、幂等和定期对账实现最终一致：

- R2 已收到对象但 `complete` 丢失：会话过期后由清理任务删除孤儿对象。
- 数据库已创建会话但未上传对象：过期后标记失败，不进入业务流程。
- `complete` 已提交但 Worker 未收到任务：outbox 发布器重试。
- Worker 中途崩溃：任务锁超时后重领，解析写入使用文件 ID + 任务类型唯一键。
- 数据库标记 `READY` 前再次 HEAD/校验；缺少对象时不得进入 `READY`。
- 每日对账数据库对象键与 R2 inventory/list 结果，异常进入管理员告警，不自动覆盖业务数据。

## 10. 部署拓扑与环境变量

### 10.1 生产拓扑

| 组件             | 建议部署位置                              | 对外暴露          |
| ---------------- | ----------------------------------------- | ----------------- |
| Next.js Web/API  | Vercel                                    | 是，绑定业务域名  |
| PostgreSQL       | Neon                                      | 仅允许服务端连接  |
| 原始文件         | Cloudflare R2 私有桶                      | 仅预签名请求      |
| FastAPI + Worker | Railway、Render、Cloud Run 或等价容器平台 | 仅内部/受保护 API |
| 定时清理/对账    | Worker scheduler                          | 否                |

### 10.2 新增配置

```dotenv
FILE_STORAGE_BACKEND="local" # production: r2
FILE_STORAGE_BUCKET=""
FILE_STORAGE_REGION="auto"
FILE_STORAGE_ENDPOINT="https://<account-id>.r2.cloudflarestorage.com"
FILE_STORAGE_ACCESS_KEY_ID=""
FILE_STORAGE_SECRET_ACCESS_KEY=""
FILE_UPLOAD_URL_TTL_SECONDS="300"
FILE_DOWNLOAD_URL_TTL_SECONDS="300"
FILE_UPLOAD_MAX_KNOWLEDGE_BYTES="10485760"
FILE_UPLOAD_MAX_FINANCIAL_BYTES="20971520"
FILE_UPLOAD_MAX_CASE_EXCEL_BYTES="20971520"
FILE_PROCESSING_MAX_ATTEMPTS="5"
```

密钥不写入 `.env.example` 的真实值，不使用 `NEXT_PUBLIC_` 前缀，也不能注入浏览器 bundle。

R2 bucket 还需要为生产域名配置 CORS。将域名替换为实际 Vercel 域名和自定义域名，只允许 `PUT`、`GET`、`HEAD`，不要使用 `*`：

```json
[
  {
    "AllowedOrigins": [
      "https://your-project.vercel.app",
      "https://your-domain.example"
    ],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["Content-Type"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

当前代码已实现 `local|r2` 存储适配、上传初始化/完成/状态/下载/删除 API、文件元数据与绑定表、浏览器直传、服务端 SHA-256 复核、案件/财报/知识文档绑定及知识原件删除。现有 Excel/PDF/OCR 解析仍沿用 FastAPI 同步业务接口；将解析队列化为独立 Worker 属于后续性能扩展，不影响文件绕过 Vercel 直传 R2。

## 11. 无停机迁移计划

### 阶段 0：基础设施与抽象

1. 新建文件、上传会话、绑定、任务、outbox 和审计表。
2. 实现 `FileStorage` 接口及 `local`、`r2` 两种后端。
3. 保持生产默认行为不变，以 feature flag 控制新链路。

### 阶段 1：新文件写入 R2

1. 服务端上传路径先切换为 R2，数据库写入文件元数据。
2. 读取端同时支持 R2 对象和旧本地路径。
3. 对新文件完成哈希、下载权限和审计验证。

### 阶段 2：浏览器直传

1. 增加 init/complete/status API 和前端上传状态机。
2. Excel、财务报告、知识库依次灰度切换到直传。
3. FastAPI 业务接口改收 `file_id`，停止通过 Vercel 代理二进制。

### 阶段 3：历史文件回填

1. 扫描 `runtime/uploads`、`knowledge-sources` 和受保留策略约束的导出文件。
2. 上传 R2、计算 SHA-256、写入文件记录并绑定原业务实体。
3. 抽样和全量脚本校验大小/哈希/可下载性。
4. 在完整对账和回滚观察期结束前不删除本地文件。

### 阶段 4：生产收口

1. 关闭生产本地存储写入和 multipart 兼容入口。
2. 验证无代码依赖 Vercel 临时文件系统或固定本地路径。
3. 完成恢复演练、保留策略和告警配置后，再按已批准的删除清单清理旧文件。

每个阶段都可通过 feature flag 回退到上一阶段；回退只改变新请求的路径，不覆盖已写入的对象和元数据。

## 12. 验收标准

### 12.1 功能与性能

- 10 MB 知识文档、20 MB PDF 和 20 MB CRA Excel 能从生产域名上传，不经过 Vercel Function 请求体。
- 上传进度可见；上传完成后立即得到文件 ID 和处理状态，不阻塞等待 OCR/Agent。
- 相同 init/complete 请求重复提交不会产生重复业务数据。
- 页面刷新、网络重试和 Worker 重启后任务可继续或安全重跑。
- 直传启用前后，页面加载和案件查询性能不下降；上传路径减少一层代理和一次内存复制。

### 12.2 数据与审计

- 每个 `READY` 文件都能查到唯一对象、服务端 SHA-256、版本、上传人、绑定实体和审计链。
- 知识正文/chunks、财务结构化字段和原件版本之间可相互追溯。
- 文件替换创建不可变新版本，已签署快照继续指向原版本。
- 备份恢复演练能够恢复数据库元数据，并重新验证对象存在和哈希。

### 12.3 权限与安全

- 未登录、无角色、跨租户或无案件权限的用户不能获得上传/下载 URL。
- 预签名 URL 过期、对象键被修改或 HTTP 方法不匹配时访问失败。
- R2 对象不能通过稳定公开 URL 访问。
- 后缀伪装、超限文件、压缩炸弹和恶意文件进入拒绝或隔离状态，不能进入业务流程。
- 日志、错误消息和数据库审计中不出现存储密钥、完整预签名 URL 或敏感原文。

### 12.4 异常恢复

- 模拟 R2 成功但回调丢失，孤儿对象会在保留窗口后被清理。
- 模拟数据库成功但任务发布失败，outbox 能恢复任务。
- 模拟 Worker 在解析中退出，任务可重领且不会重复写入结果。
- 模拟数据库存在记录但对象缺失，状态不会变成 `READY`，并触发告警。
- 模拟删除请求，业务软删除、保留限制、异步物理清理和审计均符合策略。

## 13. 不在本阶段内的事项

- 不把数据库暴露给浏览器，也不允许前台使用 PostgreSQL 连接串。
- 不把 R2 bucket 改成公开读。
- 不在本次设计中决定最终容器供应商；只要求 FastAPI/Worker 具备长期运行、后台任务和受控网络能力。
- 不在未经迁移校验和回滚观察期的情况下删除现有 `runtime` 原件。
- 不用对象存储替代数据库事务、RBAC、业务版本或审计。
